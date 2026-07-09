"""Voice Activity Detection (VAD) using webrtcvad for streaming transcription."""

import logging
import struct

logger = logging.getLogger("turbo_whisper.silence")


class SilenceDetector:
    """Detects silence after speech using webrtcvad.

    Uses Google's WebRTC VAD library for accurate voice activity detection.
    Much more reliable than simple energy-based detection.
    """

    def __init__(
        self,
        silence_threshold_ms: int = 300,
        min_speech_ms: int = 100,
        energy_threshold: float = 0.01,
        chunk_duration_ms: int = 30,
        aggressiveness: int = 1,
    ):
        """Initialize silence detector with webrtcvad.

        Args:
            silence_threshold_ms: How long silence (ms) after speech triggers a chunk
            min_speech_ms: Minimum speech duration before we consider it valid
            energy_threshold: Fallback energy threshold (used if webrtcvad unavailable)
            chunk_duration_ms: Duration of each audio chunk from PyAudio
            aggressiveness: VAD aggressiveness (0-3), higher = more aggressive filtering
                          0=quality, 1=low bitrate, 2=default, 3=aggressive
        """
        self.silence_threshold_ms = silence_threshold_ms
        self.min_speech_ms = min_speech_ms
        self.energy_threshold = energy_threshold
        self.chunk_duration_ms = chunk_duration_ms
        self.sample_rate = 16000  # webrtcvad requires 8000, 16000, 32000, or 48000

        # Frame size for webrtcvad (10ms, 20ms, or 30ms)
        self.frame_duration_ms = 30  # 30ms frames work well
        self.frame_size = int(self.sample_rate * self.frame_duration_ms / 1000)

        # Try to initialize webrtcvad
        self._vad = None
        self._use_webrtcvad = False
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(aggressiveness)
            self._use_webrtcvad = True
            logger.info(f"webrtcvad initialized successfully: aggressiveness={aggressiveness}, "
                       f"sample_rate={self.sample_rate}, frame_size={self.frame_size}")
        except ImportError:
            logger.warning("webrtcvad not installed, using energy-based VAD")
        except Exception as e:
            logger.warning(f"webrtcvad initialization failed: {e}, using energy-based VAD")

        # State tracking
        self._threshold_frames = max(1, silence_threshold_ms // chunk_duration_ms)
        self._min_speech_frames = max(1, min_speech_ms // chunk_duration_ms)
        self._silence_frames = 0
        self._speech_frames = 0
        self._speech_detected = False
        self._in_speech = False

        # Buffer for incomplete frames
        self._frame_buffer = b""

    def update(self, audio_data: bytes, energy_level: float = 0.0) -> bool:
        """Update with new audio data and check for silence after speech.

        Args:
            audio_data: Raw PCM audio data (16-bit, 16kHz, mono)
            energy_level: Fallback energy level for non-webrtcvad mode

        Returns:
            True when silence after speech is detected (time to transcribe chunk)
        """
        # Use webrtcvad if available
        if self._use_webrtcvad and self._vad:
            return self._update_webrtcvad(audio_data)
        else:
            # Fallback to energy-based detection
            return self._update_energy(energy_level)

    def _update_webrtcvad(self, audio_data: bytes) -> bool:
        """Update using webrtcvad for accurate speech detection."""
        # Add new audio to buffer
        self._frame_buffer += audio_data

        # Process complete frames
        frames_processed = 0
        while len(self._frame_buffer) >= self.frame_size * 2:  # 2 bytes per sample (16-bit)
            # Extract one frame
            frame = self._frame_buffer[:self.frame_size * 2]
            self._frame_buffer = self._frame_buffer[self.frame_size * 2:]

            # Check if frame contains speech
            try:
                is_speech = self._vad.is_speech(frame, self.sample_rate)
                frames_processed += 1
            except Exception as e:
                # If webrtcvad fails (e.g., wrong frame size), log and skip
                if frames_processed == 0:
                    logger.warning(f"webrtcvad error: {e}, frame_size={len(frame)}")
                continue

            if is_speech:
                if not self._speech_detected:
                    logger.debug(f"webrtcvad: Speech detected")
                self._speech_detected = True
                self._in_speech = True
                self._speech_frames += 1
                self._silence_frames = 0
            else:
                # Silence
                if self._in_speech:
                    self._silence_frames += 1
                    if self._silence_frames >= self._threshold_frames:
                        # Silence after speech - check if we had enough speech
                        if self._speech_frames >= self._min_speech_frames:
                            logger.info(
                                f"webrtcvad: SILENCE TRIGGER - speech={self._speech_frames} frames, "
                                f"silence={self._silence_frames} frames"
                            )
                            self.reset()
                            return True
                        else:
                            # Speech too short, reset
                            logger.debug(f"webrtcvad: Speech too short ({self._speech_frames} < {self._min_speech_frames})")
                            self.reset()

        return False

    def _update_energy(self, energy_level: float) -> bool:
        """Fallback energy-based detection."""
        if energy_level > self.energy_threshold:
            if not self._speech_detected:
                logger.debug(f"Energy: Speech started (energy={energy_level:.4f})")
            self._speech_detected = True
            self._in_speech = True
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            if self._in_speech:
                self._silence_frames += 1
                if self._silence_frames >= self._threshold_frames:
                    if self._speech_frames >= self._min_speech_frames:
                        logger.info(
                            f"Energy: SILENCE TRIGGER - speech={self._speech_frames} frames, "
                            f"silence={self._silence_frames} frames"
                        )
                        self.reset()
                        return True
                    else:
                        logger.debug(f"Energy: Speech too short ({self._speech_frames} < {self._min_speech_frames})")
                        self.reset()

        return False

    def reset(self):
        """Reset detector state."""
        self._silence_frames = 0
        self._speech_frames = 0
        self._speech_detected = False
        self._in_speech = False
        self._frame_buffer = b""

    def update_threshold(self, silence_threshold_ms: int):
        """Update the silence threshold dynamically."""
        self.silence_threshold_ms = silence_threshold_ms
        self._threshold_frames = max(1, silence_threshold_ms // self.chunk_duration_ms)
