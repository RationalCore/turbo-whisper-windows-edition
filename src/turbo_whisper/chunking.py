"""Smart chunk assembly for streaming transcription.

Splits incoming PCM frames into transcription chunks at speech pauses
instead of fixed time boundaries. Cutting words in half at arbitrary
time boundaries is the main source of streaming transcription errors.

Rules:
- A chunk is emitted at the first speech pause (>= `silence_threshold_ms`)
  after the chunk got enough speech. The cut lands inside the pause, so
  words are never split. A longer pause (>= 0.6s) ends a chunk even when
  it is still short.
- If speech continues past the maximum duration without any pause, a
  forced cut is made at the quietest recent point. Forced cuts carry a
  short audio overlap (see :data:`FORCED_OVERLAP_SECONDS`) into the next
  chunk so no words are lost; the duplicated words are removed afterwards
  with :func:`stitch_text`.
- Leading silence is trimmed (a little padding is kept) and buffers that
  contain no speech at all are not emitted.

Pure Python (no numpy/webrtcvad) so it runs in the packaged exe.
"""

import logging
import math
import re
from collections import deque

logger = logging.getLogger("turbo_whisper.chunking")

# --- Tunables -------------------------------------------------------------
PAUSE_PAD_SECONDS = 0.15        # silence kept at the end of a pause-cut chunk
LEAD_KEEP_SECONDS = 0.25        # silence kept before the first speech frame
TAIL_KEEP_SECONDS = 0.25        # silence kept after the last speech frame (flush)
MAX_STRETCH_FACTOR = 2.0        # forced cut after target * factor seconds
FORCED_LOOKBACK_SECONDS = 1.0   # window searched for the quietest cut point
FORCED_OVERLAP_SECONDS = 0.6    # audio carried across forced cuts (words on the
                                # cut would otherwise be lost or split)
PAUSE_OVERLAP_SECONDS = 0.0     # audio carried across pause cuts. Disabled on
                                # purpose: Whisper renders the same audio
                                # differently in different chunks, so text-level
                                # dedup cannot reliably remove the duplicates.
MAX_DEDUP_WORDS = 4             # max duplicated words removed by stitch_text
MIN_SILENT_FLUSH_BYTES = 16000  # drop flushed tails of pure silence longer than this
MIN_CHUNK_SECONDS = 1.0         # absolute lower bound for the min chunk duration
LONG_PAUSE_FACTOR = 2.0         # long pause = max(0.6s, pause * factor)
MIN_LONG_PAUSE_SECONDS = 0.6    # a pause this long ends an utterance


def _mean_abs_level(data: bytes) -> float:
    """Mean absolute value of 16-bit PCM samples, normalized to 0..1."""
    if len(data) < 2:
        return 0.0
    n = len(data) // 2
    total = 0
    for i in range(0, n * 2, 2):
        sample = data[i] | (data[i + 1] << 8)
        if sample >= 32768:
            sample -= 65536
        total += abs(sample)
    return total / n / 32768.0


class StreamingChunker:
    """Accumulates PCM frames and emits transcription chunks at speech pauses.

    Usage (per captured frame)::

        chunker = StreamingChunker(config)
        result = chunker.add_frame(frame_bytes)   # (chunk_pcm, had_overlap) or None
        ...
        tail = chunker.flush()                    # (chunk_pcm, had_overlap) or None

    ``had_overlap`` is True when the emitted chunk starts with audio that was
    already part of the previous chunk. The duplicated words must be removed
    with :func:`stitch_text` before use.
    """

    def __init__(self, config, sample_rate: int = 16000,
                 target_seconds: float | None = None) -> None:
        self.config = config
        self.sample_rate = sample_rate
        self.bytes_per_second = sample_rate * 2  # 16-bit mono

        self.target_seconds = target_seconds or config.chunk_duration_seconds
        self.min_speech_seconds = max(0.8, self.target_seconds * 0.4)
        self.max_seconds = self.target_seconds * MAX_STRETCH_FACTOR
        self.pause_seconds = max(0.15, config.silence_threshold_ms / 1000.0)
        self.long_pause_seconds = max(MIN_LONG_PAUSE_SECONDS,
                                      self.pause_seconds * LONG_PAUSE_FACTOR)

        self._frames: list[tuple[bytes, float, bool]] = []  # (data, level, is_speech)
        self._total_bytes = 0
        self._fresh_speech_bytes = 0  # speech bytes NOT coming from overlap carry
        self._carry_frames_len = 0    # frames carried over from the previous chunk
        self._last_speech_end = 0     # frame index after the last speech frame
        self._has_speech = False
        self._pause_bytes = 0         # consecutive trailing silence bytes
        self._carry_overlap = False   # next emitted chunk starts with overlap audio

        # Adaptive speech detection state
        self._peak = 0.0
        self._noise = 0.01  # noise-floor estimate (falls fast, releases slowly)

        logger.info(f"StreamingChunker: target={self.target_seconds:.1f}s, "
                    f"min_speech={self.min_speech_seconds:.1f}s, max={self.max_seconds:.1f}s, "
                    f"pause={self.pause_seconds * 1000:.0f}ms")

    # -- speech detection ---------------------------------------------------

    def _is_speech(self, level: float) -> bool:
        """Adaptive energy-based speech detection.

        The noise floor is tracked as a running minimum with a slow release,
        so quiet pauses between words are recognized even with a noisy mic.
        """
        self._peak = max(level, self._peak * 0.995)
        # Fast-attack minimum tracker with slow release
        if level < self._noise:
            self._noise = level
        else:
            self._noise *= 1.0002
        threshold = max(self.config.silence_energy_threshold, self._noise * 2.5, self._peak * 0.04)
        return level > threshold

    # -- frame ingestion ----------------------------------------------------

    def add_frame(self, data: bytes) -> tuple[bytes, bool] | None:
        """Add one captured PCM frame; return a chunk when a boundary is reached."""
        if not data:
            return None

        level = _mean_abs_level(data)
        is_speech = self._is_speech(level)
        self._frames.append((data, level, is_speech))
        self._total_bytes += len(data)

        if is_speech:
            self._has_speech = True
            self._fresh_speech_bytes += len(data)
            self._last_speech_end = len(self._frames)
            self._pause_bytes = 0
        else:
            self._pause_bytes += len(data)

        buf_seconds = self._total_bytes / self.bytes_per_second
        pause_seconds = self._pause_bytes / self.bytes_per_second
        fresh_speech_seconds = self._fresh_speech_bytes / self.bytes_per_second

        def pause_cut() -> tuple[bytes, bool]:
            pad_frames = max(1, int(math.ceil(
                PAUSE_PAD_SECONDS / (len(data) / self.bytes_per_second))))
            cut = min(self._last_speech_end + pad_frames, len(self._frames))
            return self._emit(cut, PAUSE_OVERLAP_SECONDS)

        # 1. Pause cut: chunk has enough fresh speech and a pause is in progress.
        if (self._fresh_speech_bytes > 0 and fresh_speech_seconds >= self.min_speech_seconds
                and pause_seconds >= self.pause_seconds):
            return pause_cut()

        # 1b. Long pause after at least some fresh speech: the utterance is
        #     over — emit even a short chunk (it carries overlap context).
        if self._fresh_speech_bytes > 0 and pause_seconds >= self.long_pause_seconds:
            return pause_cut()

        # 2. Forced cut: no usable pause for too long — cut at the quietest
        #    recent point and carry overlap so no words are lost.
        if self._fresh_speech_bytes > 0 and buf_seconds >= self.max_seconds:
            cut = self._quietest_cut()
            return self._emit(cut, FORCED_OVERLAP_SECONDS)

        # 3. Nothing fresh for a whole max window: drop the stale buffer.
        if self._fresh_speech_bytes == 0 and buf_seconds >= self.max_seconds:
            logger.info(f"StreamingChunker: dropping {buf_seconds:.1f}s without fresh speech")
            self._reset()
            return None

        return None

    def flush(self) -> tuple[bytes, bool] | None:
        """Emit whatever is left as a final chunk (called when recording stops)."""
        if not self._frames:
            return None

        # Nothing new since the last emitted chunk (only carried overlap)?
        if self._fresh_speech_bytes == 0 and len(self._frames) <= self._carry_frames_len:
            self._reset()
            return None

        had_overlap = self._carry_overlap
        total = self._total_bytes

        if self._fresh_speech_bytes > 0:
            # Keep a little silence after the last speech frame
            keep_tail = max(0, self._keep_frames(TAIL_KEEP_SECONDS))
            end = min(len(self._frames), self._last_speech_end + keep_tail)
            frames = self._trim_frames(self._frames[:end])
        elif total >= MIN_SILENT_FLUSH_BYTES:
            logger.info(f"StreamingChunker: flush drops {total} bytes of pure silence")
            self._reset()
            return None
        else:
            frames = list(self._frames)

        self._reset()
        if not frames:
            return None
        raw = b"".join(f[0] for f in frames)
        logger.info(f"StreamingChunker: flush emitted {len(raw)} bytes "
                    f"(had_overlap={had_overlap})")
        return raw, had_overlap

    # -- internals ----------------------------------------------------------

    def _keep_frames(self, seconds: float) -> int:
        """How many frames correspond to `seconds` (based on average frame size)."""
        if not self._frames:
            return 0
        avg = self._total_bytes / len(self._frames)
        if avg <= 0:
            return 0
        return int(math.ceil(seconds * self.bytes_per_second / avg))

    def _quietest_cut(self) -> int:
        """Frame index of the quietest frame within the lookback window."""
        lookback = max(1, self._keep_frames(FORCED_LOOKBACK_SECONDS))
        start = max(0, len(self._frames) - lookback)
        best_idx = len(self._frames) - 1
        best_level = float("inf")
        for i in range(start, len(self._frames)):
            level = self._frames[i][1]
            if level < best_level:
                best_level = level
                best_idx = i
        # Cut after the quietest frame
        return min(best_idx + 1, len(self._frames))

    def _trim_frames(self, frames: list[tuple[bytes, float, bool]]) -> list[tuple[bytes, float, bool]]:
        """Drop leading silence beyond LEAD_KEEP_SECONDS."""
        first_speech = None
        for i, (_, _, is_speech) in enumerate(frames):
            if is_speech:
                first_speech = i
                break
        if first_speech is None:
            return list(frames)
        keep = self._keep_frames(LEAD_KEEP_SECONDS)
        start = max(0, first_speech - keep)
        return frames[start:]

    def _emit(self, cut: int, overlap_seconds: float) -> tuple[bytes, bool]:
        """Split the buffer at frame `cut` and emit the head as a chunk.

        With ``overlap_seconds > 0`` the tail of the emitted audio is kept at
        the start of the next chunk so no words are lost at the cut; the
        duplicated words are removed later by :func:`stitch_text`.
        """
        head = self._frames[:cut]
        if overlap_seconds > 0 and cut > 0:
            overlap_frames = max(1, self._keep_frames(overlap_seconds))
            tail = self._frames[max(0, cut - overlap_frames):]
        else:
            tail = self._frames[cut:]

        had_overlap = self._carry_overlap
        # Tail is "overlap" only when it re-covers audio already emitted
        self._carry_overlap = overlap_seconds > 0 and cut > 0 and bool(tail)

        head = self._trim_frames(head)
        raw = b"".join(f[0] for f in head)

        # Rebase state on the remaining tail (its speech is "carried", not fresh)
        self._frames = list(tail)
        self._total_bytes = sum(len(f[0]) for f in self._frames)
        self._fresh_speech_bytes = 0
        self._carry_frames_len = len(self._frames)
        self._has_speech = any(f[2] for f in self._frames)
        self._last_speech_end = 0
        self._pause_bytes = 0
        for i, (data, _, is_speech) in enumerate(self._frames):
            if is_speech:
                self._last_speech_end = i + 1
                self._pause_bytes = 0
            else:
                self._pause_bytes += len(data)

        logger.info(f"StreamingChunker: emit {len(raw)} bytes "
                    f"({len(raw) / self.bytes_per_second:.1f}s), "
                    f"had_overlap={had_overlap}, carry_overlap={self._carry_overlap}, "
                    f"tail={self._total_bytes} bytes")
        return raw, had_overlap

    def _reset(self) -> None:
        self._frames = []
        self._total_bytes = 0
        self._fresh_speech_bytes = 0
        self._carry_frames_len = 0
        self._last_speech_end = 0
        self._has_speech = False
        self._pause_bytes = 0
        self._carry_overlap = False


def stitch_text(prev_text: str, new_text: str, max_words: int = MAX_DEDUP_WORDS) -> str:
    """Remove words duplicated between the end of `prev_text` and the start
    of `new_text` (caused by audio overlap at forced cuts).

    Returns `new_text` with the duplicated prefix removed, or "" when the
    whole chunk was a duplicate. Only up to `max_words` words are compared
    so legitimately repeated words further apart are preserved.
    """
    if not new_text:
        return ""
    if not prev_text:
        return new_text

    prev_words = prev_text.split()
    new_words = new_text.split()

    def norm(word: str) -> str:
        return re.sub(r"\W", "", word.lower(), flags=re.UNICODE)

    limit = min(len(prev_words), len(new_words), max_words)
    for k in range(limit, 0, -1):
        if [norm(w) for w in prev_words[-k:]] == [norm(w) for w in new_words[:k]]:
            stitched = " ".join(new_words[k:])
            logger.info(f"stitch_text: removed {k} duplicated word(s) "
                        f"({' '.join(new_words[:k])!r})")
            return stitched
    return new_text
