"""Local (offline) speech recognition for Turbo Whisper.

Engines (see docs/local-asr-benchmarks.md for benchmarks):
- Russian:     GigaAM v3 e2e_ctc (QUInt8 ONNX) - faster than the API on CPU
- Other langs: NVIDIA Parakeet TDT 0.6B v3 (sherpa-onnx int8), auto-detects

Models are downloaded on first use into the app's config dir (/models).

Heavy imports (onnxruntime, sherpa_onnx, sentencepiece) happen lazily so the
app starts instantly and works fine when local recognition is disabled.
"""

from __future__ import annotations

import io
import logging
import threading
import wave
import zipfile
from pathlib import Path

logger = logging.getLogger("turbo-whisper.local_asr")

GIGAAM_URL = ("https://github.com/RationalCore/turbo-whisper-windows-edition/"
              "releases/download/models-v1/gigaam-v3-ctc-quint8.zip")

PARAKEET_BASE = ("https://huggingface.co/csukuangfj/"
                 "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/resolve/main")
PARAKEET_FILES = ["encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"]

SAMPLE_RATE = 16000


# ── GigaAM log-mel feature extraction (numpy port of gigaam.preprocess) ───
# Model config (v3_e2e_ctc.yaml): features=64, win=320, hop=160, n_fft=320,
# center=false, mel_scale=htk, mel_norm=null.
_FFT, _WIN, _HOP, _NMELS = 320, 320, 160, 64


def _mel_filterbank() -> "np.ndarray":
    import numpy as np
    n_freqs = _FFT // 2 + 1
    all_freqs = np.linspace(0, SAMPLE_RATE // 2, n_freqs)
    m_min = 2595.0 * np.log10(1.0 + 0.0 / 700.0)
    m_max = 2595.0 * np.log10(1.0 + 8000.0 / 700.0)
    m_pts = np.linspace(m_min, m_max, _NMELS + 2)
    f_pts = 700.0 * (10 ** (m_pts / 2595.0) - 1.0)
    fdiff = np.diff(f_pts)
    ramps = f_pts[:, None] - all_freqs[None, :]
    down = -ramps[:-2] / fdiff[:-1, None]
    up = ramps[2:] / fdiff[1:, None]
    return np.maximum(0.0, np.minimum(down, up)).astype("float32")  # [64, 161]


_FBANK = None


def log_mel_features(samples) -> "np.ndarray":
    """float32 mono 16 kHz -> log-mel features [64, T] (like GigaAM/torchaudio)."""
    import numpy as np
    global _FBANK
    if _FBANK is None:
        _FBANK = _mel_filterbank()
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(_WIN) / _WIN)).astype("float32")
    n_frames = (len(samples) - _WIN) // _HOP + 1
    if n_frames < 1:
        n_frames = 1
        samples = np.pad(samples, (0, _WIN - len(samples)))
    idx = np.arange(_WIN)[None, :] + np.arange(n_frames)[:, None] * _HOP
    frames = samples[idx] * win
    spec = np.abs(np.fft.rfft(frames, n=_FFT, axis=1)) ** 2  # [T, 161]
    mel = spec @ _FBANK.T
    return np.log(np.clip(mel, 1e-9, 1e9)).T.astype("float32")  # [64, T]


def _wav_to_samples(wav_bytes: bytes):
    """WAV bytes (16k mono) -> float32 samples in [-1, 1]."""
    import numpy as np
    with wave.open(io.BytesIO(wav_bytes)) as w:
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype("float32") / 32768.0


# ── model download ─────────────────────────────────────────────────────────

def _models_root() -> Path:
    from turbo_whisper.config import Config
    root = Config.get_config_path().parent / "models"
    root.mkdir(parents=True, exist_ok=True)
    return root


class ModelNotDownloaded(Exception):
    pass


def _download(url: str, dest: Path, progress=None) -> None:
    import httpx
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 18):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
    tmp.replace(dest)


def ensure_gigaam(progress=None) -> Path:
    """Download/unpack the GigaAM model package; returns its directory."""
    root = _models_root() / "gigaam"
    marker = root / "v3_e2e_ctc_quint8.onnx"
    if marker.exists():
        return root
    root.mkdir(parents=True, exist_ok=True)
    zip_path = root / "gigaam.zip"
    if not zip_path.exists():
        logger.info("Downloading GigaAM model...")
        _download(GIGAAM_URL, zip_path, progress)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(root)
    zip_path.unlink()
    logger.info("GigaAM model ready in %s", root)
    return root


def ensure_parakeet(progress=None) -> Path:
    """Download the Parakeet sherpa-onnx package; returns its directory."""
    root = _models_root() / "parakeet"
    marker = root / "encoder.int8.onnx"
    if marker.exists():
        return root
    root.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(PARAKEET_FILES):
        dest = root / name
        if not dest.exists():
            logger.info("Downloading Parakeet model file %s...", name)
            _download(f"{PARAKEET_BASE}/{name}", dest,
                      (lambda p, i=i: progress and progress((i + p) / len(PARAKEET_FILES)))
                      if progress else None)
    logger.info("Parakeet model ready in %s", root)
    return root


# ── engines ────────────────────────────────────────────────────────────────

def _import_onnxruntime():
    """Import onnxruntime, fixing the PyInstaller stub-package issue.

    In frozen builds the root package comes from the PYZ archive and its
    submodules (onnxruntime.capi.*) are not reachable through it, leaving a
    stub without SessionOptions. The full package is bundled as data - load
    it explicitly when the stub is detected.
    """
    import sys
    import onnxruntime
    if hasattr(onnxruntime, "SessionOptions"):
        return onnxruntime
    import importlib.util
    from pathlib import Path
    base = Path(getattr(sys, "_MEIPASS", "")) / "onnxruntime"
    if base.exists():
        spec = importlib.util.spec_from_file_location(
            "onnxruntime", base / "__init__.py",
            submodule_search_locations=[str(base)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["onnxruntime"] = mod
        spec.loader.exec_module(mod)
        return mod
    return onnxruntime

class GigaAMEngine:
    """GigaAM v3 e2e_ctc (QUInt8 ONNX) for Russian. ~1.2s for 20s audio on CPU."""

    def __init__(self, model_dir: Path):
        rt = _import_onnxruntime()
        import sentencepiece as spm

        opts = rt.SessionOptions()
        opts.intra_op_num_threads = 8
        opts.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._sess = rt.InferenceSession(
            str(model_dir / "v3_e2e_ctc_quint8.onnx"),
            providers=["CPUExecutionProvider"], sess_options=opts)
        self._sp = spm.SentencePieceProcessor(
            model_file=str(model_dir / "tokenizer.model"))

    def transcribe(self, wav_bytes: bytes) -> str:
        import numpy as np
        samples = _wav_to_samples(wav_bytes)
        feats = log_mel_features(samples)  # [64, T]
        outs = self._sess.run(None, {
            "features": feats[None, :, :],
            "feature_lengths": np.array([feats.shape[1]], dtype=np.int64),
        })
        ids = outs[0].argmax(-1)[0].tolist()
        blank = outs[0].shape[-1] - 1  # CTC blank = vocab size (model-derived)
        keep, prev = [], None
        for i in ids:
            if i != blank and i != prev:
                keep.append(i)
            prev = i
        return self._sp.decode(keep)


class ParakeetEngine:
    """NVIDIA Parakeet TDT 0.6B v3 (sherpa-onnx int8) for 24 languages."""

    def __init__(self, model_dir: Path):
        import sherpa_onnx
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model_dir / "encoder.int8.onnx"),
            decoder=str(model_dir / "decoder.int8.onnx"),
            joiner=str(model_dir / "joiner.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=6,
            model_type="nemo_transducer",
            provider="cpu",
        )

    def transcribe(self, wav_bytes: bytes) -> str:
        samples = _wav_to_samples(wav_bytes)
        stream = self._recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()


# ── facade ─────────────────────────────────────────────────────────────────

class LocalASR:
    """Language-routed local recognition with lazy loading and warm-up."""

    def __init__(self):
        self._engines: dict[str, object] = {}
        self._lock = threading.Lock()

    @staticmethod
    def engine_for(language: str) -> str:
        return "gigaam" if language == "ru" else "parakeet"

    def transcribe(self, wav_bytes: bytes, language: str) -> str:
        name = self.engine_for(language)
        with self._lock:
            engine = self._engines.get(name)
            if engine is None:
                model_dir = ensure_gigaam() if name == "gigaam" else ensure_parakeet()
                engine = GigaAMEngine(model_dir) if name == "gigaam" else ParakeetEngine(model_dir)
                self._engines[name] = engine
                logger.info("Local ASR engine loaded: %s", name)
        return engine.transcribe(wav_bytes)

    def warmup(self, language: str) -> None:
        """Load the engine for the language in a background thread (startup)."""
        def _run():
            try:
                name = self.engine_for(language)
                logger.info("Local ASR warmup: engine=%s downloaded=%s",
                            name, self.is_downloaded(language))
                with self._lock:
                    if name not in self._engines and self.is_downloaded(language):
                        logger.info("Local ASR warmup: loading %s...", name)
                        model_dir = ensure_gigaam() if name == "gigaam" else ensure_parakeet()
                        self._engines[name] = (GigaAMEngine(model_dir)
                                               if name == "gigaam" else ParakeetEngine(model_dir))
                        logger.info("Local ASR warmed up: %s", name)
            except Exception as e:
                logger.info("Warmup skipped: %r", e)
        threading.Thread(target=_run, daemon=True).start()

    @staticmethod
    def is_downloaded(language: str) -> bool:
        name = LocalASR.engine_for(language)
        if name == "gigaam":
            return (_models_root() / "gigaam" / "v3_e2e_ctc_quint8.onnx").exists()
        return (_models_root() / "parakeet" / "encoder.int8.onnx").exists()

    def ensure_for(self, language: str, progress=None) -> None:
        if self.engine_for(language) == "gigaam":
            ensure_gigaam(progress)
        else:
            ensure_parakeet(progress)


# module-level singleton used by the app
_instance: LocalASR | None = None


def get_local_asr() -> LocalASR:
    global _instance
    if _instance is None:
        _instance = LocalASR()
    return _instance
