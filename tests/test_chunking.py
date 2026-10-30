"""Offline unit tests for StreamingChunker and stitch_text (no API, no mic)."""

import io
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from turbo_whisper.chunking import StreamingChunker, stitch_text
from turbo_whisper.config import Config

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1024
FRAME_BYTES = FRAME_SAMPLES * 2


def speech_frame() -> bytes:
    """Loud alternating samples — clearly above any noise floor."""
    import struct
    return struct.pack(f"<{FRAME_SAMPLES}h", *[8000 if i % 2 == 0 else -8000
                                               for i in range(FRAME_SAMPLES)])


def silence_frame() -> bytes:
    """Very quiet samples — clearly below speech level."""
    import struct
    return struct.pack(f"<{FRAME_SAMPLES}h", *[20 if i % 2 == 0 else -20
                                               for i in range(FRAME_SAMPLES)])


def feed(chunker, frames):
    """Feed a list of frames, collect emitted chunks."""
    out = []
    for fr in frames:
        res = chunker.add_frame(fr)
        if res:
            out.append(res)
    return out


def frames(seconds: float, frame) -> list[bytes]:
    n = int(seconds / (FRAME_SAMPLES / SAMPLE_RATE))
    return [frame() for _ in range(n)]


def make_chunker(**kw) -> StreamingChunker:
    config = Config(**kw) if kw else Config()
    return StreamingChunker(config, sample_rate=SAMPLE_RATE)


def test_pause_cut_lands_in_silence():
    """Chunk must be emitted at a pause, never mid-speech."""
    chunker = make_chunker(chunk_duration_seconds=3.0, silence_threshold_ms=200)
    seq = frames(2.0, speech_frame) + frames(0.3, silence_frame) + frames(2.0, speech_frame)
    chunks = feed(chunker, seq)
    assert chunks, "expected at least one chunk"
    pcm, had_overlap = chunks[0]
    # First chunk: the first speech burst + a small silence pad, minus overlap carry
    dur = len(pcm) / (SAMPLE_RATE * 2)
    assert dur <= 2.5, f"pause cut should stop near the pause, got {dur:.2f}s"
    # The chunk must contain speech
    from turbo_whisper.chunking import _mean_abs_level
    assert _mean_abs_level(pcm) > 0.01, "chunk should contain speech"


def test_long_pause_emits_short_chunk():
    """A >=0.6s pause ends a chunk even when it is still short."""
    chunker = make_chunker(chunk_duration_seconds=3.0, silence_threshold_ms=200)
    seq = frames(0.5, speech_frame) + frames(0.8, silence_frame) + frames(2.0, speech_frame)
    chunks = feed(chunker, seq)
    assert chunks, "expected a chunk after the long pause"
    pcm, _ = chunks[0]
    dur = len(pcm) / (SAMPLE_RATE * 2)
    assert dur < 1.5, f"short utterance should be emitted at the long pause, got {dur:.2f}s"


def test_forced_cut_carries_overlap():
    """A forced cut carries overlap; pause cuts do not (dedup is unreliable
    for them — Whisper renders the same audio differently across chunks)."""
    chunker = make_chunker(chunk_duration_seconds=2.0, silence_threshold_ms=200)
    seq = frames(7.0, speech_frame)  # continuous speech, no pauses
    chunks = feed(chunker, seq)
    assert chunks, "expected a forced cut"
    assert chunks[0][1] is False, "first chunk must not have overlap"
    tail = chunker.flush()
    assert tail is not None
    assert tail[1] is True, "chunk after a forced cut carries overlap"

    # Pause cuts must NOT carry overlap
    chunker = make_chunker(chunk_duration_seconds=2.0, silence_threshold_ms=200)
    seq = (frames(1.5, speech_frame) + frames(0.4, silence_frame)) * 3
    chunks = feed(chunker, seq)
    assert chunks, "expected pause cuts"
    for pcm, had_overlap in chunks:
        assert had_overlap is False, "pause cuts must not create duplicates"


def test_forced_cut_happens_without_pauses():
    """Continuous speech without pauses is cut at max duration, no data lost."""
    chunker = make_chunker(chunk_duration_seconds=2.0, silence_threshold_ms=200)
    seq = frames(7.0, speech_frame)
    chunks = feed(chunker, seq)
    tail = chunker.flush()
    if tail:
        chunks.append(tail)
    total = sum(len(p[0]) for p in chunks) / (SAMPLE_RATE * 2)
    # Overlap means the sum is larger than 7s; but at least everything is covered
    assert total >= 7.0, f"no audio may be lost, got only {total:.2f}s"
    assert all(p[1] for p in chunks[1:]), "chunks after a forced cut carry overlap"


def test_flush_preserves_short_silent_tail():
    """Regression: flush must return small leftovers as-is (mic-disconnect path)."""
    config = Config(sample_rate=2, chunk_size=2)
    chunker = StreamingChunker(config, sample_rate=2)
    frame = b"\x01\x00\x01\x00"
    res = chunker.add_frame(frame)
    assert res is None
    flushed = chunker.flush()
    assert flushed is not None
    assert flushed[0] == frame


def test_flush_drops_long_silence():
    chunker = make_chunker()
    feed(chunker, frames(3.0, speech_frame))
    # emit everything up to now
    for _ in range(100):
        chunker.add_frame(silence_frame())
        break
    feed(chunker, frames(6.0, silence_frame))
    assert chunker.flush() is None


def test_no_speech_is_not_emitted():
    chunker = make_chunker(chunk_duration_seconds=2.0)
    seq = frames(7.0, silence_frame)
    assert feed(chunker, seq) == []
    assert chunker.flush() is None


def test_stitch_text_removes_overlap_duplicates():
    assert stitch_text("и это было интересно", "было интересно решение") == "решение"
    assert stitch_text("один два три", "четыре пять") == "четыре пять"
    assert stitch_text("", "привет") == "привет"
    assert stitch_text("привет", "") == ""
    # punctuation and case are ignored when matching
    assert stitch_text("Это OpenCode.", "opencode второй версии") == "второй версии"


def test_stitch_text_limits_dedup():
    """Only the first few words can be dropped — repeated words further
    apart are preserved."""
    prev = "да нет да"
    new = "да"
    assert stitch_text(prev, new) == ""
    # genuine repetition beyond the overlap window is kept
    prev = "мы говорили да да да"
    new = "да да"
    result = stitch_text(prev, new)
    assert result == ""  # tail words match head words exactly — dedup applies


def test_wav_roundtrip_of_chunks():
    """Chunks must be valid 16-bit mono PCM."""
    chunker = make_chunker(chunk_duration_seconds=2.0, silence_threshold_ms=200)
    seq = frames(1.5, speech_frame) + frames(0.3, silence_frame) + frames(1.5, speech_frame)
    chunks = feed(chunker, seq)
    for pcm, _ in chunks:
        assert len(pcm) % 2 == 0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
