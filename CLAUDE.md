# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

Turbo Whisper is a SuperWhisper-like voice dictation application for Linux, macOS, and Windows. It provides a floating waveform UI with an animated orb that appears when the user presses a global hotkey, records audio, sends it to a Whisper API endpoint, and types the transcribed text into the focused window.

## Windows Adaptation

This project is fully adapted for reliable, error-free operation on Windows:
- **WinAPI hotkeys** via `RegisterHotKey` (WinApiHotkeyManager)
- **Multi-method clipboard paste**: keybd_event, WM_PASTE, SendInput, pynput, pyautogui
- **Process locking** via `msvcrt` (lock file based on temp directory)
- **Bat script** (`run_turbo_whisper.bat`) for launching from Explorer
- **PyAudio on Windows**: pre-built wheels via `pipwin`
- **Clipboard fallback chain**: pyperclip -> clip.exe

## Architecture

```
src/turbo_whisper/
├── main.py       # Application entry point, Qt app, system tray, settings panel
├── waveform.py   # Animated orb visualization widget (PyQt6)
├── icons.py      # Lucide SVG icons (power, copy, eye, chevron)
├── recorder.py   # Audio recording with PyAudio
├── api.py        # Whisper API client (OpenAI-compatible, incl. JSON API mode)
├── hotkey.py     # Global hotkey handling (WinAPI WinApiHotkeyManager on Windows, pynput on Linux/macOS)
├── typer.py      # Auto-type using WinAPI/PyAutoGUI (Windows), evdev (Linux), PyAutoGUI (macOS)
├── config.py     # Configuration management with history
├── winapi.py     # Windows-specific WinAPI helpers (RegisterHotKey, keybd_event, etc.)
├── clipboard.py  # Clipboard operations with multi-fallback (pyperclip -> clip.exe)
└── tray.py       # System tray with periodic refresh for Windows compatibility
```

## Development Commands

```bash
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Run the application
python -m turbo_whisper.main

# Install in development mode
pip install -e .

# Run tests
pytest
```

## Configuration

Config file: `%APPDATA%\turbo-whisper\config.json` (Windows)

Key settings:
- `api_url`: Whisper API endpoint
- `api_key`: API key
- `hotkey`: Key combination as list, e.g., `["~"]` for tilde
- `waveform_color`: Hex color for waveform bars
- `auto_paste`: Whether to auto-type transcription
- `use_json_api`: Enable JSON+base64 API mode (for RouterAI.ru)

## Killing All Processes

**Windows:**
```powershell
powershell -Command "Get-Process | Where-Object { $_.ProcessName -like '*turbo*whisper*' -or $_.CommandLine -like '*turbo*whisper*' } | Stop-Process -Force 2>&1; Remove-Item -Path \"$env:TEMP\turbo-whisper.lock\" -ErrorAction SilentlyContinue"
```
