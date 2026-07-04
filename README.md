<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/logo.svg">
    <img alt="Turbo Whisper" src="assets/logo.svg" width="100%">
  </picture>
</p>

Turbo Whisper is a **free, open source** voice dictation and transcription app for Linux, macOS, and Windows. A SuperWhisper alternative with a beautiful GUI for real-time speech to text (STT). Supports **99 languages** via OpenAI Whisper. Perfect for accessibility, RSI, and hands-free typing.

> **Windows:** Turbo Whisper is fully adapted for reliable, error-free operation on Windows. Uses native WinAPI for hotkeys (RegisterHotKey), multi-method clipboard paste (keybd_event, WM_PASTE, SendInput, pynput, pyautogui), and `msvcrt` process locking — no WSL or Cygwin required.

**Voice dictation** | **Speech to text (STT)** | **Voice typing** | **Transcription** | **Open source** | **Multilingual** | **Hands-free**

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)

## Features

- **Global hotkey** (Ctrl+Shift+Space) to start/stop recording from anywhere
- **Waveform visualization** - see your audio levels in real-time with an animated orb
- **OpenAI API compatible** - works with OpenAI Whisper API or self-hosted faster-whisper-server
- **Multilingual** - supports 99 languages via Whisper
- **Auto-type** - transcribed text is typed directly into the focused window
- **Clipboard support** - text is also copied to clipboard
- **System tray** - runs quietly in the background with autostart support
- **Cross-platform** - Linux, macOS, and Windows support
- **Accessibility** - great for RSI, carpal tunnel, or anyone preferring hands-free input

## Installation

### Windows

```powershell
# Clone the repository
git clone https://github.com/RationalCore/turbo-whisper-windows-edition.git
cd turbo-whisper-windows-edition

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -e .
pip install pyperclip  # Required for Windows clipboard/typing
```

> **Quick start:** Double-click `run_turbo_whisper.bat` in the project root to launch Turbo Whisper directly from Explorer.

## Configuration

Create `%APPDATA%\turbo-whisper\config.json` (Windows). For a full list of all available settings with their defaults, see [`config.example.json`](config.example.json).

**Single key — tilde (one-press recording):**
```json
{
  "api_url": "https://api.openai.com/v1/audio/transcriptions",
  "api_key": "sk-your-api-key",
  "hotkey": ["~"],
  "language": "en",
  "auto_paste": true,
  "copy_to_clipboard": true,
  "typing_delay_ms": 5,
  "waveform_color": "#00ff88",
  "background_color": "#1a1a2e"
}
```

> **Hotkey `~` (tilde) note:** The hotkey is bound to the physical key code, not to the layout character. On a Russian layout it's `ё`, on English — `` ` `` (backtick) with Shift — `~`, etc. Regardless of the active keyboard layout, the key will be handled correctly.

**Modifier combination — Ctrl+Shift+Space (to avoid conflicts):**
```json
{
  "api_url": "https://api.openai.com/v1/audio/transcriptions",
  "api_key": "sk-your-api-key",
  "hotkey": ["ctrl", "shift", "space"],
  "language": "en",
  "auto_paste": true,
  "copy_to_clipboard": true,
  "typing_delay_ms": 5,
  "waveform_color": "#00ff88",
  "background_color": "#1a1a2e"
}
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Credits

Inspired by [SuperWhisper](https://superwhisper.com/). This is a Windows-adapted fork focused on reliable, error-free operation on Windows using native WinAPI.
