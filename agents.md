# Turbo Whisper — Agent Guide

## Project Overview

Turbo Whisper is a free, open-source voice dictation and transcription app for Windows, Linux, and macOS. It provides real-time speech-to-text (STT) via OpenAI Whisper API with a PyQt6 GUI, global hotkeys, and auto-typing into any focused window.

- **Version:** 1.0.2
- **License:** MIT
- **Python:** 3.10+
- **Repo:** https://github.com/RationalCore/turbo-whisper-windows-edition

## Architecture

```
src/turbo_whisper/
├── main.py                 # App entry point, PyQt6 GUI (RecordingWindow), main loop
├── config.py               # Config dataclass, JSON persistence (~/.config/turbo-whisper/)
├── api.py                  # WhisperClient — HTTP API calls (httpx)
├── recorder.py             # AudioRecorder — PyAudio mic capture
├── hotkey.py               # WinAPI hotkey manager (RegisterHotKey, WH_KEYBOARD_LL)
├── typer.py                # Auto-type: clipboard paste, keybd_event, SendInput, pynput
├── silence.py              # VAD (webrtcvad) for streaming silence detection
├── waveform.py             # Waveform data processing
├── visualizer_process.py   # Separate process for floating waveform orb
├── floating_indicator.py   # FloatingIndicatorProcess — overlay window
├── integration_server.py   # HTTP server for Claude Code integration (localhost:7878)
├── icons.py                # SVG icon generators (tray, buttons)
└── __init__.py
```

## Key Dependencies

| Package | Purpose |
|---------|---------|
| PyQt6 | GUI framework (settings window, tray icon) |
| pyaudio | Microphone capture (requires PortAudio) |
| numpy | Audio buffer processing |
| httpx | HTTP client for Whisper API |
| pynput | Cross-platform keyboard simulation |
| pyautogui | Clipboard paste fallback |
| pyperclip | Windows clipboard access |

## Build System

- **Package manager:** uv (https://astral.sh/uv)
- **Build tool:** PyInstaller 6.x
- **LFS:** `*.exe` tracked via Git LFS

### Build Scripts

| Script | Description |
|--------|-------------|
| `build_exe.bat` | Standard one-file build |
| `build_exe_compat.bat` | One-file build without UPX/compression (compatibility) |
| `build_exe.spec` | PyInstaller spec — standard build |
| `build_exe_compat.spec` | PyInstaller spec — compatibility build |

### Building

```bash
# One-file exe (standard)
build_exe.bat

# One-file exe (compatibility — no compression)
build_exe_compat.bat
```

Output: `dist/TurboWhisper.exe` (~58 MB, single file)

### Build Prerequisites

1. Python 3.10+ in PATH
2. PortAudio at `C:\portaudio` (auto-built from source if missing)
3. uv package manager (auto-installed if missing)
4. Git (for PortAudio clone)

## Windows-Specific Details

- **Hotkeys:** Native WinAPI `RegisterHotKey` + `WH_KEYBOARD_LL` hook (requires admin for elevated windows)
- **Clipboard:** Multi-method fallback chain: pyperclip → clip.exe → keybd_event
- **Typing:** `KEYEVENTF_unicode` for full Cyrillic/Unicode support
- **Single instance:** `msvcrt` file lock
- **Admin elevation:** Auto-elevates via `ShellExecuteW("runas")`, `--no-admin` flag to skip
- **UAC manifest:** `runw.exe` bootloader (windowless)

## Config Location

- **Windows:** `%APPDATA%\turbo-whisper\config.json`
- **Linux/macOS:** `~/.config/turbo-whisper/config.json`
- **Logs:** `%APPDATA%\turbo-whisper\turbo-whisper.log`

## Common Tasks

### Run from source
```bash
uv sync
uv run python -m turbo_whisper.main
```

### Run without admin
```bash
dist\TurboWhisper.exe --no-admin
```

### Rebuild after code changes
```bash
build_exe_compat.bat
```

## Known Issues

- `WH_KEYBOARD_LL` hook fails silently on some systems → falls back to `RegisterHotKey` (no double-tap support)
- One-file exe first launch is slow (~5-10s extraction to temp)
- `console=False` hides runtime errors — use `console=True` in spec for debugging

## Graphify
If `graphify-out/graph.json` exists, use `graphify query` for codebase navigation
on complex tasks (understanding architecture, finding module connections, tracing
data flows). For simple edits to specific files - work directly without the graph.
