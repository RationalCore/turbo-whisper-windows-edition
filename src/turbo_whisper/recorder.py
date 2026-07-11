"""Audio recording functionality."""

import io
import logging
import os
import subprocess
import sys
import threading
import wave
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np
import pyaudio

from turbo_whisper.config import Config


def _setup_logger() -> logging.Logger:
    """Set up file logger for recorder module."""
    logger = logging.getLogger("turbo_whisper.recorder")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        if sys.platform == "win32":
            log_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            log_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        log_dir = log_dir / "turbo-whisper"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "turbo-whisper.log"

        handler = logging.FileHandler(str(log_path), encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


logger = _setup_logger()


def get_pipewire_sources() -> list[dict]:
    """Get PipeWire audio input sources with friendly names."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sources"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []

        sources = []
        current = {}

        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("Source #"):
                if current and current.get("is_input"):
                    sources.append(current)
                current = {"id": line.split("#")[1]}
            elif line.startswith("Name:"):
                current["name"] = line.split(":", 1)[1].strip()
            elif line.startswith("Description:"):
                desc = line.split(":", 1)[1].strip()
                current["description"] = desc
                current["is_input"] = (
                    "alsa_input" in current.get("name", "") and "Monitor" not in desc
                )

        if current and current.get("is_input"):
            sources.append(current)

        return sources
    except Exception:
        return []


class AudioRecorder:
    """Records audio from microphone with level monitoring."""

    def __init__(self, config: Config):
        self.config = config
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.frames: list[bytes] = []
        self.is_recording = False
        self.level_callback = None
        self._actual_sample_rate = config.sample_rate
        self.waveform_buffer = deque(maxlen=100)
        self._record_thread = None

        # Streaming mode state
        self._streaming_mode = False
        self._on_chunk_ready: Callable[[bytes], None] | None = None
        self._on_auto_stop: Callable[[], None] | None = None
        self._chunk_frames: list[bytes] = []
        self._overlap_frames: deque[bytes] = deque(maxlen=32)  # ~1s at 16kHz/chunk_size=1024
        self._chunk_interval_frames = 0  # frames between chunk emissions
        self._frames_since_last_chunk = 0
        self._peak_level = 0.0  # adaptive peak for auto-stop speech detection
        self._last_speech_frame = 0  # frame counter for auto-stop

    def get_input_devices(self) -> list[dict]:
        """Get list of available input devices."""
        # Try PipeWire first (Linux)
        if sys.platform.startswith("linux"):
            pw_sources = get_pipewire_sources()
            if pw_sources:
                return [
                    {
                        "index": src["id"],
                        "name": src["description"],
                        "pipewire_name": src["name"],
                        "channels": 2,
                        "sample_rate": 48000,
                    }
                    for src in pw_sources
                ]

        # Fallback to PyAudio
        devices = []
        for i in range(self.audio.get_device_count()):
            try:
                info = self.audio.get_device_info_by_index(i)
                if info["maxInputChannels"] > 0:
                    devices.append(
                        {
                            "index": i,
                            "name": info["name"],
                            "channels": info["maxInputChannels"],
                            "sample_rate": int(info["defaultSampleRate"]),
                        }
                    )
            except Exception:
                pass
        return devices

    def start(
        self,
        level_callback=None,
        streaming_mode: bool = False,
        on_chunk_ready: Callable[[bytes], None] | None = None,
        on_auto_stop: Callable[[], None] | None = None,
        chunk_interval_seconds: float = 4.0,
    ) -> None:
        """Start recording audio.

        Args:
            level_callback: Called with (level, waveform_buffer) for UI updates
            streaming_mode: If True, emit chunks at fixed time intervals
            on_chunk_ready: Called with WAV bytes when a chunk is ready
            on_auto_stop: Called when auto-stop timeout reached (no speech detected)
            chunk_interval_seconds: Duration of each chunk in streaming mode
        """
        if self.is_recording:
            return

        self.level_callback = level_callback
        self.frames = []
        self.is_recording = True

        # Streaming mode state
        self._streaming_mode = streaming_mode
        self._on_chunk_ready = on_chunk_ready
        self._on_auto_stop = on_auto_stop
        self._chunk_frames = []
        self._overlap_frames = deque(maxlen=32)  # ~1s overlap
        self._frames_since_last_chunk = 0
        self._peak_level = 0.0
        self._last_speech_frame = 0

        # Calculate chunk interval in frames
        if streaming_mode and chunk_interval_seconds > 0:
            # Each frame is chunk_size / sample_rate seconds
            frame_duration = self.config.chunk_size / self.config.sample_rate
            self._chunk_interval_frames = max(1, int(chunk_interval_seconds / frame_duration))
            logger.info(f"Time-based streaming: {chunk_interval_seconds}s chunks, "
                      f"{self._chunk_interval_frames} frames each, "
                      f"~{self._chunk_interval_frames * frame_duration:.1f}s effective")
        else:
            self._chunk_interval_frames = 0

        # Use simple defaults - let PyAudio/PipeWire handle device routing
        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=self.config.channels,
            rate=self.config.sample_rate,
            input=True,
            frames_per_buffer=self.config.chunk_size,
        )

        self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
        self._record_thread.start()

    def _record_loop(self) -> None:
        """Recording loop - handles both batch and streaming modes."""
        frame_count = 0
        while self.is_recording and self.stream:
            try:
                data = self.stream.read(self.config.chunk_size, exception_on_overflow=False)
                self.frames.append(data)
                frame_count += 1

                # Calculate energy level (with mic gain applied)
                audio_data = np.frombuffer(data, dtype=np.int16)
                gain_factor = self.config.mic_gain / 100.0
                level = np.abs(audio_data).mean() / 32768.0 * gain_factor

                if self._streaming_mode and self._chunk_interval_frames > 0:
                    # Time-based streaming: accumulate frames and emit at intervals
                    self._chunk_frames.append(data)

                    # Track peak level for adaptive speech detection (decays slowly)
                    self._peak_level = max(level, self._peak_level * 0.999)

                    # Adaptive threshold: 5% of peak, minimum 0.0001
                    speech_threshold = max(0.0001, self._peak_level * 0.05)

                    # Check for speech for auto-stop
                    if level > speech_threshold:
                        self._last_speech_frame = frame_count

                    # Emit chunk at fixed intervals
                    if frame_count % self._chunk_interval_frames == 0 and len(self._chunk_frames) > 0:
                        chunk_audio = self._build_chunk_wav(self._chunk_frames)
                        # Save tail of this chunk as overlap for next chunk
                        tail_frames = list(self._chunk_frames)[-self._overlap_frames.maxlen:]
                        self._overlap_frames = deque(tail_frames, maxlen=self._overlap_frames.maxlen)
                        # Reset chunk frames
                        self._chunk_frames = []
                        self._frames_since_last_chunk = 0

                        if self._on_chunk_ready and len(chunk_audio) >= self.config.min_chunk_bytes:
                            logger.info(f"Time-chunk ready: {len(chunk_audio)} bytes, "
                                      f"frame={frame_count}")
                            self._on_chunk_ready(chunk_audio)

                    # Auto-stop check: if no speech for auto_stop_timeout seconds
                    if self.config.auto_stop_timeout > 0 and self._on_auto_stop:
                        silence_frames = frame_count - self._last_speech_frame
                        silence_duration = silence_frames * self.config.chunk_size / self.config.sample_rate
                        if silence_duration >= self.config.auto_stop_timeout:
                            logger.info(f"Auto-stop fire: frame={frame_count}, "
                                      f"silence={silence_duration:.1f}s, "
                                      f"threshold={self.config.auto_stop_timeout}s")
                            self._on_auto_stop()

                # Always update waveform buffer
                self.waveform_buffer.append(level)
                if self.level_callback:
                    self.level_callback(level, list(self.waveform_buffer))

            except Exception as e:
                print(f"Recording error: {e}")
                break

    def _build_chunk_wav(self, frames: list[bytes]) -> bytes:
        """Convert frames to WAV bytes for chunk transcription.

        If VAD trim is enabled, removes leading and trailing silence
        using frame energy levels (simple and predictable).
        """
        if not frames:
            return b""

        raw_audio = b"".join(frames)

        # Energy-based silence trimming (dynamic threshold)
        if self.config.vad_trim_silence:
            try:
                # Analyze each frame's energy to find speech boundaries
                chunk_size = self.config.chunk_size  # bytes per frame
                padding_frames = 10  # Keep ~640ms context at edges to avoid cutting words

                # Calculate energy for each frame
                frame_energies = []
                for i in range(0, len(raw_audio), chunk_size):
                    frame_data = raw_audio[i:i + chunk_size]
                    if len(frame_data) >= chunk_size:
                        audio_samples = np.frombuffer(frame_data, dtype=np.int16)
                        energy = np.abs(audio_samples).mean() / 32768.0
                        frame_energies.append(energy)
                    else:
                        # Partial frame at end
                        if frame_data:
                            audio_samples = np.frombuffer(frame_data, dtype=np.int16)
                            energy = np.abs(audio_samples).mean() / 32768.0
                            frame_energies.append(energy)

                # Dynamic threshold: 3% of peak energy (works at any mic level)
                max_energy = max(frame_energies) if frame_energies else 0
                energy_threshold = max(0.00005, max_energy * 0.03)

                # Find first and last frame with energy above threshold
                speech_frames = [i for i, e in enumerate(frame_energies) if e > energy_threshold]

                if speech_frames and len(speech_frames) >= 2:  # At least 2 speech frames
                    first = max(0, speech_frames[0] - padding_frames)
                    last = min(len(frame_energies), speech_frames[-1] + 1 + padding_frames)

                    # Calculate byte positions
                    first_byte = first * chunk_size
                    last_byte = last * chunk_size

                    trimmed = raw_audio[first_byte:last_byte]
                    saved = len(raw_audio) - len(trimmed)
                    if saved > chunk_size * 2:  # Only log if we saved meaningful silence
                        logger.info(f"Energy trim: {len(raw_audio)} -> {len(trimmed)} bytes "
                                  f"({saved} bytes silence removed, "
                                  f"{len(speech_frames)}/{len(frame_energies)} frames with speech)")
                        raw_audio = trimmed
            except Exception as e:
                logger.error(f"Energy trim error: {e}")

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(self.config.channels)
            wf.setsampwidth(2)
            wf.setframerate(self._actual_sample_rate)
            wf.writeframes(raw_audio)
        return wav_buffer.getvalue()

    def flush_remaining_chunk(self) -> bytes | None:
        """Flush remaining chunk frames as a final chunk for transcription.

        Note: does NOT prepend overlap frames — those were already
        transcribed as part of the previous chunk and would only cause
        text duplication in the output.
        """
        if self._streaming_mode and self._chunk_frames:
            chunk_raw = b"".join(self._chunk_frames)
            chunk_audio = self._build_chunk_wav([chunk_raw])
            self._chunk_frames = []
            self._overlap_frames.clear()
            if len(chunk_audio) > 0:
                logger.info(f"Flushing remaining chunk: {len(chunk_audio)} bytes")
                return chunk_audio
        return None

    def disable_streaming(self) -> None:
        """Disable streaming mode without stopping the recorder.

        The recorder continues running for visual feedback, but
        no new chunks will be created and auto-stop will not fire.
        """
        logger.info("disable_streaming: clearing on_chunk_ready + on_auto_stop")
        self._streaming_mode = False
        self._on_chunk_ready = None
        self._on_auto_stop = None
        self._chunk_interval_frames = 0
        logger.info("Streaming mode disabled (recorder still running)")

    def stop(self) -> bytes:
        """Stop recording and return WAV data."""
        self.is_recording = False

        if self._record_thread:
            self._record_thread.join(timeout=1.0)

        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(self.config.channels)
            wf.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
            wf.setframerate(self._actual_sample_rate)
            wf.writeframes(b"".join(self.frames))

        return wav_buffer.getvalue()

    def cleanup(self) -> None:
        """Clean up audio resources."""
        self.is_recording = False
        if self.stream:
            self.stream.close()
        self.audio.terminate()
