"""Benchmark: streaming-mode transcription quality vs batch mode.

Runs the real WhisperClient against saved history recordings and compares:
  1. Batch     - whole WAV sent as one request (reference quality)
  2. Streaming - audio split into chunks like the streaming pipeline does,
                 each chunk transcribed separately and stitched together

Usage:
    uv run python tests/bench_streaming_quality.py [--mode legacy|smart] [--limit N]

Results are printed as a table and saved to tests/bench_results_<mode>.txt (UTF-8).
"""

import argparse
import difflib
import io
import json
import re
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from turbo_whisper.config import Config  # noqa: E402

RESULTS_DIR = Path(__file__).parent
DEFAULT_LIMIT = 5


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def load_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if channels != 1 or sampwidth != 2:
        raise ValueError(f"{path.name}: expected mono 16-bit, got {channels}ch/{sampwidth * 8}bit")
    return frames, sr


def raw_to_wav(raw: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw)
    return buf.getvalue()


def frame_energy(data: bytes) -> float:
    import array
    samples = array.array("h")
    samples.frombytes(data)
    return sum(abs(s) for s in samples) / len(samples) / 32768.0


# ---------------------------------------------------------------------------
# Chunkers
# ---------------------------------------------------------------------------

def legacy_chunks(raw: bytes, sr: int, chunk_seconds: float, min_chunk_bytes: int) -> list[bytes]:
    """Replicate the CURRENT production behaviour: fixed time-based chunks.

    recorder._record_loop emits a chunk every `_chunk_interval_frames` frames
    (frame = config.chunk_size samples), no overlap is used in the emitted audio.
    """
    frame_samples = 1024
    frame_bytes = frame_samples * 2
    interval_frames = max(1, int(chunk_seconds / (frame_samples / sr)))

    chunks = []
    frames = [raw[i:i + frame_bytes] for i in range(0, len(raw), frame_bytes)]
    buf: list[bytes] = []
    for idx, fr in enumerate(frames, start=1):
        buf.append(fr)
        if idx % interval_frames == 0 and buf:
            audio = b"".join(buf)
            if len(audio) >= min_chunk_bytes:
                chunks.append(audio)
            buf = []
    tail = b"".join(buf)
    if tail:
        chunks.append(tail)  # flush_remaining_chunk
    return chunks


def smart_chunks(raw: bytes, sr: int, config: Config) -> list[tuple[bytes, bool]]:
    """New silence-aware chunking (see turbo_whisper.chunking)."""
    from turbo_whisper.chunking import StreamingChunker
    chunker = StreamingChunker(config, sample_rate=sr)
    frame_bytes = config.chunk_size * 2
    out: list[tuple[bytes, bool]] = []
    for i in range(0, len(raw), frame_bytes):
        piece = chunker.add_frame(raw[i:i + frame_bytes])
        if piece:
            out.append(piece)
    tail = chunker.flush()
    if tail:
        out.append(tail)
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def normalize_words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower()).split()


def word_edit_distance(a: list[str], b: list[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, wa in enumerate(a, start=1):
        cur = [i]
        for j, wb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (wa != wb)))
        prev = cur
    return prev[-1]


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_words(a), normalize_words(b)).ratio()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(mode: str, limit: int, files: list[str] | None = None) -> None:
    config = Config.load()
    from turbo_whisper.api import WhisperClient
    from turbo_whisper.main import _clean_hallucination

    client = WhisperClient(config)
    if files:
        recordings = [config.get_recordings_dir() / f for f in files]
    else:
        recordings = sorted(config.get_recordings_dir().glob("*.wav"))[-limit:]

    out_lines: list[str] = []

    def log(s: str = "") -> None:
        print(s)
        out_lines.append(s)

    log(f"mode={mode}  model={config.model}  language={config.language}")
    log(f"chunk_duration_seconds={config.chunk_duration_seconds}  "
        f"min_chunk_bytes={config.min_chunk_bytes}")
    log(f"files: {[p.name for p in recordings]}")
    log()

    rows = []
    for path in recordings:
        raw, sr = load_wav(path)
        duration = len(raw) / (sr * 2)
        log(f"=== {path.name}  ({duration:.1f}s) ===")

        batch_text = client.transcribe_sync(raw_to_wav(raw, sr))
        log(f"  BATCH: {batch_text}")

        if mode == "legacy":
            pieces = [(c, False) for c in legacy_chunks(raw, sr, config.chunk_duration_seconds, config.min_chunk_bytes)]
        else:
            pieces = smart_chunks(raw, sr, config)

        log(f"  chunks: {len(pieces)} ({[round(len(p) / (sr * 2), 1) for p, _ in pieces]}s)")

        parts: list[str] = []
        prev_text = ""
        for i, (chunk_raw, had_overlap) in enumerate(pieces, start=1):
            chunk_wav = raw_to_wav(chunk_raw, sr)
            prompt = prev_text[-300:] if (mode == "smart" and prev_text) else None
            text = client.transcribe_sync(chunk_wav, prompt=prompt) if prompt else client.transcribe_sync(chunk_wav)
            cleaned = _clean_hallucination(text.strip()) if text and text.strip() else None
            if not cleaned:
                log(f"  chunk {i}: [filtered/empty] raw='{text[:60] if text else ''}'")
                continue
            if mode == "smart" and had_overlap and parts:
                from turbo_whisper.chunking import stitch_text
                stitched = stitch_text(prev_text, cleaned)
                if not stitched:
                    log(f"  chunk {i}: [deduped entirely]")
                    continue
                cleaned = stitched
            parts.append(cleaned)
            prev_text = (prev_text + " " + cleaned).strip()
            log(f"  chunk {i}: {cleaned}")

        stream_text = " ".join(parts).strip()

        wer = word_edit_distance(normalize_words(batch_text), normalize_words(stream_text)) / max(1, len(normalize_words(batch_text)))
        sim = similarity(batch_text, stream_text)
        log(f"  STREAM: {stream_text}")
        log(f"  WER={wer:.2%}  similarity={sim:.2%}")
        log()
        rows.append((path.name, wer, sim))

    log("=== SUMMARY ===")
    log(f"{'file':<28} {'WER':>8} {'similarity':>12}")
    for name, wer, sim in rows:
        log(f"{name:<28} {wer:>7.2%} {sim:>11.2%}")
    avg_wer = sum(r[1] for r in rows) / len(rows)
    avg_sim = sum(r[2] for r in rows) / len(rows)
    log(f"{'AVERAGE':<28} {avg_wer:>7.2%} {avg_sim:>11.2%}")

    (RESULTS_DIR / f"bench_results_{mode}.txt").write_text("\n".join(out_lines), encoding="utf-8")
    (RESULTS_DIR / f"bench_results_{mode}.json").write_text(
        json.dumps({"mode": mode, "rows": rows, "avg_wer": avg_wer, "avg_sim": avg_sim},
                   ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["legacy", "smart"], default="legacy")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--files", nargs="*", default=None,
                    help="Explicit recording filenames (in the recordings dir)")
    args = ap.parse_args()
    run(args.mode, args.limit, args.files)
