"""Main application entry point for Turbo Whisper."""

import logging
import os
import subprocess
import sys
import tempfile
import threading
import time

# Platform-specific imports for single-instance locking
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSlider,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .api import WhisperAPIError, WhisperClient
from .config import Config
from .hotkey import create_hotkey_manager
from .icons import (
    get_check_icon,
    get_chevron_down_icon,
    get_chevron_up_icon,
    get_close_icon,
    get_copy_icon,
    get_eye_icon,
    get_eye_off_icon,
    get_play_icon,
    get_stop_icon,
    get_tray_icon,
)
from .recorder import AudioRecorder
from .typer import Typer
from .waveform import WaveformWidget

logger = logging.getLogger("turbo-whisper.main")


class SignalBridge(QObject):
    """Bridge for thread-safe Qt signals."""

    toggle_recording = pyqtSignal()
    update_waveform = pyqtSignal(float, list)
    transcription_complete = pyqtSignal(str)
    transcription_error = pyqtSignal(str)
    show_status = pyqtSignal(str)


class TickMarksWidget(QWidget):
    """Widget that draws tick mark notches for a slider."""

    def __init__(self, num_ticks: int = 11, parent=None):
        super().__init__(parent)
        self.num_ticks = num_ticks
        self.setFixedHeight(6)

    def paintEvent(self, event):
        from PyQt6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(QColor("#666"))
        pen.setWidth(1)
        painter.setPen(pen)

        width = self.width()
        padding = 8
        usable_width = width - 2 * padding

        for i in range(self.num_ticks):
            x = padding + int(i * usable_width / (self.num_ticks - 1))
            if i == 5:
                painter.drawLine(x, 0, x, 5)
            else:
                painter.drawLine(x, 2, x, 5)

        painter.end()


class RecordingWindow(QWidget):
    """Floating window showing waveform during recording."""

    cancel_requested = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self._drag_pos = None
        self._setup_ui()

        self._claude_status_timer = QTimer()
        self._claude_status_timer.timeout.connect(self._update_claude_status)
        self._claude_status_timer.setInterval(1000)

    def _setup_ui(self) -> None:
        """Set up the recording window UI."""
        self.setWindowIcon(get_tray_icon(128, recording=False))

        # Use ToolTip type which never takes focus on any platform
        self._base_window_flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.ToolTip
        )
        self.setWindowFlags(self._base_window_flags | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_X11DoNotAcceptFocus)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._resize_edge = None

        container = QWidget(self)
        container.setObjectName("container")
        container.setStyleSheet(
            """
            #container {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #2d1b4e, stop:0.5 #1a1033, stop:1 #0f0a1a);
                border-radius: 12px;
                border: 1px solid #4a3070;
            }
        """
        )

        from PyQt6.QtWidgets import QFrame

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        content_frame = QFrame()
        content_frame.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content_frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        container_layout.addWidget(content_frame)

        self.waveform = WaveformWidget(
            color="#84cc16",
            bg_color=self.config.background_color,
        )
        self.waveform.setMinimumHeight(160)
        layout.addWidget(self.waveform, stretch=2)

        status_widget = QWidget()
        status_widget.setStyleSheet("background: transparent;")
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(4, 0, 4, 0)

        self.status_label = QLabel("Listening...")
        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()

        self._hotkey_str = "+".join(k.title() for k in self.config.hotkey)
        self.hints_label = QLabel(f"Start: {self._hotkey_str}")
        self.hints_label.setStyleSheet("color: #666; font-size: 10px;")
        status_layout.addWidget(self.hints_label)

        self._status_dots = 0
        self._status_timer = QTimer()
        self._status_timer.timeout.connect(self._animate_status)
        self._status_timer.setInterval(400)
        layout.addWidget(status_widget)

        self.settings_btn = QPushButton()
        self.settings_btn.setIcon(get_chevron_down_icon(20, "#84cc16"))
        self.settings_btn.setFixedSize(40, 28)
        self.settings_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.settings_btn.setStyleSheet(
            "QPushButton { background: rgba(132, 204, 22, 0.1); "
            "border: 1px solid rgba(132, 204, 22, 0.3); border-radius: 6px; }"
            "QPushButton:hover { background: rgba(132, 204, 22, 0.2); }"
        )
        self.settings_btn.clicked.connect(self._toggle_settings)
        layout.addWidget(self.settings_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.settings_panel = self._build_settings_panel()
        self.settings_panel.hide()
        layout.addWidget(self.settings_panel)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        self.close_btn = QPushButton(container)
        self.close_btn.setIcon(get_close_icon(14, "#666666"))
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setToolTip("Close")
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.close_btn.clicked.connect(self._close_window)
        self.close_btn.enterEvent = lambda e: self.close_btn.setIcon(get_close_icon(14, "#84cc16"))
        self.close_btn.leaveEvent = lambda e: self.close_btn.setIcon(get_close_icon(14, "#666666"))
        self.close_btn.move(self.config.window_width - 28, 8)
        self.close_btn.raise_()

        self.version_label = QLabel("v1.0.0", container)
        self.version_label.setStyleSheet("color: #666; font-size: 10px;")
        self.version_label.move(12, 8)

        self.setFixedSize(self.config.window_width, self.config.window_height)

    def _build_settings_panel(self):
        """Build the collapsible settings panel."""
        from PyQt6.QtWidgets import QComboBox, QLineEdit, QListWidget, QPushButton, QSlider

        panel = QWidget()
        panel.setStyleSheet("""
            QWidget { background-color: rgba(0, 0, 0, 0.3); border-radius: 8px; }
            QLabel { color: #888; font-size: 10px; }
            QLineEdit { background-color: rgba(255, 255, 255, 0.1);
                border: 1px solid #4a3070; border-radius: 4px; color: #fff;
                padding: 6px; font-size: 11px; }
            QSlider::groove:horizontal { background: #333; height: 6px; border-radius: 3px; }
            QSlider::handle:horizontal { background: #84cc16; width: 14px;
                margin: -4px 0; border-radius: 7px; }
            QComboBox { background-color: rgba(255, 255, 255, 0.1);
                border: 1px solid #4a3070; border-radius: 4px; color: #fff;
                padding: 6px; font-size: 11px; }
            QComboBox::drop-down { border: none; }
            QListWidget { background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid #4a3070; border-radius: 4px; color: #ccc; font-size: 11px; }
        """)

        s = QVBoxLayout(panel)
        s.setContentsMargins(12, 8, 12, 8)
        s.setSpacing(6)

        # API URL
        s.addWidget(QLabel("API URL"))
        self.api_url_input = QLineEdit(self.config.api_url)
        self.api_url_input.setPlaceholderText("https://api.openai.com/v1/audio/transcriptions")
        s.addWidget(self.api_url_input)

        # API Key
        s.addWidget(QLabel("API Key"))
        key_row = QHBoxLayout()
        self._actual_api_key = self.config.api_key
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("sk-...")
        self._key_visible = False
        self._update_api_key_display()
        self.api_key_input.textChanged.connect(self._on_api_key_changed)
        key_row.addWidget(self.api_key_input)

        self.key_visible_btn = QPushButton()
        self.key_visible_btn.setIcon(get_eye_icon(16, "#888888"))
        self.key_visible_btn.setFixedSize(28, 28)
        self.key_visible_btn.setToolTip("Show/hide API key")
        self.key_visible_btn.setStyleSheet("QPushButton { background: transparent; border: 1px solid #555; border-radius: 4px; }")
        self.key_visible_btn.clicked.connect(self._toggle_key_visibility)
        key_row.addWidget(self.key_visible_btn)

        self.key_copy_btn = QPushButton()
        self.key_copy_btn.setIcon(get_copy_icon(16, "#888888"))
        self.key_copy_btn.setFixedSize(28, 28)
        self.key_copy_btn.setToolTip("Copy to clipboard")
        self.key_copy_btn.setStyleSheet("QPushButton { background: transparent; border: 1px solid #555; border-radius: 4px; }")
        self.key_copy_btn.clicked.connect(lambda: self._copy_to_clipboard(self._actual_api_key, self.key_copy_btn))
        key_row.addWidget(self.key_copy_btn)
        s.addLayout(key_row)

        # Microphone
        s.addWidget(QLabel("Microphone"))
        self.mic_combo = QComboBox()
        self._populate_mic_dropdown()
        s.addWidget(self.mic_combo)

        # Gain slider
        gain_row = QHBoxLayout()
        self.gain_label = QLabel("Mic Gain:")
        self.gain_value_label = QLabel("100%")
        self.gain_value_label.setStyleSheet("color: #84cc16; font-weight: bold;")
        gain_row.addWidget(self.gain_label)
        gain_row.addStretch()
        gain_row.addWidget(self.gain_value_label)
        s.addLayout(gain_row)

        self.sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(0, 200)
        self.sensitivity_slider.setValue(100)
        self.sensitivity_slider.setSingleStep(20)
        self.sensitivity_slider.setPageStep(20)
        self.sensitivity_slider.valueChanged.connect(self._on_sensitivity_changed)
        self._current_mic_level = 0
        self._update_sensitivity_style()
        s.addWidget(self.sensitivity_slider)

        # History
        s.addWidget(QLabel("Recent Clips"))
        self.history_list = QListWidget()
        self.history_list.setMinimumHeight(160)
        self.history_list.setMaximumHeight(250)
        self._refresh_history()
        s.addWidget(self.history_list)

        # Save
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setStyleSheet(
            "QPushButton { background-color: #84cc16; color: #000; border: none; "
            "border-radius: 4px; font-size: 11px; font-weight: bold; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #9ae62a; }"
        )
        self.save_btn.clicked.connect(self._save_settings)
        s.addWidget(self.save_btn)

        return panel

    def update_icon(self, recording: bool) -> None:
        self.setWindowIcon(get_tray_icon(128, recording=recording))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
        else:
            super().keyPressEvent(event)

    def set_status(self, text: str, animate: bool = False) -> None:
        self._base_status = text
        self._status_dots = 0
        self.status_label.setText(text)
        if animate:
            self._status_timer.start()
        else:
            self._status_timer.stop()

    def set_recording_hint(self, recording: bool) -> None:
        action = "Stop" if recording else "Start"
        self.hints_label.setText(f"{action}: {self._hotkey_str}")

    def update_mic_level(self, level: float) -> None:
        if abs(level - self._current_mic_level) > 0.01 or level == 0:
            self._current_mic_level = level
            self._update_sensitivity_style()

    def _animate_status(self) -> None:
        self._status_dots = (self._status_dots + 1) % 4
        dots = "." * self._status_dots
        self.status_label.setText(f"{self._base_status}{dots}")

    def _toggle_settings(self) -> None:
        if self.settings_panel.isVisible():
            self.settings_panel.hide()
            self.settings_btn.setIcon(get_chevron_down_icon(20, "#84cc16"))
            self.setFixedSize(self.config.window_width, self.config.window_height)
            self._claude_status_timer.stop()
            self.setWindowFlags(self._base_window_flags | Qt.WindowType.WindowDoesNotAcceptFocus)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.show()
        else:
            self.settings_panel.show()
            self.settings_btn.setIcon(get_chevron_up_icon(20, "#84cc16"))
            self.setFixedSize(self.config.window_width, self.config.window_height + 480)
            self._update_claude_status()
            self._claude_status_timer.start()
            self.setWindowFlags(self._base_window_flags)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.show()
            self.activateWindow()

    def _update_api_key_display(self) -> None:
        self.api_key_input.blockSignals(True)
        if self._key_visible:
            self.api_key_input.setText(self._actual_api_key)
            self.api_key_input.setReadOnly(False)
        else:
            mask = "●" * len(self._actual_api_key) if self._actual_api_key else ""
            self.api_key_input.setText(mask)
            self.api_key_input.setReadOnly(True)
        self.api_key_input.blockSignals(False)

    def _on_api_key_changed(self, text: str) -> None:
        if self._key_visible:
            self._actual_api_key = text

    def _toggle_key_visibility(self) -> None:
        self._key_visible = not self._key_visible
        self._update_api_key_display()
        if self._key_visible:
            self.key_visible_btn.setIcon(get_eye_off_icon(16, "#888888"))
        else:
            self.key_visible_btn.setIcon(get_eye_icon(16, "#888888"))

    def _copy_to_clipboard(self, text: str, button: QPushButton = None) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        if button:
            original_icon = button.icon()
            button.setIcon(get_check_icon(16, "#84cc16"))
            QTimer.singleShot(1500, lambda: button.setIcon(original_icon))

    def _on_sensitivity_changed(self, value: int) -> None:
        snapped = round(value / 20) * 20
        if snapped != value:
            self.sensitivity_slider.blockSignals(True)
            self.sensitivity_slider.setValue(snapped)
            self.sensitivity_slider.blockSignals(False)
            value = snapped
        self.waveform.sensitivity = value
        self.gain_value_label.setText(f"{value}%")
        self._update_sensitivity_style()

    def _update_sensitivity_style(self) -> None:
        gain = self.sensitivity_slider.value() / 100.0
        gained_level = min(1.0, self._current_mic_level * gain * 5)
        level_pct = int(gained_level * 100)
        self.sensitivity_slider.setStyleSheet(
            f"""
            QSlider::groove:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #84cc16, stop:{level_pct / 100:.2f} #84cc16,
                    stop:{min(1.0, level_pct / 100 + 0.01):.2f} #333, stop:1 #333);
                height: 8px; border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: #fff; width: 16px; height: 16px;
                margin: -5px 0; border-radius: 8px; border: 2px solid #84cc16;
            }}
            QSlider {{ height: 24px; }}
        """
        )

    def _populate_mic_dropdown(self) -> None:
        import sys
        from .recorder import get_pipewire_sources

        self.mic_combo.clear()
        self.mic_combo.addItem("System Default", None)

        if sys.platform.startswith("linux"):
            pw_sources = get_pipewire_sources()
            if pw_sources:
                for src in pw_sources:
                    self.mic_combo.addItem(f"{src['description']} (48000Hz)", src["id"])
                return

        import pyaudio
        try:
            audio = pyaudio.PyAudio()
            for i in range(audio.get_device_count()):
                try:
                    info = audio.get_device_info_by_index(i)
                    if info["maxInputChannels"] > 0 and info["maxOutputChannels"] == 0:
                        self.mic_combo.addItem(f"{info['name']} ({int(info['defaultSampleRate'])}Hz)", i)
                except Exception:
                    pass
            audio.terminate()
        except Exception as e:
            print(f"Could not enumerate audio devices: {e}")

        if self.config.input_device_index is not None:
            for i in range(self.mic_combo.count()):
                if self.mic_combo.itemData(i) == self.config.input_device_index:
                    self.mic_combo.setCurrentIndex(i)
                    break

    def _save_settings(self) -> None:
        self.config.api_url = self.api_url_input.text()
        self.config.api_key = self._actual_api_key
        self.config.input_device_index = self.mic_combo.currentData()
        self.config.input_device_name = self.mic_combo.currentText()
        self.config.save()
        self.save_btn.setText("✓ Saved!")
        QTimer.singleShot(1500, lambda: self.save_btn.setText("Save Settings"))

    def _update_claude_status(self) -> None:
        if not self.config.claude_integration:
            self.claude_status.setText("Disabled")
            self.claude_status.setStyleSheet("color: #666; font-size: 11px;")
            return
        try:
            import json
            import urllib.request

            req = urllib.request.Request(
                f"http://127.0.0.1:{self.config.claude_integration_port}/status", method="GET",
            )
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                data = json.loads(resp.read().decode())
                age = data.get("last_signal_age", 999)
                if age < 30:
                    self.claude_status.setText("Ready")
                    self.claude_status.setStyleSheet("color: #84cc16; font-size: 11px;")
                else:
                    self.claude_status.setText("Busy")
                    self.claude_status.setStyleSheet("color: #f59e0b; font-size: 11px;")
        except Exception:
            self.claude_status.setText("Server error")
            self.claude_status.setStyleSheet("color: #f59e0b; font-size: 11px;")

    def _refresh_history(self) -> None:
        self.history_list.clear()
        for entry in self.config.history:
            if isinstance(entry, dict):
                text = entry.get("text", "")
                timestamp = entry.get("timestamp", "")
                audio_file = entry.get("audio_file", "")
            else:
                text = entry
                timestamp = ""
                audio_file = ""

            time_str = ""
            if timestamp:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(timestamp)
                    time_str = dt.strftime("%b %d %H:%M") + " "
                except ValueError:
                    pass

            widget = QWidget()
            lay = QHBoxLayout(widget)
            lay.setContentsMargins(4, 2, 4, 2)
            display = text[:40] + "..." if len(text) > 40 else text
            label = QLabel(f"{time_str}{display}")
            label.setStyleSheet("color: #ccc; font-size: 11px;")
            label.setToolTip(text)
            lay.addWidget(label, stretch=1)

            copy_btn = QPushButton()
            copy_btn.setIcon(get_copy_icon(14, "#888"))
            copy_btn.setFixedSize(24, 24)
            copy_btn.setStyleSheet("QPushButton { background: transparent; border: none; border-radius: 4px; }")
            copy_btn.clicked.connect(lambda checked, t=text: self._copy_history_item(t))
            lay.addWidget(copy_btn)

            if audio_file:
                play_btn = QPushButton()
                play_btn.setIcon(get_play_icon(14, "#888"))
                play_btn.setFixedSize(24, 24)
                play_btn.setStyleSheet("QPushButton { background: transparent; border: none; border-radius: 4px; }")
                play_btn.clicked.connect(lambda checked, f=audio_file, b=play_btn: self._play_audio(f, b))
                lay.addWidget(play_btn)

            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self.history_list.addItem(item)
            self.history_list.setItemWidget(item, widget)

    def _copy_history_item(self, text: str) -> None:
        self._copy_to_clipboard(text)
        self.set_status("Copied!")

    def _play_audio(self, filename: str, button: QPushButton) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

        if hasattr(self, "_playing_button") and self._playing_button == button:
            self._media_player.stop()
            button.setIcon(get_play_icon(14, "#888"))
            button.setToolTip("Play recording")
            self._playing_button = None
            return

        audio_path = self.config.get_recordings_dir() / filename
        if not audio_path.exists():
            self.set_status("Audio file not found")
            return

        if not hasattr(self, "_media_player"):
            self._media_player = QMediaPlayer()
            self._audio_output = QAudioOutput()
            self._media_player.setAudioOutput(self._audio_output)
            self._media_player.playbackStateChanged.connect(self._on_playback_state_changed)

        if hasattr(self, "_playing_button") and self._playing_button:
            self._playing_button.setIcon(get_play_icon(14, "#888"))
            self._playing_button.setToolTip("Play recording")
        self._media_player.stop()

        button.setIcon(get_stop_icon(14, "#888"))
        button.setToolTip("Stop playback")
        self._playing_button = button
        self._media_player.setSource(QUrl.fromLocalFile(str(audio_path)))
        self._audio_output.setVolume(1.0)
        self._media_player.play()

    def _on_playback_state_changed(self, state) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        if state == QMediaPlayer.PlaybackState.StoppedState:
            if hasattr(self, "_playing_button") and self._playing_button:
                self._playing_button.setIcon(get_play_icon(14, "#888"))
                self._playing_button.setToolTip("Play recording")
                self._playing_button = None

    def _close_window(self) -> None:
        self.cancel_requested.emit()
        self.hide()

    def center_on_screen(self) -> None:
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = int(screen.height() * 0.3)
        self.move(x, y)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if hasattr(self.windowHandle(), "startSystemMove"):
                self.windowHandle().startSystemMove()
            else:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None


class TurboWhisper:
    """Main application class."""

    def __init__(self):
        self.config = Config.load()
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setWindowIcon(get_tray_icon(128, recording=False))

        self.recorder = AudioRecorder(self.config)
        self.client = WhisperClient(self.config)
        self.typer = Typer(
            typing_delay_ms=self.config.typing_delay_ms,
            use_character_typing=self.config.use_character_typing,
        )
        self.signals = SignalBridge()

        self.window = RecordingWindow(self.config)
        self._setup_tray()

        self.is_recording = False
        self.is_processing = False
        self._pending_waveform_data = None
        self._last_notification_time = 0.0
        self._processing_watchdog = QTimer()
        self._processing_watchdog.setSingleShot(True)
        self._processing_watchdog.timeout.connect(self._on_processing_timeout)
        self._processing_thread = None

        self.signals.toggle_recording.connect(self._toggle_recording)
        self.signals.transcription_complete.connect(self._on_transcription_complete)
        self.signals.transcription_error.connect(self._on_transcription_error)
        self.signals.show_status.connect(self.window.set_status)
        self.window.cancel_requested.connect(self._cancel_recording)

        self._waveform_timer = QTimer()
        self._waveform_timer.timeout.connect(self._poll_waveform_data)
        self._waveform_timer.setInterval(30)

        # Hotkey callback - PyQt6 signals are thread-safe, emit() from any thread
        # queues the call on the Qt main thread automatically
        self.hotkey_manager = create_hotkey_manager(
            self.config.hotkey,
            lambda: self.signals.toggle_recording.emit(),
        )
        if self.hotkey_manager is None:
            print("Warning: Global hotkeys not available on this platform")

        self.integration_server = None
        if self.config.claude_integration:
            from .integration_server import IntegrationServer
            self.integration_server = IntegrationServer(self.config.claude_integration_port)
            if not self.integration_server.start():
                self.integration_server = None

        # Periodic tray refresh to prevent Windows from hiding the icon
        self._tray_refresh_timer = QTimer()
        self._tray_refresh_timer.timeout.connect(self._refresh_tray_icon)
        self._tray_refresh_timer.setInterval(10000)  # every 10 seconds
        self._tray_refresh_timer.start()

    def _refresh_tray_icon(self) -> None:
        """Periodically re-show tray icon to prevent Windows from hiding it.
        
        Hides and shows the icon with a short delay to ensure Windows
        doesn't optimize away the visibility toggle.
        """
        self.tray.setVisible(False)
        QTimer.singleShot(200, lambda: self.tray.setVisible(True))

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.app)
        self.tray.setIcon(get_tray_icon(64, recording=False))
        hotkey_str = "+".join(k.capitalize() for k in self.config.hotkey)
        self.tray.setToolTip(f"Turbo Whisper - Press {hotkey_str} to dictate")

        # Make tray icon always visible (Windows may hide it by default)
        self.tray.setVisible(True)

        menu = QMenu()

        show_action = QAction("Show Window", menu)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        self.toggle_action = QAction("Start Recording", menu)
        self.toggle_action.triggered.connect(self._toggle_recording)
        menu.addAction(self.toggle_action)

        menu.addSeparator()

        settings_action = QAction("Settings...", menu)
        settings_action.triggered.connect(self._show_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_window()

    def _update_icons(self, recording: bool) -> None:
        self.tray.setIcon(get_tray_icon(64, recording=recording))
        self.tray.setVisible(True)
        self.window.update_icon(recording=recording)

    def _on_processing_timeout(self) -> None:
        """Watchdog: if processing takes >30s, reset flag to unblock hotkey."""
        if self.is_processing:
            print("_on_processing_timeout: processing watchdog triggered, resetting flag")
            self.is_processing = False
            self._show_notification("Turbo Whisper", "Processing timed out, you can record again",
                                    QSystemTrayIcon.MessageIcon.Warning)

    def _save_wav(self, path, audio_data: bytes) -> None:
        import wave
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(self.config.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.config.sample_rate)
            wf.writeframes(audio_data)

    def _show_window(self) -> None:
        self.window.waveform.set_recording(False)
        self.window.set_recording_hint(recording=False)
        self._update_icons(recording=False)
        self.window.set_status("Ready", animate=False)
        self.window.center_on_screen()
        self.window.show()
        self.window.raise_()

    def _show_settings(self) -> None:
        self._show_window()
        if not self.window.settings_panel.isVisible():
            self.window._toggle_settings()
        self.window.activateWindow()

    def _toggle_recording(self) -> None:
        now = time.time()
        if hasattr(self, '_last_toggle') and (now - self._last_toggle) < 0.25:
            return
        self._last_toggle = now

        if self.is_processing:
            thread = self._processing_thread
            if thread is not None and thread.is_alive():
                print(f"_toggle_recording: still processing, rejecting toggle at {now:.3f}")
                self._show_notification("Turbo Whisper", "Still processing, please wait...",
                                        QSystemTrayIcon.MessageIcon.Information)
                return
            else:
                print("_toggle_recording: processing flag stuck, resetting")
                self.is_processing = False
                self._processing_watchdog.stop()
        if self.is_recording:
            print(f"_toggle_recording: is_recording=True -> _stop_recording at {now:.3f}")
            self._stop_recording()
        else:
            print(f"_toggle_recording: is_recording=False -> _start_recording at {now:.3f}")
            self._start_recording()

    def _start_recording(self) -> None:
        if self.is_recording:
            print("_start_recording: already recording, skipping")
            return
        print("_start_recording: starting...")
        self.is_recording = True
        self.toggle_action.setText("Stop Recording")
        self._update_icons(recording=True)

        # Capture the foreground window handle BEFORE recording starts
        # This ensures we paste into the correct window after recording
        if sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    self.typer.set_target_window(hwnd)
                    logger.info(f"_start_recording: captured foreground hwnd={hwnd}")
            except Exception as e:
                print(f"_start_recording: failed to capture foreground hwnd: {e}")

        # Don't show window — only indicate via tray icon (green = recording)
        # This avoids stealing focus and keeps pynput working
        self._pending_waveform_data = None
        self._waveform_timer.start()
        try:
            self.recorder.start(level_callback=self._on_audio_level)
            print("_start_recording: recorder started successfully")
        except Exception as e:
            print(f"_start_recording FAILED: {e}")
            self.is_recording = False
            self.toggle_action.setText("Start Recording")
            self._update_icons(recording=False)
            self._waveform_timer.stop()
            self._show_notification("Turbo Whisper", f"Microphone error: {e}", QSystemTrayIcon.MessageIcon.Critical)

    def _cancel_recording(self) -> None:
        if not self.is_recording:
            return
        self.is_recording = False
        self.toggle_action.setText("Start Recording")
        self._update_icons(recording=False)
        self._waveform_timer.stop()
        self.recorder.stop()
        self._show_notification("Turbo Whisper", "Recording cancelled", QSystemTrayIcon.MessageIcon.Information)

    def _stop_recording(self) -> None:
        if not self.is_recording:
            print("_stop_recording: not recording, returning")
            return
        self.is_recording = False
        self.toggle_action.setText("Start Recording")
        self._update_icons(recording=False)
        self._waveform_timer.stop()

        self.window.set_status("Processing", animate=True)
        audio_data = self.recorder.stop()
        print(f"_stop_recording: got {len(audio_data)} bytes of audio")

        if len(audio_data) < self.config.min_audio_bytes:
            print(f"_stop_recording: too short ({len(audio_data)} < {self.config.min_audio_bytes}), aborting")
            self._show_notification("Turbo Whisper", "Recording too short", QSystemTrayIcon.MessageIcon.Warning)
            return

        self.is_processing = True
        self._processing_watchdog.start(30000)
        print(f"_stop_recording: starting transcription thread, audio={len(audio_data)} bytes")

        audio_filename = None
        if self.config.store_recordings and audio_data:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            audio_filename = f"{timestamp}.wav"
            audio_path = self.config.get_recordings_dir() / audio_filename
            try:
                self._save_wav(audio_path, audio_data)
            except Exception as e:
                print(f"Warning: Could not save audio: {e}")
                audio_filename = None

        self._pending_audio_filename = audio_filename

        def transcribe():
            try:
                text = self.client.transcribe_sync(audio_data)
                self.signals.transcription_complete.emit(text)
            except WhisperAPIError as e:
                self.signals.transcription_error.emit(str(e))
            except Exception as e:
                self.signals.transcription_error.emit(str(e))

        self._processing_thread = threading.Thread(target=transcribe, daemon=True)
        self._processing_thread.start()

    def _on_audio_level(self, level: float, waveform_buffer: list[float]) -> None:
        self._pending_waveform_data = (level, list(waveform_buffer))

    def _poll_waveform_data(self) -> None:
        if self._pending_waveform_data is not None:
            level, waveform_buffer = self._pending_waveform_data
            self.window.waveform.update_waveform(level, waveform_buffer)
            self.window.update_mic_level(level)

    def _is_claude_running(self) -> bool:
        try:
            result = subprocess.run(["pgrep", "-x", "claude"], capture_output=True, timeout=1)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _wait_for_claude_ready(self) -> bool:
        if not self.config.claude_integration or not self.integration_server:
            return True
        if not self._is_claude_running():
            return True
        from .integration_server import IntegrationServer
        if IntegrationServer.is_ready(max_age=30.0):
            IntegrationServer.reset_ready()
            return True
        timeout = self.config.claude_wait_timeout
        start = time.time()
        while (time.time() - start) < timeout:
            if IntegrationServer.is_ready(max_age=1.0):
                IntegrationServer.reset_ready()
                return True
            time.sleep(0.1)
        return False

    def _show_notification(self, title: str, message: str, icon: QSystemTrayIcon.MessageIcon,
                          duration_ms: int = 2000) -> None:
        now = time.time()
        cooldown = self.config.notification_cooldown

        if title.startswith("Copied") and len(message) > 10:
            if not self._last_notification_time > 0:
                self._last_notification_time = now
                self.tray.showMessage(title, message, icon, duration_ms)
            return
        if (now - self._last_notification_time) < cooldown:
            return
        self._last_notification_time = now
        self.tray.showMessage(title, message, icon, duration_ms)

    def _on_transcription_complete(self, text: str) -> None:
        self.is_processing = False
        self._processing_watchdog.stop()

        audio_filename = getattr(self, "_pending_audio_filename", None)
        self._pending_audio_filename = None

        if not text or not text.strip():
            if audio_filename:
                audio_path = self.config.get_recordings_dir() / audio_filename
                if audio_path.exists():
                    try:
                        audio_path.unlink()
                    except OSError:
                        pass
            self._show_notification("Turbo Whisper", "No speech detected", QSystemTrayIcon.MessageIcon.Warning)
            return

        self.config.add_to_history(text, audio_file=audio_filename)
        self.window._refresh_history()

        if self.config.copy_to_clipboard:
            self.typer.copy_to_clipboard(text)

        print(f"_on_transcription_complete: text='{text[:60]}' auto_paste={self.config.auto_paste} copy_to_clipboard={self.config.copy_to_clipboard}")

        if self.config.auto_paste:
            if self._wait_for_claude_ready():
                print(f"_on_transcription_complete: calling typer.type_text()...")
                result = self.typer.type_text(text)
                print(f"_on_transcription_complete: typer.type_text() returned {result}")
                self._show_notification(
                    "Turbo Whisper",
                    f"Transcribed: {text[:50]}..." if len(text) > 50 else f"Transcribed: {text}",
                    QSystemTrayIcon.MessageIcon.Information,
                )
            else:
                self._show_notification("Turbo Whisper", "Copied (Claude busy)", QSystemTrayIcon.MessageIcon.Information)
        else:
            self._show_notification(
                "Turbo Whisper",
                f"Transcribed: {text[:50]}..." if len(text) > 50 else f"Transcribed: {text}",
                QSystemTrayIcon.MessageIcon.Information,
            )

    def _on_transcription_error(self, error: str) -> None:
        self.is_processing = False
        self._processing_watchdog.stop()
        self._show_notification("Turbo Whisper - Error", error, QSystemTrayIcon.MessageIcon.Critical, 3000)

    def _quit(self) -> None:
        if self.is_recording or self.is_processing:
            try:
                self.recorder.stop()
            except Exception:
                pass
        self.is_recording = False
        self.is_processing = False

        if self.hotkey_manager:
            try:
                self.hotkey_manager.stop()
            except Exception:
                pass
        if self.integration_server:
            try:
                self.integration_server.stop()
            except Exception:
                pass
        try:
            self.recorder.cleanup()
        except Exception:
            pass
        self.app.quit()

    def run(self) -> int:
        if self.hotkey_manager:
            self.hotkey_manager.start()
        hotkey_str = "+".join(k.title() for k in self.config.hotkey)
        self._show_notification("Turbo Whisper", f"Press {hotkey_str} to start dictating", QSystemTrayIcon.MessageIcon.Information, 3000)
        return self.app.exec()


_lock_fd = None


def ensure_single_instance():
    global _lock_fd
    if sys.platform == "win32":
        lock_path = os.path.join(tempfile.gettempdir(), "turbo-whisper.lock")
        try:
            _lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            msvcrt.locking(_lock_fd, msvcrt.LK_NBLCK, 1)
            os.lseek(_lock_fd, 0, os.SEEK_SET)
            os.ftruncate(_lock_fd, 0)
            os.write(_lock_fd, str(os.getpid()).encode())
        except OSError:
            print("Turbo Whisper is already running.")
            sys.exit(0)
    else:
        lock_path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "turbo-whisper.lock")
        try:
            _lock_fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
            fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(_lock_fd, 0)
            os.write(_lock_fd, str(os.getpid()).encode())
        except OSError:
            print("Turbo Whisper is already running.")
            sys.exit(0)


def main():
    ensure_single_instance()
    app = TurboWhisper()
    sys.exit(app.run())


if __name__ == "__main__":
    main()