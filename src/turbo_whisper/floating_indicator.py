"""Floating audio indicator widget - persistent overlay with waveform bars and status."""

import atexit
import json
import logging
import math
import os
import sys
from collections import deque
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, QProcess
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QLinearGradient
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger("turbo_whisper.indicator")


def _get_config_dir() -> Path:
    """Get config directory path."""
    import sys
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "turbo-whisper"
    else:
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "turbo-whisper"


class FloatingIndicator(QWidget):
    """Persistent floating widget with waveform bars, status text, and drag support.

    Always visible from app start. Shows hotkey hint when idle,
    waveform visualization and status during recording.
    """

    def __init__(self, hotkey_str: str = "F8", parent=None):
        super().__init__(parent)

        self._hotkey_str = hotkey_str

        # Window flags: frameless, always on top, tool window (no taskbar)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Size - taller to show text
        self._width = 220
        self._height = 100
        self.setFixedSize(self._width, self._height)

        # Drag state
        self._drag_pos = None
        self._is_dragging = False
        self._previous_focus_window = None
        self._on_double_click = None  # Callback for double-click event

        # Audio state
        self._current_level = 0.0
        self._target_level = 0.0
        self._level_history = deque(maxlen=50)

        # Status
        self._status_text = f"Press {hotkey_str} to dictate"
        self._status_color = QColor("#888888")
        self._sub_text = ""
        self._sub_color = QColor("#666666")

        # Wave bars
        self._num_bars = 24
        self._bar_values = deque(maxlen=self._num_bars)
        self._scroll_offset = 0.0  # For continuous scrolling animation

        # Animation timer (~30 FPS)
        self._timer = QTimer()
        self._timer.timeout.connect(self._animate)
        self._timer.setInterval(33)

        # Load saved position
        self._load_position()

    def start(self):
        """Start the floating indicator."""
        self._timer.start()
        self.show()
        self.raise_()

    def stop(self):
        """Stop the floating indicator (hide but don't destroy)."""
        self._timer.stop()

    def update_level(self, level: float):
        """Update audio level (0.0 to 1.0)."""
        self._target_level = min(1.0, level * 4.0)

    def set_status(self, text: str, color: str = "#84cc16", sub_text: str = ""):
        """Update status text, color, and optional sub-text."""
        self._status_text = text
        self._status_color = QColor(color)
        self._sub_text = sub_text
        if sub_text:
            self._sub_color = QColor("#666666")

    def set_idle(self):
        """Set to idle state with hotkey hint."""
        self._status_text = f"Press {self._hotkey_str} to dictate"
        self._status_color = QColor("#888888")
        self._sub_text = ""
        self._target_level = 0.0

    def _position_on_screen(self):
        """Position in the bottom-right corner of the screen."""
        screen = self.screen()
        if screen:
            geo = screen.geometry()
            x = geo.right() - self._width - 20
            y = geo.bottom() - self._height - 20
            self.move(x, y)
            self._save_position()

    def _load_position(self):
        """Load saved position from config."""
        try:
            config_path = _get_config_dir() / "indicator_position.json"
            if config_path.exists():
                with open(config_path, "r") as f:
                    pos = json.load(f)
                self.move(pos.get("x", 100), pos.get("y", 100))
                return
        except Exception:
            pass
        self._position_on_screen()

    def _save_position(self):
        """Save current position to config."""
        try:
            config_path = _get_config_dir() / "indicator_position.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            pos = {"x": self.x(), "y": self.y()}
            with open(config_path, "w") as f:
                json.dump(pos, f)
        except Exception:
            pass

    def _animate(self):
        """Animation tick."""
        # Smooth interpolation
        self._current_level += (self._target_level - self._current_level) * 0.3
        self._target_level *= 0.85

        # Update bar values
        self._bar_values.append(self._current_level)

        # Store history
        self._level_history.append(self._current_level)

        # Continuous scroll animation (right to left)
        self._scroll_offset += 0.15

        self.update()

    def mousePressEvent(self, event):
        """Start drag on any mouse button."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._is_dragging = True
            # Remember which window had focus before we took it
            self._previous_focus_window = self._get_foreground_window()
            event.accept()

    def mouseMoveEvent(self, event):
        """Drag the window."""
        if self._is_dragging and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        """End drag, save position, and restore focus to previous window."""
        if self._is_dragging:
            self._is_dragging = False
            self._drag_pos = None
            self._save_position()
            # Restore focus to the window that had it before dragging
            self._restore_previous_focus()
            event.accept()

    def _get_foreground_window(self):
        """Get current foreground window handle."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            return user32.GetForegroundWindow()
        except Exception:
            return None

    def _restore_previous_focus(self):
        """Restore focus to the window that had it before dragging."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            if hasattr(self, '_previous_focus_window') and self._previous_focus_window:
                hwnd = self._previous_focus_window
                # Only restore if the window still exists
                if user32.IsWindow(hwnd):
                    user32.SetForegroundWindow(hwnd)
                    logger.info(f"Restored focus to hwnd={hwnd}")
        except Exception as e:
            logger.debug(f"Could not restore focus: {e}")

    def mouseDoubleClickEvent(self, event):
        """Open main window on double-click."""
        if event.button() == Qt.MouseButton.LeftButton:
            # Emit signal to open main window
            if hasattr(self, '_on_double_click') and self._on_double_click:
                self._on_double_click()
            event.accept()

    def paintEvent(self, event):
        """Draw the semi-transparent widget with waveform bars and text."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self._width
        h = self._height

        # Semi-transparent background
        bg_color = QColor(15, 15, 25, 180)
        painter.setBrush(bg_color)
        painter.setPen(QPen(QColor(80, 80, 100, 100), 1))
        painter.drawRoundedRect(0, 0, w, h, 8, 8)

        # Draw waveform bars in upper portion
        bars_height = 45
        bars_y = 8
        self._draw_waveform_bars(painter, 8, bars_y, w - 16, bars_height)

        # Draw status text
        font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(self._status_color)

        # Center status text
        metrics = painter.fontMetrics()
        text_width = metrics.horizontalAdvance(self._status_text)
        text_x = (w - text_width) // 2
        text_y = h - 30
        painter.drawText(text_x, text_y, self._status_text)

        # Draw sub-text if present
        if self._sub_text:
            font_small = QFont("Segoe UI", 8)
            painter.setFont(font_small)
            painter.setPen(self._sub_color)
            sub_width = metrics.horizontalAdvance(self._sub_text)
            sub_x = (w - sub_width) // 2
            sub_y = h - 12
            painter.drawText(sub_x, sub_y, self._sub_text)

        painter.end()

    def _draw_waveform_bars(self, painter, x, y, width, height):
        """Draw vertical waveform bars with continuous scrolling animation."""
        bar_width = max(2, (width - (self._num_bars - 1) * 2) // self._num_bars)
        gap = 2
        total_width = self._num_bars * (bar_width + gap) - gap
        start_x = x + (width - total_width) // 2

        mid_y = y + height / 2

        for i in range(self._num_bars):
            # Generate wave value with scrolling offset for continuous movement
            # Each bar position gets a wave value based on scroll offset + position
            pos = (i + self._scroll_offset) * 0.4

            # Multiple sine waves for organic look
            wave1 = math.sin(pos * 1.0) * 0.3
            wave2 = math.sin(pos * 2.3 + 1.5) * 0.2
            wave3 = math.sin(pos * 0.7 + 3.0) * 0.25
            wave4 = math.sin(pos * 3.7 + 0.8) * 0.15

            # Combine waves
            base_wave = (wave1 + wave2 + wave3 + wave4) * 0.5 + 0.5  # Normalize to 0-1

            # Add current audio level on top
            level_influence = self._current_level * 0.6
            val = base_wave * 0.4 + level_influence * base_wave

            # Add some randomness from history
            if i < len(self._bar_values):
                val += self._bar_values[i] * 0.2

            val = max(0.05, min(1.0, val))

            # Bar height
            bar_height = max(3, val * height * 0.85)

            # Bar position
            bar_x = start_x + i * (bar_width + gap)
            bar_y_top = mid_y - bar_height / 2

            # Color - green with varying intensity
            if val > 0.5:
                color = QColor(132, 204, 22)  # Bright green
                alpha = min(200, 100 + int(val * 100))
            elif val > 0.2:
                color = QColor(100, 180, 40)  # Medium green
                alpha = 80
            else:
                color = QColor(60, 100, 40)  # Dim green
                alpha = 50

            color.setAlpha(alpha)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)

            # Draw rounded bar
            painter.drawRoundedRect(
                QRectF(bar_x, bar_y_top, bar_width, bar_height),
                2, 2
            )


class FloatingIndicatorProcess:
    """Drop-in replacement for FloatingIndicator that runs in a subprocess.

    Launches visualizer_process.py via QProcess. The child process owns
    its own QApplication + QWidget and renders the waveform without any
    blocking from the main application thread.

    Set _on_double_click to a callable to handle double-click events.
    """

    _instance = None  # keep a strong ref so QProcess doesn't get GC'd

    def __new__(cls, *args, **kwargs):
        inst = super().__new__(cls)
        cls._instance = inst
        return inst

    def __init__(self, hotkey_str: str = "F8"):
        # Prevent re-init on the same instance
        if getattr(self, "_started", False):
            return
        self._started = True

        self._on_double_click = None

        self._on_right_click = None

        self._on_left_click = None

        self._hotkey_str = hotkey_str
        self._proc = QProcess()

        # Route stderr of the child to our logging
        self._proc.readyReadStandardError.connect(self._read_stderr)
        # Route stdout for double-click signals from the child
        self._proc.readyReadStandardOutput.connect(self._read_stdout)
        self._proc.finished.connect(self._on_finished)

        # Keep stdin pipe open, forward stderr, capture stdout
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)

        atexit.register(self._kill)

    # ── public API (mirrors FloatingIndicator) ────────────────────────────

    def start(self):
        """Launch the visualizer subprocess."""
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            return  # already running

        # Locate the visualizer_process.py module
        module_dir = Path(__file__).parent
        script = module_dir / "visualizer_process.py"
        if not script.exists():
            logger.error("visualizer_process.py not found at %s", script)
            return

        python = sys.executable
        self._proc.start(python, [str(script), self._hotkey_str])
        if not self._proc.waitForStarted(3000):
            logger.error("Visualizer process failed to start")
            return

        logger.info("Visualizer process started (pid=%d)", self._proc.processId())

    def stop(self):
        self._send({"type": "hide"})

    def update_level(self, level: float):
        self._send({"type": "level", "value": level})

    def set_status(self, text: str, color: str = "#84cc16", sub_text: str = ""):
        self._send({"type": "status", "text": text, "color": color, "sub_text": sub_text})

    def update_hotkey(self, hotkey_str: str):
        """Update hotkey string in the visualizer process."""
        self._hotkey_str = hotkey_str
        self._send({"type": "hotkey", "text": hotkey_str})

    def set_recording(self, active: bool):
        """Notify visualizer whether recording is active (green) or idle (grey)."""
        self._send({"type": "recording", "active": active})

    def set_opacity(self, value: int):
        """Set visualizer window opacity (30-255, 255=opaque)."""
        self._send({"type": "opacity", "value": value})

    def set_idle(self):
        self._send({"type": "idle"})

    # ── internal helpers ──────────────────────────────────────────────────

    def _send(self, cmd: dict):
        """Write one JSON line to the child's stdin."""
        proc = self._proc
        if proc.state() != QProcess.ProcessState.Running:
            return
        try:
            line = json.dumps(cmd, ensure_ascii=False) + "\n"
            proc.write(line.encode("utf-8"))
        except Exception as e:
            logger.debug("Visualizer send error: %s", e)

    def _read_stderr(self):
        data = self._proc.readAllStandardError().data().decode("utf-8", errors="replace")
        if data.strip():
            logger.info("[visualizer] %s", data.strip())

    def _read_stdout(self):
        """Read stdout from the child (for double-click and right-click events)."""
        data = self._proc.readAllStandardOutput().data().decode("utf-8", errors="replace")
        for line in data.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
                t = cmd.get("type")
                if t == "doubleclick" and self._on_double_click:
                    self._on_double_click()
                elif t == "rightclick" and self._on_right_click:
                    self._on_right_click()
                elif t == "leftclick" and self._on_left_click:
                    self._on_left_click()
            except json.JSONDecodeError:
                pass

    def _on_finished(self, exit_code, exit_status):
        logger.info("Visualizer process exited (code=%d, status=%s)", exit_code, exit_status)
        FloatingIndicatorProcess._instance = None

    def _kill(self):
        """Terminate the subprocess on shutdown."""
        try:
            if self._proc.state() == QProcess.ProcessState.Running:
                self._send({"type": "exit"})
                if not self._proc.waitForFinished(2000):
                    logger.warning("Visualizer process did not exit gracefully, terminating")
                    self._proc.kill()
                    self._proc.waitForFinished(1000)
        except Exception:
            pass
