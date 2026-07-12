"""Main application entry point for Turbo Whisper."""

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


# Punctuation to strip from hallucination text/pattern edges
_PUNCTUATION_TRIM = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~…—–«» "


def _load_hallucination_patterns() -> list[str]:
    """Load hallucination filter patterns from JSON file."""
    patterns = []

    # Load from JSON file
    json_path = Path(__file__).parent / "assets" / "base_hallucination_filter.json"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for lang_items in data.values():
                for item in lang_items:
                    if item and item.strip():
                        patterns.append(item.strip())
        except Exception as e:
            print(f"Warning: Could not load hallucination filter: {e}")

    # Also add substring checks for "субтитры" derivatives
    patterns.append("субтитр")

    # Filter out single-letter patterns (useless for exact match)
    patterns = [p for p in patterns if len(p.strip()) > 2 or p.strip() == "субтитр"]

    # Additional Russian-specific hallucination patterns
    ru_patterns = [
        "ВЕСЕЛАЯ МУЗЫКА", "СПОКОЙНАЯ МУЗЫКА", "ГРУСТНАЯ МЕЛОДИЯ",
        "ЛИРИЧЕСКАЯ МУЗЫКА", "ДИНАМИЧНАЯ МУЗЫКА", "ТАИНСТВЕННАЯ МУЗЫКА",
        "ТОРЖЕСТВЕННАЯ МУЗЫКА", "ИНТРИГУЮЩАЯ МУЗЫКА", "НАПРЯЖЕННАЯ МУЗЫКА",
        "ПЕЧАЛЬНАЯ МУЗЫКА", "ТРЕВОЖНАЯ МУЗЫКА", "МУЗЫКАЛЬНАЯ ЗАСТАВКА",
        "ПЕРЕСТРЕЛКА", "ГУДОК ПОЕЗДА", "РЁВ МОТОРА", "ШУМ ДВИГАТЕЛЯ",
        "СИГНАЛ АВТОМОБИЛЯ", "ЛАЙ СОБАК", "ПЕС ЛАЕТ", "КАШЕЛЬ", "ВЫСТРЕЛЫ",
        "ШУМ ДОЖДЯ", "ПЕСНЯ", "ВЗРЫВ", "ШУМ МОТОРА", "ПЛЕСК ВОДЫ",
        "ГУДОК АВТОМОБИЛЯ", "ЛАЙ СОБАКИ", "ПО ГРОМКОГОВОРИТЕЛЮ",
        "ПО ГРОМКОГОВОРИЧЕСКОМ ЯЗЫКЕ", "ПО ТВ.", "АПЛОДИСМЕНТЫ",
        "ГОРОДСКОЙ ШУМ", "ПОЛИЦИЯ", "СМЕХ", "СТУК В ДВЕРЬ",
        "ШУМ ДОЖДЯ", "ПОЛИЦЕЙСКАЯ СИРЕНА", "ЗВОНОК В ДВЕРЬ",
        "Спасибо за субтитры!", "Субтитры добавил DimaTorzok",
        "Субтитры подогнал «Симон»!",
        "Редактор субтитров М.Лосева Корректор А.Егорова",
        "Редактор субтитров А.Синецкая Корректор А.Егорова",
        "Редактор субтитров Т.Горелова Корректор А.Егорова",
        "Редактор субтитров Е.Жукова Корректор А.Егорова",
        "Редактор субтитров А.Захарова Корректор А.Егорова",
        "Смотрите продолжение во второй части видео.",
        "Смотрите продолжение в следующей части.",
        "Смотрите продолжение в следующей части видео.",
        "Смотрите продолжение в 4 части видео.",
        "Смотрите продолжение в следующей серии...",
        "Смотрите продолжение во второй части.",
        "ПОДПИШИСЬ НА КАНАЛ", "ПОДПИШИСЬ!", "ПОДПИШИСЬ",
        "Поехали!", "Поехали.",
        "Девушки отдыхают...",
        "Пока-пока! Удачи!",
        "🦜", "💥", "😎", "🤨", "🤔",
    ]
    for p in ru_patterns:
        if p not in patterns:
            patterns.append(p)

    return patterns


# Load hallucination patterns at module level
_HALLUCINATION_PATTERNS = _load_hallucination_patterns()


def _is_hallucination(text: str) -> bool:
    """Check if text is a hallucination/artifact from the speech model.

    Strips trailing punctuation before comparing so that e.g.
    'Продолжение следует.' matches the pattern 'Продолжение следует...'
    Also filters very short texts (1 word or very short) that are usually hallucinated.
    """
    text_stripped = text.strip(_PUNCTUATION_TRIM)
    text_lower = text_stripped.lower()

    # Filter single words shorter than 5 chars (usually noise artifacts)
    if len(text_stripped) < 5 and " " not in text_stripped:
        return True

    # Special case: substring check for 'субтитр' derivatives
    if "субтитр" in text_lower:
        return True

    # All other patterns: exact match (case-insensitive), trimmed same way
    for pattern in _HALLUCINATION_PATTERNS:
        if pattern.strip(_PUNCTUATION_TRIM).lower() == text_lower:
            return True

    return False

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

from turbo_whisper.api import WhisperAPIError, WhisperClient
from turbo_whisper.config import Config
from turbo_whisper.hotkey import create_hotkey_manager
from turbo_whisper.icons import (
    get_check_icon,
    get_chevron_down_icon,
    get_chevron_up_icon,
    get_copy_icon,
    get_eye_icon,
    get_eye_off_icon,
    get_play_icon,
    get_stop_icon,
    get_tray_icon,
)
from turbo_whisper.recorder import AudioRecorder
from turbo_whisper.typer import Typer
from turbo_whisper.floating_indicator import FloatingIndicatorProcess


def _setup_logger() -> logging.Logger:
    """Set up file logger for main module."""
    logger = logging.getLogger("turbo-whisper.main")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        import sys as _sys
        if _sys.platform == "win32":
            log_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            log_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        log_dir = log_dir / "turbo-whisper"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "turbo-whisper.log"

        handler = logging.FileHandler(str(log_path), encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


logger = _setup_logger()


class SignalBridge(QObject):
    """Bridge for thread-safe Qt signals."""

    toggle_recording = pyqtSignal()
    update_waveform = pyqtSignal(float, list)
    transcription_complete = pyqtSignal(str)
    transcription_error = pyqtSignal(str)
    show_status = pyqtSignal(str)
    chunk_transcription_complete = pyqtSignal(str)


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
    """Main application window with waveform and all settings visible."""

    cancel_requested = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self._drag_pos = None
        self._is_capturing_key = False
        self._key_listener = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the main window UI with all settings visible."""
        self.setWindowIcon(get_tray_icon(128, recording=False))
        self.setWindowTitle("Turbo Whisper v1.0.0")

        # Normal window with Windows default styling
        self._base_window_flags = (
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowFlags(self._base_window_flags)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # Enable focus events

        container = QWidget(self)
        self.container = container
        container.setObjectName("container")
        container.setStyleSheet(
            """
            #container {
                background-color: #f0f0f0;
                border: 1px solid #cccccc;
            }
            QLabel { color: #000000; font-size: 11px; }
            QLineEdit {
                background-color: #ffffff;
                border: 1px solid #cccccc;
                border-radius: 2px;
                color: #000000;
                padding: 4px;
                font-size: 11px;
            }
            QSlider::groove:horizontal {
                background: #cccccc;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #0078d4;
                width: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }
            QComboBox {
                background-color: #ffffff;
                border: 1px solid #cccccc;
                border-radius: 2px;
                color: #000000;
                padding: 4px;
                font-size: 11px;
            }
            QComboBox::drop-down { border: none; }
            QCheckBox { color: #000000; font-size: 11px; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QListWidget {
                background-color: #ffffff;
                border: 1px solid #cccccc;
                border-radius: 2px;
                color: #000000;
                font-size: 11px;
            }
            QPushButton {
                background-color: #e1e1e1;
                border: 1px solid #adadad;
                border-radius: 2px;
                color: #000000;
                padding: 4px 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #d0d0d0;
            }
            QPushButton:pressed {
                background-color: #c0c0c0;
            }
        """
        )

        from PyQt6.QtWidgets import QFrame, QScrollArea

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Scroll area for all content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        # === Status ===
        status_widget = QWidget()
        status_widget.setStyleSheet("background: transparent;")
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(4, 0, 4, 0)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()

        #self._hotkey_str = "+".join(k.title() for k in self.config.hotkey)
        self.hints_label = QLabel(f"Hotkey: {self.config.hotkey}")
        self.hints_label.setStyleSheet("color: #666; font-size: 10px;")
        status_layout.addWidget(self.hints_label)

        self._status_dots = 0
        self._status_timer = QTimer()
        self._status_timer.timeout.connect(self._animate_status)
        self._status_timer.setInterval(400)
        layout.addWidget(status_widget)

        # === Visualizer Opacity (always visible) ===
        opacity_row = QHBoxLayout()
        opacity_row.setContentsMargins(4, 0, 4, 0)
        opacity_label = QLabel("Opacity:")
        opacity_label.setStyleSheet("color: #666; font-size: 10px;")
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(30, 255)
        self.opacity_slider.setValue(self.config.indicator_opacity)
        self.opacity_slider.setFixedWidth(120)
        self.opacity_value_label = QLabel(str(self.config.indicator_opacity))
        self.opacity_value_label.setStyleSheet("color: #666; font-size: 10px;")
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_value_label.setText(str(v))
        )
        opacity_row.addWidget(opacity_label)
        opacity_row.addWidget(self.opacity_slider)
        opacity_row.addWidget(self.opacity_value_label)
        opacity_row.addStretch()
        layout.addLayout(opacity_row)

        # === Settings (always visible) ===
        settings_widget = self._build_settings_panel()
        layout.addWidget(settings_widget)

        layout.addStretch()

        scroll.setWidget(content_widget)
        container_layout.addWidget(scroll)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        # Set size - wider and taller to fit all settings
        self.setFixedSize(self.config.window_width + 40, self.config.window_height + 480)

    def _build_settings_panel(self):
        """Build the collapsible settings panel with all config options."""
        from PyQt6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QListWidget, QPushButton, QSlider

        panel = QWidget()
        panel.setStyleSheet("""
            QWidget { background-color: #f0f0f0; }
            QLabel { color: #000000; font-size: 10px; }
            QLineEdit { background-color: #ffffff;
                border: 1px solid #cccccc; border-radius: 2px; color: #000000;
                padding: 4px; font-size: 11px; }
            QSlider::groove:horizontal { background: #cccccc; height: 6px; border-radius: 3px; }
            QSlider::handle:horizontal { background: #0078d4; width: 14px;
                margin: -4px 0; border-radius: 7px; }
            QComboBox { background-color: #ffffff;
                border: 1px solid #cccccc; border-radius: 2px; color: #000000;
                padding: 4px; font-size: 11px; }
            QComboBox::drop-down { border: none; }
            QCheckBox { color: #000000; font-size: 11px; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QListWidget { background-color: #ffffff;
                border: 1px solid #cccccc; border-radius: 2px; color: #000000; font-size: 11px; }
        """)

        s = QVBoxLayout(panel)
        s.setContentsMargins(12, 8, 12, 8)
        s.setSpacing(6)

        # === API Settings ===
        api_label = QLabel("API Settings")
        api_label.setStyleSheet("color: #000000; font-size: 11px; font-weight: bold; margin-top: 4px;")
        s.addWidget(api_label)

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

        # Model
        s.addWidget(QLabel("Model"))
        self.model_input = QLineEdit(self.config.model)
        self.model_input.setPlaceholderText("openai/whisper-large-v3-turbo")
        s.addWidget(self.model_input)

        # Language
        s.addWidget(QLabel("Language"))
        self.language_combo = QComboBox()
        languages = [("Russian", "ru"), ("English", "en"), ("German", "de"), ("French", "fr"),
                     ("Spanish", "es"), ("Italian", "it"), ("Portuguese", "pt"), ("Chinese", "zh"),
                     ("Japanese", "ja"), ("Korean", "ko"), ("Auto-detect", "")]
        for name, code in languages:
            self.language_combo.addItem(name, code)
        # Set current language
        for i in range(self.language_combo.count()):
            if self.language_combo.itemData(i) == self.config.language:
                self.language_combo.setCurrentIndex(i)
                break
        s.addWidget(self.language_combo)

        # === Audio Settings ===
        audio_label = QLabel("Audio")
        audio_label.setStyleSheet("color: #0078d4; font-size: 11px; font-weight: bold; margin-top: 8px;")
        s.addWidget(audio_label)

        # Microphone
        s.addWidget(QLabel("Microphone"))
        self.mic_combo = QComboBox()
        self._populate_mic_dropdown()
        s.addWidget(self.mic_combo)

        # Gain slider
        gain_row = QHBoxLayout()
        self.gain_label = QLabel("Mic Gain:")
        self.gain_value_label = QLabel("100%")
        self.gain_value_label.setStyleSheet("color: #0078d4; font-weight: bold;")
        gain_row.addWidget(self.gain_label)
        gain_row.addStretch()
        gain_row.addWidget(self.gain_value_label)
        s.addLayout(gain_row)

        self.sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(0, 200)
        self.sensitivity_slider.setValue(self.config.mic_gain)
        self.sensitivity_slider.setSingleStep(20)
        self.sensitivity_slider.setPageStep(20)
        self.sensitivity_slider.valueChanged.connect(self._on_sensitivity_changed)
        self._current_mic_level = 0
        self._update_sensitivity_style()
        s.addWidget(self.sensitivity_slider)

        # === Hotkey ===
        hotkey_label = QLabel("Hotkey")
        hotkey_label.setStyleSheet("color: #0078d4; font-size: 11px; font-weight: bold; margin-top: 8px;")
        s.addWidget(hotkey_label)

        # Hotkey configuration: modifier dropdown + key input
        hotkey_row = QHBoxLayout()

        # Modifier combo box
        self.hotkey_modifier_combo = QComboBox()
        self.hotkey_modifier_combo.addItems([
            "None (single key)",
            "Ctrl +",
            "Shift +",
            "Alt +",
            "Ctrl + Shift +",
            "Ctrl + Alt +",
            "Shift + Alt +",
            "Ctrl + Shift + Alt +",
            "Win +",
            "Win + Ctrl +",
            "Win + Shift +",
            "Win + Ctrl + Alt +",
        ])
        self.hotkey_modifier_combo.setStyleSheet(
            "QComboBox { background-color: #ffffff; border: 1px solid #cccccc; "
            "border-radius: 2px; color: #000000; padding: 4px; font-size: 11px; }"
        )
        hotkey_row.addWidget(self.hotkey_modifier_combo)
        self.hotkey_modifier_combo.currentIndexChanged.connect(self._update_hotkey_preview)

        # Key input - simple editable text field
        self.hotkey_key_input = QLineEdit()
        self.hotkey_key_input.setPlaceholderText("e.g. F8, ~, A")
        self.hotkey_key_input.setMaximumWidth(100)
        self.hotkey_key_input.setStyleSheet(
            "QLineEdit { background-color: #ffffff; border: 1px solid #cccccc; "
            "border-radius: 2px; color: #000000; padding: 4px; font-size: 11px; }"
        )
        self.hotkey_key_input.textChanged.connect(self._on_hotkey_key_changed)
        hotkey_row.addWidget(self.hotkey_key_input)

        # Preview
        self.hotkey_preview = QLabel()
        self.hotkey_preview.setStyleSheet("color: #000000; font-size: 11px; font-weight: bold;")
        hotkey_row.addWidget(self.hotkey_preview)

        s.addLayout(hotkey_row)

        # Initialize hotkey display from current config
        self._update_hotkey_display()

        # === Behavior ===
        behavior_label = QLabel("Behavior")
        behavior_label.setStyleSheet("color: #0078d4; font-size: 11px; font-weight: bold; margin-top: 8px;")
        s.addWidget(behavior_label)

        # Auto-paste
        self.auto_paste_cb = QCheckBox("Auto-type transcription")
        self.auto_paste_cb.setChecked(self.config.auto_paste)
        self.auto_paste_cb.setToolTip("Automatically type transcription into focused window")
        s.addWidget(self.auto_paste_cb)

        # Copy to clipboard
        self.copy_clipboard_cb = QCheckBox("Copy to clipboard")
        self.copy_clipboard_cb.setChecked(self.config.copy_to_clipboard)
        self.copy_clipboard_cb.setToolTip("Copy transcription text to clipboard")
        s.addWidget(self.copy_clipboard_cb)

        # Character-by-character typing
        self.char_typing_cb = QCheckBox("Character-by-character typing")
        self.char_typing_cb.setChecked(self.config.use_character_typing)
        self.char_typing_cb.setToolTip("Type character by character instead of clipboard paste")
        s.addWidget(self.char_typing_cb)

        # Store recordings
        self.store_recordings_cb = QCheckBox("Save recordings with history")
        self.store_recordings_cb.setChecked(self.config.store_recordings)
        self.store_recordings_cb.setToolTip("Save WAV files alongside transcription history")
        s.addWidget(self.store_recordings_cb)

        # Auto-start
        self.auto_start_cb = QCheckBox("Auto-start on login")
        self.auto_start_cb.setChecked(self.config.auto_start)
        self.auto_start_cb.setToolTip("Automatically start Turbo Whisper when you log in")
        s.addWidget(self.auto_start_cb)

        # === Streaming Mode ===
        streaming_label = QLabel("Streaming Mode")
        streaming_label.setStyleSheet("color: #0078d4; font-size: 11px; font-weight: bold; margin-top: 8px;")
        s.addWidget(streaming_label)

        self.streaming_cb = QCheckBox("Enable streaming (transcribe as you speak)")
        self.streaming_cb.setChecked(self.config.streaming_mode)
        self.streaming_cb.setToolTip(
            "When enabled, transcribes audio in real-time as you speak.\n"
            "Words appear in the target window within 1-2 seconds.\n"
            "Window stays visible during recording with live status updates."
        )
        self.streaming_cb.stateChanged.connect(self._on_streaming_mode_changed)
        s.addWidget(self.streaming_cb)

        # Silence threshold (only relevant for streaming)
        silence_row = QHBoxLayout()
        self.silence_label = QLabel("Silence threshold:")
        self.silence_slider = QSlider(Qt.Orientation.Horizontal)
        self.silence_slider.setRange(200, 1000)
        self.silence_slider.setValue(self.config.silence_threshold_ms)
        self.silence_slider.setSingleStep(50)
        self.silence_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.silence_slider.setTickInterval(100)
        self.silence_slider.valueChanged.connect(self._on_silence_threshold_changed)
        self.silence_value_label = QLabel(f"{self.config.silence_threshold_ms}ms")
        self.silence_value_label.setStyleSheet("color: #0078d4; font-weight: bold;")
        silence_row.addWidget(self.silence_label)
        silence_row.addWidget(self.silence_slider)
        silence_row.addWidget(self.silence_value_label)
        s.addLayout(silence_row)

        # VAD Aggressiveness (only relevant for streaming)
        vad_row = QHBoxLayout()
        self.vad_label = QLabel("VAD sensitivity:")
        self.vad_slider = QSlider(Qt.Orientation.Horizontal)
        self.vad_slider.setRange(0, 3)
        self.vad_slider.setValue(self.config.vad_aggressiveness)
        self.vad_slider.setSingleStep(1)
        self.vad_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.vad_slider.setTickInterval(1)
        self.vad_slider.valueChanged.connect(self._on_vad_changed)
        self.vad_value_label = QLabel(self._get_vad_description(self.config.vad_aggressiveness))
        self.vad_value_label.setStyleSheet("color: #0078d4; font-weight: bold;")
        vad_row.addWidget(self.vad_label)
        vad_row.addWidget(self.vad_slider)
        vad_row.addWidget(self.vad_value_label)
        s.addLayout(vad_row)

        # VAD trim silence
        self.vad_trim_cb = QCheckBox("Remove silence from chunks (VAD trim)")
        self.vad_trim_cb.setChecked(self.config.vad_trim_silence)
        self.vad_trim_cb.setToolTip(
            "When enabled, leading and trailing silence is removed from\n"
            "audio chunks before sending to API.\n"
            "Reduces API costs and improves transcription accuracy."
        )
        s.addWidget(self.vad_trim_cb)

        # Auto-stop timeout (listen mode timeout)
        timeout_row = QHBoxLayout()
        self.timeout_label = QLabel("Stop after silence:")
        self.timeout_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeout_slider.setRange(0, 30)
        self.timeout_slider.setValue(self.config.auto_stop_timeout)
        self.timeout_slider.setSingleStep(1)
        self.timeout_slider.setPageStep(5)
        self.timeout_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.timeout_slider.setTickInterval(5)
        self.timeout_slider.valueChanged.connect(self._on_timeout_changed)
        self.timeout_value_label = QLabel(self._get_timeout_description(self.config.auto_stop_timeout))
        self.timeout_value_label.setStyleSheet("color: #0078d4; font-weight: bold;")
        timeout_row.addWidget(self.timeout_label)
        timeout_row.addWidget(self.timeout_slider)
        timeout_row.addWidget(self.timeout_value_label)
        s.addLayout(timeout_row)

        # Max recording duration (batch mode)
        max_row = QHBoxLayout()
        max_label = QLabel("Max recording:")
        max_label.setStyleSheet("color: #888; font-size: 10px;")
        self.max_slider = QSlider(Qt.Orientation.Horizontal)
        self.max_slider.setRange(0, 600)
        self.max_slider.setValue(self.config.max_recording_seconds)
        self.max_slider.setSingleStep(30)
        self.max_slider.setPageStep(60)
        self.max_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.max_slider.setTickInterval(60)
        self.max_slider.valueChanged.connect(self._on_max_duration_changed)
        self.max_value_label = QLabel(self._get_max_duration_description(self.config.max_recording_seconds))
        self.max_value_label.setStyleSheet("color: #888; font-size: 10px;")
        max_row.addWidget(max_label)
        max_row.addWidget(self.max_slider)
        max_row.addWidget(self.max_value_label)
        s.addLayout(max_row)

        # Update visibility based on current streaming mode
        self._update_streaming_ui_visibility()

        # === History ===
        history_label = QLabel("History")
        history_label.setStyleSheet("color: #0078d4; font-size: 11px; font-weight: bold; margin-top: 8px;")
        s.addWidget(history_label)

        self.history_list = QListWidget()
        self.history_list.setMinimumHeight(120)
        self.history_list.setMaximumHeight(200)
        self._refresh_history()
        s.addWidget(self.history_list)

        # Clear history button
        self.clear_history_btn = QPushButton("Clear History")
        self.clear_history_btn.setStyleSheet(
            "QPushButton { background-color: #666; color: #fff; border: none; "
            "border-radius: 4px; font-size: 10px; padding: 4px 8px; }"
            "QPushButton:hover { background-color: #888; }"
        )
        self.clear_history_btn.clicked.connect(self._clear_history)
        s.addWidget(self.clear_history_btn)

        # === Save ===
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setStyleSheet(
            "QPushButton { background-color: #0078d4; color: #ffffff; border: none; "
            "border-radius: 2px; font-size: 11px; font-weight: bold; padding: 6px 12px; margin-top: 8px; }"
            "QPushButton:hover { background-color: #106ebe; }"
            "QPushButton:pressed { background-color: #005a9e; }"
        )
        self.save_btn.clicked.connect(self._save_settings)
        s.addWidget(self.save_btn)

        return panel

    def _on_streaming_mode_changed(self, state) -> None:
        """Toggle streaming mode and show/hide related settings."""
        self._update_streaming_ui_visibility()

    def _update_streaming_ui_visibility(self) -> None:
        """Update visibility of streaming-related UI elements."""
        is_streaming = self.streaming_cb.isChecked() if hasattr(self, 'streaming_cb') else self.config.streaming_mode
        if hasattr(self, 'silence_label'):
            self.silence_label.setVisible(is_streaming)
        if hasattr(self, 'silence_slider'):
            self.silence_slider.setVisible(is_streaming)
        if hasattr(self, 'silence_value_label'):
            self.silence_value_label.setVisible(is_streaming)
        if hasattr(self, 'vad_label'):
            self.vad_label.setVisible(is_streaming)
        if hasattr(self, 'vad_slider'):
            self.vad_slider.setVisible(is_streaming)
        if hasattr(self, 'vad_value_label'):
            self.vad_value_label.setVisible(is_streaming)
        # Auto-stop timeout is always visible

    def _on_silence_threshold_changed(self, value: int) -> None:
        """Update silence threshold display."""
        if hasattr(self, 'silence_value_label'):
            self.silence_value_label.setText(f"{value}ms")

    def _on_vad_changed(self, value: int) -> None:
        """Update VAD aggressiveness display."""
        if hasattr(self, 'vad_value_label'):
            self.vad_value_label.setText(self._get_vad_description(value))

    def _get_vad_description(self, value: int) -> str:
        """Get human-readable description for VAD aggressiveness value."""
        descriptions = {
            0: "Quality (most sensitive)",
            1: "Balanced",
            2: "Default",
            3: "Aggressive (may miss quiet speech)"
        }
        return descriptions.get(value, f"Mode {value}")

    def _get_timeout_description(self, value: int) -> str:
        """Get human-readable description for auto-stop timeout value."""
        if value <= 0:
            return "Off"
        return f"{value}s"

    def _on_timeout_changed(self, value: int) -> None:
        """Update auto-stop timeout display."""
        if hasattr(self, 'timeout_value_label'):
            self.timeout_value_label.setText(self._get_timeout_description(value))

    def _get_max_duration_description(self, value: int) -> str:
        """Get human-readable description for max recording duration."""
        if value <= 0:
            return "Off"
        minutes = value // 60
        seconds = value % 60
        if minutes > 0:
            return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
        return f"{seconds}s"

    def _on_max_duration_changed(self, value: int) -> None:
        """Update max recording duration display."""
        if hasattr(self, 'max_value_label'):
            self.max_value_label.setText(self._get_max_duration_description(value))

    def _update_hotkey_display(self) -> None:
        """Update hotkey display from current config."""
        hotkey = self.config.hotkey
        modifiers = set()
        main_key = "~"

        for key in hotkey:
            key_lower = key.lower()
            if key_lower in ("ctrl", "shift", "alt", "super", "win"):
                modifiers.add(key_lower)
            else:
                main_key = key

        # Set modifier combo by matching modifier set
        combo_items = {
            frozenset(): "None (single key)",
            frozenset(["ctrl"]): "Ctrl +",
            frozenset(["shift"]): "Shift +",
            frozenset(["alt"]): "Alt +",
            frozenset(["ctrl", "shift"]): "Ctrl + Shift +",
            frozenset(["ctrl", "alt"]): "Ctrl + Alt +",
            frozenset(["shift", "alt"]): "Shift + Alt +",
            frozenset(["ctrl", "shift", "alt"]): "Ctrl + Shift + Alt +",
            frozenset(["win"]): "Win +",
            frozenset(["win", "ctrl"]): "Win + Ctrl +",
            frozenset(["win", "shift"]): "Win + Shift +",
            frozenset(["win", "ctrl", "shift", "alt"]): "Win + Ctrl + Alt +",
        }

        mod_set = frozenset(modifiers)
        for i in range(self.hotkey_modifier_combo.count()):
            item_text = self.hotkey_modifier_combo.itemText(i)
            if item_text == combo_items.get(mod_set, ""):
                self.hotkey_modifier_combo.setCurrentIndex(i)
                break

        # Set key input (keep original case for special keys like F8, use uppercase for letters)
        if main_key == "~":
            self.hotkey_key_input.setText("~")
        else:
            self.hotkey_key_input.setText(main_key.upper() if main_key else "")

        self._update_hotkey_preview()

    def _on_hotkey_key_changed(self, text: str) -> None:
        """Handle hotkey key input: keep only last char, map Russian to Latin."""
        if not text:
            return

        # Keep only the last character
        last_char = text[-1]

        # Russian letters to Latin QWERTY mapping
        ru_to_latin = {
            'а': 'F', 'б': ',', 'в': 'D', 'г': 'U', 'д': 'L', 'е': 'T',
            'ё': '~', 'ж': ';', 'з': 'P', 'и': 'B', 'й': 'Q', 'к': 'R',
            'л': 'K', 'м': 'V', 'н': 'Y', 'о': 'J', 'п': 'G', 'р': 'H',
            'с': 'C', 'т': 'N', 'у': 'E', 'ф': 'A', 'х': '[', 'ц': 'W',
            'ч': 'X', 'ш': 'I', 'щ': 'O', 'ъ': ']', 'ы': 'S', 'ь': 'M',
            'э': "'", 'ю': '.', 'я': 'Z',
            'А': 'F', 'Б': ',', 'В': 'D', 'Г': 'U', 'Д': 'L', 'Е': 'T',
            'Ё': '~', 'Ж': ';', 'З': 'P', 'И': 'B', 'Й': 'Q', 'К': 'R',
            'Л': 'K', 'М': 'V', 'Н': 'Y', 'О': 'J', 'П': 'G', 'Р': 'H',
            'С': 'C', 'Т': 'N', 'У': 'E', 'Ф': 'A', 'Х': '[', 'Ц': 'W',
            'Ч': 'X', 'Ш': 'I', 'Щ': 'O', 'Ъ': ']', 'Ы': 'S', 'Ь': 'M',
            'Э': "'", 'Ю': '.', 'Я': 'Z',
        }

        # Check if it's a Russian letter
        if last_char in ru_to_latin:
            mapped = ru_to_latin[last_char]
            self.hotkey_key_input.setText(mapped)
        else:
            # For Latin letters - uppercase
            if last_char.isalpha() and ord(last_char) < 128:
                self.hotkey_key_input.setText(last_char.upper())
            else:
                # For everything else, keep the last char as-is
                self.hotkey_key_input.setText(last_char)

        # Update preview
        self._update_hotkey_preview()

    def _update_hotkey_preview(self) -> None:
        """Update hotkey preview label."""
        hotkey = self._build_hotkey_from_ui()
        self.hotkey_preview.setText("→ " + "+".join(k.title() for k in hotkey))

    def _build_hotkey_from_ui(self) -> list[str]:
        """Build hotkey list from UI controls."""
        hotkey = []

        # Get modifier from combo
        mod_text = self.hotkey_modifier_combo.currentText().rstrip(" +")
        if mod_text != "None (single key)":
            modifiers = [m.strip().lower() for m in mod_text.split("+")]
            hotkey.extend(modifiers)

        # Get main key
        key_text = self.hotkey_key_input.text().strip()
        if key_text:
            # Always use uppercase Latin letter
            key_upper = key_text.upper()
            hotkey.append(key_upper)

        return hotkey if hotkey else ["~"]  # Default to ~ if empty

    def update_icon(self, recording: bool) -> None:
        self.setWindowIcon(get_tray_icon(128, recording=recording))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        """Window gained focus - notify to disable hotkey."""
        if hasattr(self, '_on_focus_change'):
            self._on_focus_change(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        """Window lost focus - notify to re-enable hotkey."""
        if hasattr(self, '_on_focus_change'):
            self._on_focus_change(False)
        super().focusOutEvent(event)

    def eventFilter(self, obj, event):
        """Event filter for hotkey key input capture."""
        if obj == self.hotkey_key_input:
            if event.type() == event.Type.MouseButtonPress:
                self.hotkey_key_input.clear()
                self.hotkey_key_input.setFocus()
                return True
        return super().eventFilter(obj, event)

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
        hotkey_str = "+".join(k.title() for k in self.config.hotkey)
        self.hints_label.setText(f"{action}: {hotkey_str}")

    def update_mic_level(self, level: float) -> None:
        if abs(level - self._current_mic_level) > 0.01 or level == 0:
            self._current_mic_level = level
            self._update_sensitivity_style()

    def _animate_status(self) -> None:
        self._status_dots = (self._status_dots + 1) % 4
        dots = "." * self._status_dots
        self.status_label.setText(f"{self._base_status}{dots}")

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
                    stop:0 #0078d4, stop:{level_pct / 100:.2f} #0078d4,
                    stop:{min(1.0, level_pct / 100 + 0.01):.2f} #cccccc, stop:1 #cccccc);
                height: 8px; border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: #fff; width: 16px; height: 16px;
                margin: -5px 0; border-radius: 8px; border: 2px solid #0078d4;
            }}
            QSlider {{ height: 24px; }}
        """
        )

    def _populate_mic_dropdown(self) -> None:
        import sys
        from turbo_whisper.recorder import get_pipewire_sources

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
        """Save all settings from UI to config."""
        # API settings
        self.config.api_url = self.api_url_input.text()
        self.config.api_key = self._actual_api_key
        self.config.model = self.model_input.text()
        self.config.language = self.language_combo.currentData()

        # Audio settings
        self.config.input_device_index = self.mic_combo.currentData()
        self.config.input_device_name = self.mic_combo.currentText()
        self.config.mic_gain = self.sensitivity_slider.value()

        # Hotkey setting
        new_hotkey = self._build_hotkey_from_ui()

        # Behavior settings
        self.config.auto_paste = self.auto_paste_cb.isChecked()
        self.config.copy_to_clipboard = self.copy_clipboard_cb.isChecked()
        self.config.use_character_typing = self.char_typing_cb.isChecked()
        self.config.store_recordings = self.store_recordings_cb.isChecked()
        self.config.auto_start = self.auto_start_cb.isChecked()

        # Streaming mode settings
        self.config.streaming_mode = self.streaming_cb.isChecked()
        self.config.silence_threshold_ms = self.silence_slider.value()
        self.config.vad_aggressiveness = self.vad_slider.value()
        if hasattr(self, 'vad_trim_cb'):
            self.config.vad_trim_silence = self.vad_trim_cb.isChecked()
        if hasattr(self, 'timeout_slider'):
            self.config.auto_stop_timeout = self.timeout_slider.value()
        if hasattr(self, 'max_slider'):
            self.config.max_recording_seconds = self.max_slider.value()

        # Visualizer settings
        self.config.indicator_opacity = self.opacity_slider.value()

        # Hotkey setting - apply before saving
        new_hotkey = self._build_hotkey_from_ui()
        if new_hotkey != self.config.hotkey:
            self.config.hotkey = new_hotkey
            logger.info(f"Hotkey changed to: {new_hotkey}")

        # Save config
        self.config.save()
        self.config.apply_autostart()

        # Restart hotkey manager if hotkey changed
        if hasattr(self, '_on_hotkey_changed'):
            self._on_hotkey_changed(self.config.hotkey)
            logger.info("Hotkey manager restart signaled with: %s", self.config.hotkey)

        # Show tray notification via callback
        if hasattr(self, '_on_settings_saved'):
            self._on_settings_saved()

        self.save_btn.setText("✓ Saved!")
        QTimer.singleShot(1500, lambda: self.save_btn.setText("Save Settings"))

    def _clear_history(self) -> None:
        """Clear all transcription history."""
        self.config.history = []
        self.config.save_history()
        self._refresh_history()

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
        # Always let child widgets handle their own events first
        # Only start drag if click is on the empty window background
        if event.button() == Qt.MouseButton.LeftButton:
            # Check if we clicked on a child widget that should handle clicks
            child = self.childAt(event.position().toPoint())
            if child is not None and child is not self:
                # Click is on a child - don't intercept
                return

            # Click is on empty window space - start drag
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
        logger.info(f"Config loaded: streaming_mode={self.config.streaming_mode}, "
                   f"silence_threshold={self.config.silence_threshold_ms}ms, "
                   f"energy_threshold={self.config.silence_energy_threshold}")
        # Apply behavior settings that take effect immediately at startup
        self.config.apply_behavior_on_start()
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setWindowIcon(get_tray_icon(128, recording=False))

        self.recorder = AudioRecorder(self.config)
        self.client = WhisperClient(self.config)
        self.typer = Typer(
            typing_delay_ms=self.config.typing_delay_ms,
            use_character_typing=self.config.use_character_typing,
            copy_to_clipboard=self.config.copy_to_clipboard,
        )
        self.signals = SignalBridge()

        self.window = RecordingWindow(self.config)
        self.window._on_focus_change = self._on_window_focus_change
        self.window._on_hotkey_changed = self._restart_hotkey_manager
        self.window._on_settings_saved = self._on_settings_saved
        self.window.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._setup_tray()

        self.is_recording = False
        self.is_processing = False
        self.is_stopping = False
        self._pending_waveform_data = None
        self._last_notification_time = 0.0
        self._processing_watchdog = QTimer()
        self._processing_watchdog.setSingleShot(True)
        self._processing_watchdog.timeout.connect(self._on_processing_timeout)
        self._processing_thread = None

        # Streaming mode state
        self._chunk_transcription_threads = []
        self._pending_chunk_texts = []  # Accumulate chunk texts for full message
        self._chunk_count = 0
        self._current_session_text = ""  # Full text for current recording session
        self._streaming_session_id = 0  # Incremented each recording to discard stale chunks

        # Ordered chunk processing queue
        import queue
        self._chunk_queue = queue.Queue()  # (sequence_number, text) tuples
        self._next_chunk_to_type = 0  # Next sequence number to type
        self._chunk_results = {}  # {sequence_number: text} for storing results
        self._chunk_order_timer = QTimer()
        self._chunk_order_timer.timeout.connect(self._process_chunk_queue)
        self._chunk_order_timer.setInterval(100)  # Check every 100ms

        self.signals.toggle_recording.connect(self._toggle_recording)
        self.signals.transcription_complete.connect(self._on_transcription_complete)
        self.signals.transcription_error.connect(self._on_transcription_error)
        self.signals.chunk_transcription_complete.connect(self._on_chunk_transcription_complete)
        self.signals.show_status.connect(self.window.set_status)
        self.window.cancel_requested.connect(self._cancel_recording)

        self._waveform_timer = QTimer()
        self._waveform_timer.timeout.connect(self._poll_waveform_data)
        self._waveform_timer.setInterval(30)

        # Auto-stop timer (checks if no text was inserted for too long)
        self._auto_stop_timer = QTimer()
        self._auto_stop_timer.timeout.connect(self._check_auto_stop)
        self._auto_stop_timer.setInterval(1000)  # Check every second
        self._last_insert_time = 0.0

        # Floating indicator - always visible (runs in a separate process)
        hotkey_str = "+".join(k.title() for k in self.config.hotkey)
        self._floating_indicator = FloatingIndicatorProcess(hotkey_str=hotkey_str)
        self._floating_indicator._on_double_click = self._show_window
        self._floating_indicator._on_right_click = self._show_tray_menu
        self._floating_indicator._on_left_click = self._close_tray_menu

        # Hotkey callback - check if main window is focused before processing
        def hotkey_callback():
            # Check if main window is the foreground window
            if self._is_main_window_focused():
                logger.info("Hotkey ignored - main window is focused")
                return
            self.signals.toggle_recording.emit()

        self.hotkey_manager = create_hotkey_manager(
            self.config.hotkey,
            hotkey_callback,
        )
        if self.hotkey_manager is None:
            print("Warning: Global hotkeys not available on this platform")

        self.integration_server = None
        if self.config.claude_integration:
            from turbo_whisper.integration_server import IntegrationServer
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
        """Set up system tray with improved menu."""
        self.tray = QSystemTrayIcon(self.app)
        self.tray.setIcon(get_tray_icon(64, recording=False))
        hotkey_str = "+".join(k.capitalize() for k in self.config.hotkey)
        self.tray.setToolTip(f"Turbo Whisper - Press {hotkey_str} to dictate")

        # Make tray icon always visible (Windows may hide it by default)
        self.tray.setVisible(True)

        menu = QMenu()

        # Show Window
        show_action = QAction("Show Window", menu)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        menu.addSeparator()

        # Start/Stop Recording with hotkey hint
        self.toggle_action = QAction(f"Start Recording ({hotkey_str})", menu)
        self.toggle_action.triggered.connect(self._toggle_recording)
        menu.addAction(self.toggle_action)

        menu.addSeparator()

        # Streaming mode toggle
        self.streaming_action = QAction("Streaming Mode", menu)
        self.streaming_action.setCheckable(True)
        self.streaming_action.setChecked(self.config.streaming_mode)
        self.streaming_action.triggered.connect(self._toggle_streaming_mode)
        menu.addAction(self.streaming_action)

        menu.addSeparator()

        # Copy last transcription
        self.copy_last_action = QAction("Copy Last Transcription", menu)
        self.copy_last_action.triggered.connect(self._copy_last_transcription)
        menu.addAction(self.copy_last_action)

        menu.addSeparator()

        # Quit
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self._tray_menu = menu
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _toggle_streaming_mode(self) -> None:
        """Toggle streaming mode from tray menu."""
        self.config.streaming_mode = not self.config.streaming_mode
        self.streaming_action.setChecked(self.config.streaming_mode)
        self.config.save()
        status = "enabled" if self.config.streaming_mode else "disabled"
        self._show_notification("Turbo Whisper", f"Streaming mode {status}",
                               QSystemTrayIcon.MessageIcon.Information)

    def _copy_last_transcription(self) -> None:
        """Copy the most recent transcription to clipboard."""
        if self.config.history:
            last_text = self.config.history[0].get("text", "")
            if last_text:
                self.typer.copy_to_clipboard(last_text)
                display = last_text[:50] + "..." if len(last_text) > 50 else last_text
                self._show_notification("Copied!", display,
                                       QSystemTrayIcon.MessageIcon.Information)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_window()

    def _update_icons(self, recording: bool) -> None:
        self.tray.setIcon(get_tray_icon(64, recording=recording))
        self.tray.setVisible(True)
        self.window.update_icon(recording=recording)

    def _on_opacity_changed(self, value: int) -> None:
        """Apply opacity immediately and save to config."""
        self.window.opacity_value_label.setText(str(value))
        self.config.indicator_opacity = value
        self._floating_indicator.set_opacity(value)

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
        """Show the main window with all settings."""
        # Block opening settings while recording
        if self.is_recording or self.is_processing:
            hotkey_str = "+".join(k.title() for k in self.config.hotkey)
            self._show_notification(
                "Turbo Whisper",
                f"Please stop recording (press {hotkey_str} again) before opening settings",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
            return
        self._update_icons(recording=False)
        self.window.set_status("Ready", animate=False)
        self.window.center_on_screen()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

        # Stop the hotkey hook while settings are open (Windows may refuse
        # focus transfer to a Tool window from another process, so we can't
        # rely on focusInEvent alone to kill the WH_KEYBOARD_LL hook).
        if self.hotkey_manager:
            self.hotkey_manager.stop()
            logger.info("Hotkey manager stopped (settings window opened)")

    def _show_tray_menu(self) -> None:
        """Show the tray context menu at cursor position (from visualizer right-click)."""
        from PyQt6.QtGui import QCursor
        self._tray_menu.exec(QCursor.pos())

    def _close_tray_menu(self) -> None:
        """Close the tray context menu (triggered by left-click on visualizer)."""
        self._tray_menu.close()

    def _on_settings_saved(self) -> None:
        """Show notification when settings are saved."""
        self._show_notification("Turbo Whisper", "Settings saved", QSystemTrayIcon.MessageIcon.Information)

        # Update floating indicator hotkey
        hotkey_str = "+".join(k.title() for k in self.config.hotkey)
        self._floating_indicator.update_hotkey(hotkey_str)
        self._floating_indicator.set_idle()

        # Update visualizer opacity
        self._floating_indicator.set_opacity(self.config.indicator_opacity)

        # Update tray tooltip
        self.tray.setToolTip(f"Turbo Whisper - Press {hotkey_str} to dictate")
        self.toggle_action.setText(f"Start Recording ({hotkey_str})")

    def _on_window_focus_change(self, has_focus: bool) -> None:
        """Handle window focus changes to disable/enable hotkey."""
        logger.info(f"Window focus changed: has_focus={has_focus}")
        if self.hotkey_manager:
            if has_focus:
                # Window gained focus - disable hotkey to avoid triggering while editing
                logger.info("Window focused - disabling hotkey")
                try:
                    self.hotkey_manager.stop()
                except Exception as e:
                    logger.error(f"Failed to stop hotkey manager: {e}")
            else:
                # Window lost focus - re-enable hotkey
                logger.info("Window unfocused - re-enabling hotkey")
                try:
                    self.hotkey_manager.start()
                except Exception as e:
                    logger.error(f"Failed to start hotkey manager: {e}")

    def _restart_hotkey_manager(self, new_hotkey: list[str] = None, temp_stop: bool = False) -> None:
        """Restart hotkey manager with new hotkey combination."""
        if temp_stop:
            # Temporarily stop hotkey manager (for key capture)
            if self.hotkey_manager:
                self.hotkey_manager.stop()
                logger.info("Hotkey manager stopped for key capture")
            return

        if new_hotkey:
            self.config.hotkey = new_hotkey

        if self.hotkey_manager:
            self.hotkey_manager.stop()

        self.hotkey_manager = create_hotkey_manager(
            self.config.hotkey,
            lambda: self.signals.toggle_recording.emit(),
        )
        if self.hotkey_manager:
            self.hotkey_manager.start()
            logger.info(f"Hotkey manager restarted with: {self.config.hotkey}")

    def _is_main_window_focused(self) -> bool:
        """Check if the main window is the foreground window."""
        try:
            # Use Qt's isActiveWindow which is thread-safe
            return self.window.isActiveWindow()
        except Exception:
            return False

    def _toggle_recording(self) -> None:
        now = time.time()
        if hasattr(self, '_last_toggle') and (now - self._last_toggle) < 0.25:
            logger.debug(f"_toggle_recording: debounced ({(now - self._last_toggle)*1000:.0f}ms since last)")
            return
        self._last_toggle = now

        if self.is_stopping:
            logger.debug(f"_toggle_recording: still stopping, rejecting")
            return

        if self.is_processing:
            thread = self._processing_thread
            if thread is not None and thread.is_alive():
                print(f"_toggle_recording: still processing, rejecting toggle at {now:.3f}")
                self._floating_indicator.set_status("Still processing...", "#f59e0b")
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
        """Start recording audio - batch or streaming mode."""
        if self.is_recording:
            print("_start_recording: already recording, skipping")
            return

        logger.info(f"_start_recording: streaming_mode={self.config.streaming_mode}, "
                   f"silence_threshold={self.config.silence_threshold_ms}ms, "
                   f"energy_threshold={self.config.silence_energy_threshold}")

        print("_start_recording: starting...")
        self.is_recording = True
        self.toggle_action.setText("Stop Recording")
        self._update_icons(recording=True)

        # Update floating indicator
        if self.config.streaming_mode:
            self._floating_indicator.set_status("Listening...", "#84cc16")
        else:
            hotkey_str = "+".join(k.capitalize() for k in self.config.hotkey)
            self._floating_indicator.set_status(f"Press {hotkey_str} to stop", "#84cc16")
        self._floating_indicator.set_recording(True)

        # Reset auto-stop timer (tracking time since last insert)
        self._last_insert_time = time.time()
        self._recording_start_time = time.time()

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

        # Reset streaming state
        self._pending_chunk_texts = []
        self._chunk_count = 0
        self._current_session_text = ""

        self._pending_waveform_data = None
        self._waveform_timer.start()
        self._auto_stop_timer.start()  # Start auto-stop timer

        if self.config.streaming_mode:
            # Streaming mode: show window and use VAD
            self._start_streaming_recording()
        else:
            # Batch mode: hide window, record all then transcribe
            try:
                self.recorder.start(level_callback=self._on_audio_level)
                print("_start_recording: recorder started (batch mode)")
            except Exception as e:
                print(f"_start_recording FAILED: {e}")
                self.is_recording = False
                self.toggle_action.setText("Start Recording")
                self._update_icons(recording=False)
                self._waveform_timer.stop()
                self._show_notification("Turbo Whisper", f"Microphone error: {e}", QSystemTrayIcon.MessageIcon.Critical)

    def _start_streaming_recording(self) -> None:
        """Start recording in streaming mode with time-based chunking."""
        logger.info(f"Starting streaming mode: chunk_duration={self.config.chunk_duration_seconds}s, "
                   f"auto_stop={self.config.auto_stop_timeout}s")

        # Stop background mic first if running
        if self.recorder.is_recording:
            logger.info("Stopping background mic before starting streaming")
            self.recorder.stop()
            time.sleep(0.1)  # Small delay to ensure clean stop

        self._chunk_count = 0
        self._streaming_session_id += 1

        # Reset ordered chunk processing state
        self._next_chunk_to_type = 1
        self._chunk_results = {}
        # Clear queue
        while not self._chunk_queue.empty():
            try:
                self._chunk_queue.get_nowait()
            except:
                break
        # Start the ordered processing timer
        self._chunk_order_timer.start()

        try:
            self.recorder.start(
                level_callback=self._on_audio_level,
                streaming_mode=True,
                on_chunk_ready=self._on_chunk_ready,
                on_auto_stop=self._on_auto_stop,
                chunk_interval_seconds=self.config.chunk_duration_seconds,
            )
            logger.info("Streaming recording started successfully")
            # Show floating indicator
            self._floating_indicator.set_status("Listening...", "#84cc16")
            self._floating_indicator.start()
        except Exception as e:
            logger.error(f"Streaming recording FAILED: {e}")
            self.is_recording = False
            self.toggle_action.setText("Start Recording")
            self._update_icons(recording=False)
            self._waveform_timer.stop()
            self._chunk_order_timer.stop()
            self.window.hide()
            self._show_notification("Turbo Whisper", f"Microphone error: {e}", QSystemTrayIcon.MessageIcon.Critical)

    def _on_auto_stop(self) -> None:
        """Called when auto-stop timeout is reached (no speech detected)."""
        import threading
        logger.info(f"Auto-stop triggered (thread={threading.current_thread().name}), emitting toggle_recording")
        # Use signals to toggle recording from main thread
        self.signals.toggle_recording.emit()

    def _on_chunk_ready(self, chunk_audio: bytes) -> None:
        """Called when a chunk boundary is reached (streaming mode)."""
        if len(chunk_audio) < self.config.min_chunk_bytes:
            return

        self._chunk_count += 1
        chunk_seq = self._chunk_count
        logger.info(f"_on_chunk_ready: chunk #{chunk_seq}, {len(chunk_audio)} bytes, session_id={self._streaming_session_id}")

        def transcribe_chunk(_seq=chunk_seq, _session_id=self._streaming_session_id):
            logger.info(f"transcribe_chunk #{_seq}: started, session_id={_session_id}")
            time.sleep(0.5)
            if _session_id != self._streaming_session_id:
                logger.info(f"transcribe_chunk #{_seq}: STALE session {_session_id} != {self._streaming_session_id}, discarding")
                return
            self.signals.show_status.emit(f"Transcribing chunk #{_seq}...")
            self._floating_indicator.set_status("Transcribing...", "#f59e0b")
            try:
                logger.info(f"transcribe_chunk #{_seq}: calling API...")
                text = self.client.transcribe_sync(chunk_audio)
                logger.info(f"transcribe_chunk #{_seq}: API OK, len={len(text) if text else 0}, preview='{text[:80] if text else 'EMPTY'}'")
                self._chunk_results[_seq] = text
                self._chunk_queue.put(_seq)
            except Exception as e:
                logger.error(f"transcribe_chunk #{_seq}: API FAILED: {e}")
                self._chunk_results[_seq] = None
                self._chunk_queue.put(_seq)

        thread = threading.Thread(target=transcribe_chunk, daemon=True)
        thread.start()
        self._chunk_transcription_threads.append(thread)

    def _update_insert_time(self) -> None:
        """Update the last insert time (called after successful text insert)."""
        self._last_insert_time = time.time()

    def _check_auto_stop(self) -> None:
        """Check recording limits: auto-stop (streaming) / max duration (batch)."""
        if not self.is_recording:
            return

        if self.config.streaming_mode:
            # Streaming: auto-stop on silence after speech
            timeout = self.config.auto_stop_timeout
            if timeout > 0:
                elapsed = time.time() - self._last_insert_time
                if elapsed >= timeout:
                    logger.info(f"Auto-stop: no text inserted for {elapsed:.0f}s (timeout={timeout}s)")
                    self._stop_recording()
        else:
            # Batch: max recording duration
            max_dur = self.config.max_recording_seconds
            if max_dur > 0:
                elapsed = time.time() - self._recording_start_time
                if elapsed >= max_dur:
                    logger.info(f"Max recording duration reached: {elapsed:.0f}s (limit={max_dur}s)")
                    self._stop_recording()

    def _on_chunk_transcription_complete(self, text: str) -> None:
        """Called when a chunk transcription completes (streaming mode).
        This is now mainly for backward compatibility - actual processing
        is done by _process_chunk_queue for proper ordering.
        """
        pass

    def _process_chunk_queue(self) -> None:
        """Process chunks in order from the queue."""
        while not self._chunk_queue.empty():
            try:
                chunk_seq = self._chunk_queue.get_nowait()
            except:
                break

            # Check if this is the next expected chunk
            if chunk_seq != self._next_chunk_to_type:
                # Put it back and wait for earlier chunks
                self._chunk_queue.put(chunk_seq)
                break

            # Get the transcription result
            text = self._chunk_results.pop(chunk_seq, None)
            self._next_chunk_to_type += 1

            # Filter out empty/invalid transcriptions
            if not text or not text.strip():
                logger.debug(f"Chunk #{chunk_seq}: empty transcription, skipping")
                continue

            # Filter out common model artifacts
            text = text.strip()
            if _is_hallucination(text):
                logger.debug(f"Chunk #{chunk_seq}: model artifact '{text[:50]}', skipping")
                continue

            logger.info(f"Chunk #{chunk_seq}: typing '{text[:50]}'")

            # Type the chunk text, then add space after
            if self.config.auto_paste:
                self.typer.type_text(text)
                self._update_insert_time()
                # Add space after chunk for separation
                self.typer.type_text(" ")

            # Accumulate text for full message
            self._pending_chunk_texts.append(text)

            # Show transcribed text briefly
            display = text[:40] + "..." if len(text) > 40 else text
            self.window.set_status(f"Transcribed: {display}")
            self._floating_indicator.set_status("Listening...", "#84cc16")

    def _cancel_recording(self) -> None:
        """Cancel recording and clean up."""
        if not self.is_recording:
            return
        self.is_recording = False
        self.toggle_action.setText("Start Recording")
        self._update_icons(recording=False)
        self._waveform_timer.stop()
        self._auto_stop_timer.stop()
        self._floating_indicator.set_recording(False)
        if hasattr(self, '_chunk_order_timer'):
            self._chunk_order_timer.stop()
        self.recorder.stop()

        # Hide window if streaming mode
        if self.config.streaming_mode:
            self.window.hide()

        # Update floating indicator
        self._floating_indicator.set_status("Cancelled", "#f59e0b")
        QTimer.singleShot(1500, self._floating_indicator.set_idle)

    def _stop_recording(self) -> None:
        """Stop recording and process audio."""
        if not self.is_recording:
            print("_stop_recording: not recording, returning")
            return

        # Block hotkey while stopping
        self.is_stopping = True

        # Show stopping indicator immediately
        if self.config.streaming_mode:
            self._floating_indicator.set_status("Finishing...", "#f59e0b")
        else:
            self._floating_indicator.set_status("Stopping...", "#f59e0b")

        self.is_recording = False
        self.toggle_action.setText("Start Recording")
        self._update_icons(recording=False)
        self._waveform_timer.stop()
        self._auto_stop_timer.stop()
        self._floating_indicator.set_recording(False)

        if self.config.streaming_mode:
            # Streaming mode: combine chunks into full message
            self._stop_streaming_recording()
        else:
            # Batch mode: transcribe full audio
            self._stop_batch_recording()

        self.is_stopping = False


    def _stop_streaming_recording(self) -> None:
        """Stop streaming recording — stop mic, show Finishing...,
        complete in-flight transcriptions, flush remaining audio."""
        logger.info("_stop_streaming_recording: stop mic then finish")

        # 1. Kill auto-stop first (prevents signal spam from recorder thread)
        self.recorder._on_auto_stop = None
        self.recorder._on_chunk_ready = None
        self.recorder._chunk_interval_frames = 0
        self._chunk_order_timer.stop()

        # 2. Flush remaining chunk BEFORE stopping (needs _streaming_mode=True)
        remaining_chunk = self.recorder.flush_remaining_chunk()

        # 3. Stop microphone
        self.recorder._streaming_mode = False
        audio_data = self.recorder.stop()

        # 4. Show Finishing... while we process remaining
        self._floating_indicator.set_status("Finishing...", "#f59e0b")

        # 5. Wait for in-flight transcriptions (max 10s)
        wait_start = time.time()
        while self._chunk_transcription_threads and (time.time() - wait_start) < 10:
            alive = [t for t in self._chunk_transcription_threads if t.is_alive()]
            if not alive:
                break
            logger.info(f"Waiting for {len(alive)} transcription threads...")
            time.sleep(0.2)

        # 6. Process the chunk queue (ordered)
        self._process_chunk_queue()

        # 7. Transcribe remaining chunk (already flushed at step 2)
        if remaining_chunk and len(remaining_chunk) >= self.config.min_chunk_bytes:
            logger.info(f"Processing final chunk: {len(remaining_chunk)} bytes")
            try:
                text = self.client.transcribe_sync(remaining_chunk)
                if text:
                    clean_text = text.strip()
                    if not _is_hallucination(clean_text) and clean_text:
                        if self.config.auto_paste:
                            self.typer.type_text(clean_text)
                            self._update_insert_time()
                            self.typer.type_text(" ")
                        self._pending_chunk_texts.append(clean_text)
                        logger.info(f"Final chunk transcribed: '{clean_text[:50]}'")
            except Exception as e:
                logger.error(f"Final chunk failed: {e}")

        logger.info(f"_stop_streaming_recording: {len(self._pending_chunk_texts)} chunks total")

        # 6. Save to history and show Done!
        full_text = " ".join(self._pending_chunk_texts).strip()
        if full_text:
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

            self.config.add_to_history(full_text, audio_file=audio_filename)
            self.window._refresh_history()

        # Hide main window
        self.window.hide()

        # Show summary on floating indicator
        if full_text:
            self._floating_indicator.set_status("Done!", "#84cc16")
        else:
            self._floating_indicator.set_status("No speech detected", "#f59e0b")

        self._floating_indicator.set_idle()

        # Clean up
        self._pending_chunk_texts = []
        self._chunk_count = 0

    def _stop_batch_recording(self) -> None:
        """Stop batch recording and transcribe full audio."""
        self._floating_indicator.set_status("Processing...", "#f59e0b")
        audio_data = self.recorder.stop()
        print(f"_stop_batch_recording: got {len(audio_data)} bytes of audio")

        if len(audio_data) < self.config.min_audio_bytes:
            print(f"_stop_recording: too short ({len(audio_data)} < {self.config.min_audio_bytes}), aborting")
            self._floating_indicator.set_status("Too short", "#f59e0b")
            self._floating_indicator.set_idle()
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
            self.window.update_mic_level(level)
            # Always update floating indicator with audio level
            self._floating_indicator.update_level(level)

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
        from turbo_whisper.integration_server import IntegrationServer
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
            self._floating_indicator.set_status("No speech detected", "#f59e0b")
            self._floating_indicator.set_idle()
            return

        # Filter out model artifacts
        clean_text = text.strip()
        if _is_hallucination(clean_text):
            logger.debug(f"Batch mode: model artifact '{clean_text[:50]}', skipping")
            self._floating_indicator.set_status("No speech detected", "#f59e0b")
            self._floating_indicator.set_idle()
            return

        self.config.add_to_history(clean_text, audio_file=audio_filename)
        self.window._refresh_history()

        if self.config.copy_to_clipboard:
            self.typer.copy_to_clipboard(clean_text)

        print(f"_on_transcription_complete: text='{clean_text[:60]}' auto_paste={self.config.auto_paste} copy_to_clipboard={self.config.copy_to_clipboard}")

        if self.config.auto_paste:
            if self._wait_for_claude_ready():
                print(f"_on_transcription_complete: calling typer.type_text()...")
                result = self.typer.type_text(clean_text)
                print(f"_on_transcription_complete: typer.type_text() returned {result}")
                self._floating_indicator.set_status("Done!", "#84cc16")
            else:
                self._floating_indicator.set_status("Copied (Claude busy)", "#f59e0b")
        else:
            self._floating_indicator.set_status("Done!", "#84cc16")

        self._floating_indicator.set_idle()

    def _on_transcription_error(self, error: str) -> None:
        self.is_processing = False
        self._processing_watchdog.stop()
        logger.error(f"Transcription error: {error}")
        # Show more informative error message
        if "429" in error or "rate limit" in error.lower():
            self._floating_indicator.set_status("Rate limited - try again", "#f59e0b")
        else:
            self._floating_indicator.set_status("Error", "#ef4444", error[:30])
        QTimer.singleShot(3000, self._floating_indicator.set_idle)

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
        # Shut down visualizer process
        self._floating_indicator._kill()
        self.app.quit()

    def run(self) -> int:
        if self.hotkey_manager:
            self.hotkey_manager.start()
        # Start floating indicator (always visible)
        self._floating_indicator.start()
        # Set initial opacity
        self._floating_indicator.set_opacity(self.config.indicator_opacity)
        # Start background microphone for visual feedback
        self._start_background_mic()
        return self.app.exec()

    def _start_background_mic(self) -> None:
        """Start microphone in background for visual feedback."""
        try:
            self.recorder.start(level_callback=self._on_audio_level)
            self._waveform_timer.start()
            logger.info("Background microphone started")
        except Exception as e:
            logger.error(f"Failed to start background mic: {e}")

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
    # When launched as a child visualizer process (PyInstaller build),
    # run the visualizer event loop instead of the main app.
    if "--visualizer" in sys.argv:
        from turbo_whisper.visualizer_process import main as viz_main
        viz_main()
        return

    ensure_single_instance()
    app = TurboWhisper()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
