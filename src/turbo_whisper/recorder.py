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
        self._silence_detector = None
        self._chunk_frames: list[bytes] = []
        self._overlap_frames: deque[bytes] = deque(maxlen=10)

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
        silence_detector=None,
        on_auto_stop: Callable[[], None] | None = None,
    ) -> None:
        """Start recording audio.

        Args:
            level_callback: Called with (level, waveform_buffer) for UI updates
            streaming_mode: If True, detect silence and call on_chunk_ready
            on_chunk_ready: Called with WAV bytes when a speech chunk is detected
            silence_detector: SilenceDetector instance for VAD
            on_auto_stop: Called when auto-stop timeout reached (no speech detected)
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
        self._silence_detector = silence_detector
        self._chunk_frames = []
        self._overlap_frames = deque(maxlen=10)

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

                # Calculate energy level
                audio_data = np.frombuffer(data, dtype=np.int16)
                level = np.abs(audio_data).mean() / 32768.0

                if self._streaming_mode and self._silence_detector:
                    # Streaming mode: accumulate frames and check for silence
                    self._chunk_frames.append(data)
                    self._overlap_frames.append(data)

                    # Log every 100 frames to see energy levels
                    if frame_count % 100 == 0:
                        logger.info(f"Streaming frame={frame_count}: level={level:.6f}, "
                                  f"threshold={self._silence_detector.energy_threshold}, "
                                  f"chunk_frames={len(self._chunk_frames)}, "
                                  f"speech_detected={self._silence_detector._speech_detected}")

                    # Pass audio data and energy level to detector
                    if self._silence_detector.update(data, level):
                        # Silence after speech detected - send chunk for transcription
                        chunk_audio = self._build_chunk_wav(self._chunk_frames)
                        logger.info(f"SILENCE DETECTED! Chunk ready: {len(chunk_audio)} bytes, "
                                  f"frames={len(self._chunk_frames)}")
                        self._chunk_frames = []  # Clear chunk
                        self._overlap_frames.clear()  # Clear overlap to avoid duplication

                        if self._on_chunk_ready and len(chunk_audio) > 0:
                            self._on_chunk_ready(chunk_audio)

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

        # Energy-based silence trimming
        if self.config.vad_trim_silence:
            try:
                # Analyze each frame's energy to find speech boundaries
                chunk_size = self.config.chunk_size  # bytes per frame
                energy_threshold = 0.005  # Lower = more sensitive (more speech kept)
                padding_frames = 2  # Keep 2 frames (~60ms) of context

                # Calculate energy for each frame
                frame_energies = []
                for i in range(0, len(raw_audio), chunk_size):
                    frame_data = raw_audio[i:i + chunk_size]
                    if len(frame_data) >= chunk_size:
                        audio_samples = np.frombuffer(frame_data, dtype=np.int16)
                        energy = np.abs(audio_samples).mean() / 32768.0
                        frame_energies.append(energy)
                    else:
                        # Partial frame at end - use lower threshold
                        if frame_data:
                            audio_samples = np.frombuffer(frame_data, dtype=np.int16)
                            energy = np.abs(audio_samples).mean() / 32768.0
                            frame_energies.append(energy)

                # Find first and last frame with energy above threshold
                speech_frames = [i for i, e in enumerate(frame_energies) if e > energy_threshold]

                if speech_frames and len(speech_frames) >= 2:  # At least 2 speech frames
                    first = max(0, speech_frames[0] - padding_frames)
                    last = min(len(frames), speech_frames[-1] + 1 + padding_frames)

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

        Returns:
            WAV bytes if there are remaining frames, None otherwise
        """
        if self._streaming_mode and self._chunk_frames:
            # Only use remaining chunk frames, not overlap
            chunk_audio = self._build_chunk_wav(self._chunk_frames)
            self._chunk_frames = []
            self._overlap_frames.clear()
            if len(chunk_audio) > 0:
                logger.info(f"Flushing remaining chunk: {len(chunk_audio)} bytes")
                return chunk_audio
        return None

    def disable_streaming(self) -> None:
        """Disable streaming mode without stopping the recorder.

        The recorder continues running for visual feedback, but
        no new chunks will be created.
        """
        self._streaming_mode = False
        self._on_chunk_ready = None
        self._silence_detector = None
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
