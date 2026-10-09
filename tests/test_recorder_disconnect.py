import io
import sys
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from turbo_whisper import recorder as recorder_module
from turbo_whisper.config import Config
from turbo_whisper.recorder import AudioRecorder


class FakeStream:
    def __init__(self, reads, block_cleanup=False):
        self._reads = iter(reads)
        self._cleanup_release = threading.Event()
        self._block_cleanup = block_cleanup

    def read(self, chunk_size, exception_on_overflow=False):
        item = next(self._reads)
        if isinstance(item, BaseException):
            raise item
        return item

    def stop_stream(self):
        if self._block_cleanup:
            self._cleanup_release.wait()

    def close(self):
        if self._block_cleanup:
            self._cleanup_release.wait()

    def release_cleanup(self):
        self._cleanup_release.set()


class BlockingReadStream:
    def __init__(self):
        self.read_started = threading.Event()
        self.read_release = threading.Event()

    def read(self, chunk_size, exception_on_overflow=False):
        self.read_started.set()
        self.read_release.wait()
        return b"\x00\x00" * chunk_size

    def stop_stream(self):
        pass

    def close(self):
        pass


class FakePyAudio:
    def __init__(self, stream):
        self.stream = stream

    def open(self, **kwargs):
        return self.stream

    def get_sample_size(self, sample_format):
        return 2

    def terminate(self):
        pass


def test_stalled_microphone_read_reports_disconnect(monkeypatch):
    config = Config()
    stream = BlockingReadStream()
    monkeypatch.setattr(recorder_module.pyaudio, "PyAudio", lambda: FakePyAudio(stream))
    monkeypatch.setattr(recorder_module, "MIC_STALL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(recorder_module, "WATCHDOG_POLL_INTERVAL_SECONDS", 0.01)
    errors = []

    recorder = AudioRecorder(config)
    recorder.start(on_error=errors.append)

    assert stream.read_started.wait(timeout=0.5)
    deadline = time.monotonic() + 0.5
    while not errors and time.monotonic() < deadline:
        time.sleep(0.01)

    assert errors == ["Microphone stopped responding"]
    assert recorder.is_recording is False

    stream.read_release.set()
    recorder._record_thread.join(timeout=0.5)


def test_microphone_error_reports_disconnect_and_preserves_audio(monkeypatch):
    config = Config(sample_rate=2, chunk_size=2)
    frame = b"\x01\x00\x01\x00"
    stream = FakeStream([frame, OSError("microphone disconnected")])
    monkeypatch.setattr(recorder_module.pyaudio, "PyAudio", lambda: FakePyAudio(stream))
    errors = []

    recorder = AudioRecorder(config)
    recorder.start(
        streaming_mode=True,
        chunk_interval_seconds=2,
        on_error=errors.append,
    )

    deadline = time.monotonic() + 1
    while recorder.is_recording and time.monotonic() < deadline:
        time.sleep(0.01)

    assert errors == ["microphone disconnected"]
    assert recorder.is_recording is False

    remaining_chunk = recorder.flush_remaining_chunk()
    assert remaining_chunk is not None

    with wave.open(io.BytesIO(remaining_chunk), "rb") as wav:
        assert wav.readframes(wav.getnframes()) == frame


def test_stop_returns_captured_audio_when_device_cleanup_blocks(monkeypatch):
    config = Config()
    stream = FakeStream([], block_cleanup=True)
    monkeypatch.setattr(recorder_module.pyaudio, "PyAudio", lambda: FakePyAudio(stream))
    monkeypatch.setattr(recorder_module, "STREAM_STOP_TIMEOUT_SECONDS", 0.05)

    recorder = AudioRecorder(config)
    recorder.is_recording = True
    recorder.stream = stream
    recorder.frames = [b"\x01\x00\x01\x00"]

    result = []

    def stop_recorder():
        result.append(recorder.stop())

    stop_thread = threading.Thread(target=stop_recorder, daemon=True)
    stop_thread.start()
    stop_thread.join(timeout=0.5)

    assert not stop_thread.is_alive(), "AudioRecorder.stop() blocked on disconnected device"
    assert result and len(result[0]) > 44
    stream.release_cleanup()
