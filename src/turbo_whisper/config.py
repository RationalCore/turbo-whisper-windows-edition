"""Configuration management for Turbo Whisper."""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TypedDict


def _default_hotkey() -> list[str]:
    """Return platform-appropriate default hotkey."""
    import sys
    if sys.platform == "win32":
        return ["f8"]
    else:
        return ["alt", "space"]


class HistoryEntry(TypedDict, total=False):
    """A history entry with text, timestamp, and optional audio file."""

    text: str
    timestamp: str  # ISO format
    audio_file: str  # Filename (not full path) of WAV recording


@dataclass
class Config:
    """Application configuration."""

    # API settings
    api_url: str = "https://routerai.ru/api/v1/audio/transcriptions"
    api_key: str = ""
    model: str = "openai/whisper-large-v3-turbo"
    use_json_api: bool = True  # True = JSON+base64 (routerai), False = multipart (OpenAI)

    # Hotkey settings (using pynput key names)
    hotkey: list[str] = field(default_factory=_default_hotkey)

    # Audio settings
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 1024
    input_device_index: int | str | None = None  # None = system default, str for PipeWire source ID
    input_device_name: str = ""  # For display purposes
    min_audio_bytes: int = 1000  # Minimum audio size before sending
    mic_gain: int = 100  # Microphone gain (0-200 percent)

    # UI settings
    waveform_color: str = "#84cc16"  # KnowAll.ai lime green
    background_color: str = "#1a1a2e"
    window_width: int = 520
    window_height: int = 260

    # Behavior
    auto_paste: bool = True
    copy_to_clipboard: bool = True
    auto_start: bool = True  # Auto-start Turbo Whisper on Windows login
    language: str = "ru"
    use_character_typing: bool = False  # False = clipboard paste (Ctrl+V), True = char-by-char
    typing_delay_ms: int = 5  # Milliseconds between keystrokes (only used if use_character_typing=True)

    # Notifications
    notification_cooldown: float = 2.5  # Seconds between notifications

    # Claude Code integration (disabled by default — enable in settings if you use Claude Code)
    claude_integration: bool = False
    claude_integration_port: int = 7878
    claude_wait_timeout: float = 30.0

    # History (recent transcriptions) - stored in separate history.json
    history: list[HistoryEntry] = field(default_factory=list)
    history_max: int = 20
    store_recordings: bool = True  # Save audio files with transcriptions

    # Streaming mode settings
    streaming_mode: bool = False  # Enable chunked streaming transcription
    silence_threshold_ms: int = 300  # Silence duration to trigger chunk (ms)
    silence_energy_threshold: float = 0.01  # Energy level below which is "silence"
    min_chunk_bytes: int = 2000  # Minimum chunk size to transcribe
    vad_aggressiveness: int = 1  # webrtcvad aggressiveness (0-3): 0=quality, 1=low bitrate, 2=default, 3=aggressive

    def get_recordings_dir(self) -> Path:
        """Get the directory for storing audio recordings."""
        import sys
        if sys.platform == "win32":
            config_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        recordings_dir = config_dir / "turbo-whisper" / "recordings"
        recordings_dir.mkdir(parents=True, exist_ok=True)
        return recordings_dir

    @classmethod
    def get_history_path(cls) -> Path:
        """Get the history file path (separate from config)."""
        import sys
        if sys.platform == "win32":
            config_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return config_dir / "turbo-whisper" / "history.json"

    def load_history(self) -> None:
        """Load history from separate history.json file."""
        history_path = self.get_history_path()
        if history_path.exists():
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Migrate old string-based history to new format
                migrated = []
                for entry in data:
                    if isinstance(entry, str):
                        migrated.append({"text": entry, "timestamp": ""})
                    else:
                        migrated.append(entry)
                self.history = migrated
            except (json.JSONDecodeError, Exception) as e:
                print(f"Warning: Could not load history: {e}")
                self.history = []

    def save_history(self) -> None:
        """Save history to separate history.json file with human-readable encoding."""
        history_path = self.get_history_path()
        history_path.parent.mkdir(parents=True, exist_ok=True)

        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)

    def add_to_history(self, text: str, audio_file: str | None = None) -> None:
        """Add a transcription to history.

        Args:
            text: The transcribed text
            audio_file: Optional filename of the WAV recording
        """
        if text and text.strip():
            # Remove if already exists (move to top) and delete old audio
            for i, entry in enumerate(self.history):
                entry_text = entry["text"] if isinstance(entry, dict) else entry
                if entry_text == text:
                    # Delete old audio file if it exists
                    old_audio = entry.get("audio_file") if isinstance(entry, dict) else None
                    if old_audio:
                        old_path = self.get_recordings_dir() / old_audio
                        if old_path.exists():
                            old_path.unlink()
                    self.history.pop(i)
                    break
            # Add to front with timestamp
            entry: HistoryEntry = {
                "text": text,
                "timestamp": datetime.now().isoformat(),
            }
            if audio_file:
                entry["audio_file"] = audio_file
            self.history.insert(0, entry)
            # Trim to max size and clean up old recordings
            self._cleanup_old_recordings()
            # Save to separate history.json file
            self.save_history()

    def _cleanup_old_recordings(self) -> None:
        """Remove old recordings beyond history_max limit."""
        # Get entries that will be removed
        removed_entries = self.history[self.history_max :]
        self.history = self.history[: self.history_max]

        # Delete audio files for removed entries
        recordings_dir = self.get_recordings_dir()
        for entry in removed_entries:
            if isinstance(entry, dict) and entry.get("audio_file"):
                audio_path = recordings_dir / entry["audio_file"]
                if audio_path.exists():
                    try:
                        audio_path.unlink()
                    except OSError:
                        pass  # Ignore errors deleting files

    @classmethod
    def get_config_path(cls) -> Path:
        """Get the configuration file path."""
        import sys
        if sys.platform == "win32":
            config_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return config_dir / "turbo-whisper" / "config.json"

    @staticmethod
    def _get_autostart_registry_key() -> str:
        """Get the Windows Registry path for startup programs.

        HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
        is a user-level key, no admin rights needed to modify.
        """
        return r"Software\Microsoft\Windows\CurrentVersion\Run"

    @staticmethod
    def _get_executable_path() -> str:
        """Get path to the current executable or script for autostart.

        For bundled exe returns its path; for development returns the batch file.
        """
        import sys as _sys
        if getattr(_sys, 'frozen', False):
            return _sys.executable
        # Fallback: use the project's run_turbo_whisper.bat if it exists
        project_bat = Path(__file__).resolve().parent.parent.parent.parent / "run_turbo_whisper.bat"
        if project_bat.exists():
            return str(project_bat)
        # Last resort: sys.executable + main module
        return _sys.executable

    def apply_autostart(self) -> None:
        """Enable or disable auto-start based on current config value.

        On Windows: creates/deletes HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run entry.
        On Linux: creates/removes .config/autostart .desktop file.
        On macOS: creates/removes launchd plist.

        Useful for daemon-type apps like Turbo Whisper that are always-on.
        """
        import sys as _sys
        if _sys.platform == "win32":
            self._apply_autostart_windows()
        elif _sys.platform == "linux":
            self._apply_autostart_linux()
        elif _sys.platform == "darwin":
            self._apply_autostart_macos()

    def _apply_autostart_windows(self) -> None:
        """Manage Windows Registry autostart entry."""
        try:
            import winreg
            key_path = self._get_autostart_registry_key()
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)

            app_name = "TurboWhisper"
            if self.auto_start:
                exe_path = self._get_executable_path()
                # Wrap in quotes if path contains spaces
                if " " in exe_path:
                    exe_path = f'"{exe_path}"'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
                print(f"Autostart enabled: {exe_path}")
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                    print("Autostart disabled")
                except FileNotFoundError:
                    pass  # Entry didn't exist
            winreg.CloseKey(key)
        except ImportError:
            print("winreg not available (not Windows?)")
        except Exception as e:
            print(f"Failed to set autostart: {e}")

    def _apply_autostart_linux(self) -> None:
        """Manage Linux autostart desktop file in ~/.config/autostart/."""
        autostart_dir = Path.home() / ".config" / "autostart"
        desktop_file = autostart_dir / "turbo-whisper.desktop"

        if self.auto_start:
            autostart_dir.mkdir(parents=True, exist_ok=True)
            exe_path = self._get_executable_path()
            content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Turbo Whisper\n"
                f"Exec={exe_path}\n"
                "X-GNOME-Autostart-enabled=true\n"
                "NoDisplay=true\n"
                "Terminal=false\n"
            )
            desktop_file.write_text(content, encoding="utf-8")
            print(f"Linux autostart enabled: {desktop_file}")
        else:
            if desktop_file.exists():
                desktop_file.unlink()
                print("Linux autostart disabled")

    def _apply_autostart_macos(self) -> None:
        """Manage macOS launchd plist for autostart."""
        launch_agents = Path.home() / "Library" / "LaunchAgents"
        plist_path = launch_agents / "com.turbowhisper.app.plist"

        if self.auto_start:
            launch_agents.mkdir(parents=True, exist_ok=True)
            exe_path = self._get_executable_path()
            plist_content = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n'
                "<dict>\n"
                "    <key>Label</key>\n"
                "    <string>com.turbowhisper.app</string>\n"
                "    <key>ProgramArguments</key>\n"
                "    <array>\n"
                f"        <string>{exe_path}</string>\n"
                "    </array>\n"
                "    <key>RunAtLoad</key>\n"
                "    <true/>\n"
                "    <key>KeepAlive</key>\n"
                "    <true/>\n"
                "</dict>\n"
                "</plist>\n"
            )
            plist_path.write_text(plist_content, encoding="utf-8")
            import subprocess as _sp
            _sp.run(["launchctl", "load", str(plist_path)], capture_output=True)
            print(f"macOS autostart enabled: {plist_path}")
        else:
            if plist_path.exists():
                import subprocess as _sp
                _sp.run(["launchctl", "unload", str(plist_path)], capture_output=True)
                plist_path.unlink()
                print("macOS autostart disabled")

    def apply_behavior_on_start(self) -> None:
        """Called once at app startup to apply behavior settings."""
        self.apply_autostart()

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from file or create default."""
        config_path = cls.get_config_path()
        config = cls()

        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Remove history from config data (it's in separate file now)
                data.pop("history", None)
                config = cls(**data)
            except (json.JSONDecodeError, TypeError) as e:
                print(f"Warning: Could not load config: {e}")

        # Load history from separate file
        config.load_history()
        return config

    def save(self) -> None:
        """Save configuration to file (without history)."""
        config_path = self.get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove history from config data before saving (history is in separate file)
        data = {k: v for k, v in self.__dict__.items() if k != "history"}

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
