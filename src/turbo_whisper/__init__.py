"""Turbo Whisper - SuperWhisper-like voice dictation for Linux."""

from importlib.metadata import version, PackageNotFoundError


def _detect_version() -> str:
    """Single version source: pyproject.toml (via package metadata when installed)."""
    # 1. Installed package metadata (pip/uv install, or PyInstaller builds
    #    that bundle the dist-info via copy_metadata)
    try:
        return version("turbo-whisper")
    except PackageNotFoundError:
        pass
    # 2. Running from a source checkout without installation
    try:
        import re
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        m = re.search(
            r'^version\s*=\s*"([^"]+)"',
            pyproject.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.0.0"


__version__ = _detect_version()
APP_VERSION = __version__
