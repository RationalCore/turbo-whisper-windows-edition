"""Integration test: batch vs streaming transcription comparison.

Transcribes a WAV file in two modes and verifies the results are similar:
1. Batch mode — full audio sent as one request
2. Streaming mode — audio split into fixed-duration chunks with overlap,
   each transcribed with previous context (simulating the streaming pipeline)
"""

import io
import sys
import wave
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

WAV_PATH = Path(__file__).parent.parent / "test.wav"

SAMPLE_RATE = 16000
CHUNK_SECONDS = 6          # chunk duration (matches config.chunk_duration_seconds)
OVERLAP_SECONDS = 1.0      # overlap between chunks (~1s covers word boundaries)
MIN_CHUNK_BYTES = 8000
SIMILARITY_THRESHOLD = 0.80


def load_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 1, f"Expected mono, got {wf.getnchannels()}"
        assert wf.getsampwidth() == 2, f"Expected 16-bit, got {wf.getsampwidth()}"
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    return frames, sr


def raw_to_wav(raw_audio: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw_audio)
    return buf.getvalue()


def split_by_time(raw: bytes, sample_rate: int,
                  chunk_sec: int = CHUNK_SECONDS,
                  overlap_sec: float = OVERLAP_SECONDS) -> list[bytes]:
    """Split raw PCM into fixed-duration chunks with overlap.

    Each chunk is `chunk_sec` seconds long. Consecutive chunks overlap
    by `overlap_sec` seconds to avoid cutting words at boundaries.
    Matches the time-based streaming mode in recorder.py.
    """
    chunk_frames = int(sample_rate * chunk_sec)
    chunk_bytes = chunk_frames * 2  # 16-bit mono
    overlap_frames = int(sample_rate * overlap_sec)
    overlap_bytes = overlap_frames * 2
    stride = chunk_bytes - overlap_bytes  # how many new bytes per chunk

    chunks = []
    pos = 0
    while pos < len(raw):
        chunk = raw[pos:pos + chunk_bytes]
        if len(chunk) < MIN_CHUNK_BYTES:
            break  # too short, will be appended to previous chunk
        chunks.append(chunk)
        pos += stride

    # Attach remaining tail to last chunk
    tail = raw[pos:]
    if chunks and tail:
        chunks[-1] = chunks[-1] + tail
    elif not chunks and tail:
        chunks.append(tail)

    return chunks


def energy_trim(raw_pcm: bytes, chunk_size: int = 1024,
                energy_threshold: float = 0.002,
                padding_frames: int = 10) -> bytes:
    """Simulate recorder._build_chunk_wav energy trim — both sides."""
    import numpy as np
    frame_energies = []
    for i in range(0, len(raw_pcm), chunk_size):
        frame = raw_pcm[i:i + chunk_size]
        if len(frame) >= chunk_size:
            audio = np.frombuffer(frame, dtype=np.int16)
            energy = np.abs(audio).mean() / 32768.0
            frame_energies.append(energy)
        elif frame:
            audio = np.frombuffer(frame, dtype=np.int16)
            energy = np.abs(audio).mean() / 32768.0
            frame_energies.append(energy)

    speech_frames = [i for i, e in enumerate(frame_energies) if e > energy_threshold]
    if speech_frames and len(speech_frames) >= 2:
        first = max(0, speech_frames[0] - padding_frames)
        last = min(len(frame_energies), speech_frames[-1] + 1 + padding_frames)
        first_byte = first * chunk_size
        last_byte = last * chunk_size
        return raw_pcm[first_byte:last_byte]
    return raw_pcm


def tokenize(text: str) -> set[str]:
    import re
    return set(re.sub(r"[^\w\s]", "", text.lower()).split())


def word_overlap(ref: set[str], test: set[str]) -> float:
    if not ref:
        return 0.0
    return len(ref & test) / len(ref)


def test_batch_vs_streaming():
    print("=" * 60)
    print("Batch vs Streaming Transcription Comparison")
    print("=" * 60)

    assert WAV_PATH.exists(), f"WAV not found: {WAV_PATH}"
    raw_audio, sample_rate = load_wav(WAV_PATH)
    duration_s = len(raw_audio) / (sample_rate * 2)
    print(f"\n1. Loaded: {WAV_PATH.name}")
    print(f"   Duration: {duration_s:.1f}s, {len(raw_audio)} bytes")

    from turbo_whisper.config import Config
    from turbo_whisper.api import WhisperClient
    from turbo_whisper.main import _is_hallucination

    config = Config.load()
    client = WhisperClient(config)

    # 2. Batch transcription
    print(f"\n2. Batch transcription...")
    batch_text = client.transcribe_sync(raw_to_wav(raw_audio, sample_rate))
    print(f"   '{batch_text}'")
    assert batch_text.strip(), "Batch returned empty!"
    batch_words = tokenize(batch_text)

    # 3. Streaming simulation — time-based chunks with energy trim (vad_trim_silence=true)
    print(f"\n3. Time-based splitting ({CHUNK_SECONDS}s chunks, {OVERLAP_SECONDS}s overlap)...")
    chunks = split_by_time(raw_audio, sample_rate)
    # Apply energy trim (threshold=0.002, padding=10 frames) — matches recorder.py
    trimmed_chunks = [energy_trim(c) for c in chunks]
    trimmed_chunks = [c for c in trimmed_chunks if len(c) >= MIN_CHUNK_BYTES]
    print(f"   Split into {len(chunks)} chunks ({len(trimmed_chunks)} after energy trim 0.002/10fr)")

    accumulated = ""
    streaming_parts = []
    hallucination_count = 0
    for i, chunk_raw in enumerate(trimmed_chunks):
        chunk_wav = raw_to_wav(chunk_raw, sample_rate)
        context = accumulated[-300:] if accumulated else ""
        text = client.transcribe_sync(chunk_wav, context=context)
        clean = text.strip()

        if not clean or _is_hallucination(clean):
            hallucination_count += 1
            print(f"   Chunk #{i+1}: [filtered: '{clean or '(empty)'}']")
            continue

        streaming_parts.append(clean)
        accumulated += " " + clean
        print(f"   Chunk #{i+1}: '{clean[:80]}'")

    streaming_text = " ".join(streaming_parts).strip()
    print(f"\n   Streaming: '{streaming_text}'")
    assert streaming_text.strip(), "Streaming returned empty!"
    streaming_words = tokenize(streaming_text)

    # 4. Compare
    overlap = word_overlap(batch_words, streaming_words)
    print(f"\n4. Word overlap: {overlap:.0%}  (threshold: {SIMILARITY_THRESHOLD:.0%})")
    print(f"   Batch words: {len(batch_words)}, Streaming words: {len(streaming_words)}")
    if hallucination_count:
        print(f"   Filtered hallucinations: {hallucination_count}")

    assert overlap >= SIMILARITY_THRESHOLD, (
        f"FAIL: overlap {overlap:.0%} < {SIMILARITY_THRESHOLD:.0%}\n"
        f"  Batch:    '{batch_text}'\n"
        f"  Streaming: '{streaming_text}'"
    )
    print(f"   ✓ PASS")

    print(f"\n5. Full batch:")
    print(f"   '{batch_text}'")
    print(f"\n   Full streaming:")
    print(f"   '{streaming_text}'")
    print(f"\n{'=' * 20} TEST PASSED {'=' * 20}")


if __name__ == "__main__":
    test_batch_vs_streaming()
