"""Turbo Whisper - SuperWhisper-like voice dictation for Linux."""

from importlib.metadata import version, PackageNotFoundError

APP_VERSION = "1.1.0"

try:
    __version__ = version("turbo-whisper")
except PackageNotFoundError:
    __version__ = APP_VERSION  # Fallback for development/frozen builds
