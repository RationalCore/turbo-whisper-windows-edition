"""Visualizer subprocess — runs FloatingIndicator in its own Qt event loop.

Started by FloatingIndicatorProcess (main.py) via QProcess.
Communicates via JSON lines on stdin:
  {"type":"level","value":0.5}
  {"type":"status","text":"Recording...","color":"#ef4444","sub_text":""}
  {"type":"idle"}
  {"type":"hotkey","text":"F8"}
  {"type":"exit"}
"""

import json
import logging
import math
import os
import sys
import threading
from collections import deque
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QRectF, QObject, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QFont
from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger("turbo_whisper.visualizer")


# ── lightweight copy of FloatingIndicator (kept self-contained) ────────────

def _get_config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "turbo-whisper"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "turbo-whisper"


class _IndicatorWindow(QWidget):
    """Floating waveform indicator — exact copy of FloatingIndicator logic."""

    def __init__(self, hotkey_str: str = "F8"):
        super().__init__()
        self._hotkey_str = hotkey_str

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._width = 220
        self._height = 100
        self.setFixedSize(self._width, self._height)

        self._drag_pos = None
        self._is_dragging = False
        self._previous_focus_window = None

        self._current_level = 0.0
        self._target_level = 0.0
        self._level_history = deque(maxlen=50)

        self._status_text = f"Press {hotkey_str} to dictate"
        self._status_color = QColor("#888888")
        self._sub_text = ""
        self._sub_color = QColor("#666666")

        self._num_bars = 24
        self._bar_values = deque(maxlen=self._num_bars)
        self._scroll_offset = 0.0
        self._is_recording = False

        self._timer = QTimer()
        self._timer.timeout.connect(self._animate)
        self._timer.setInterval(33)

        self._load_position()

    def start(self):
        self._timer.start()
        self.show()
        self.raise_()

    def stop(self):
        self._timer.stop()

    def update_level(self, level: float):
        self._target_level = min(1.0, level * 4.0)

    def set_status(self, text: str, color: str = "#84cc16", sub_text: str = ""):
        self._status_text = text
        self._status_color = QColor(color)
        self._sub_text = sub_text
        if sub_text:
            self._sub_color = QColor("#666666")

    def set_idle(self):
        self._status_text = f"Press {self._hotkey_str} to dictate"
        self._status_color = QColor("#888888")
        self._sub_text = ""
        self._target_level = 0.0

    def set_recording(self, active: bool):
        """Switch between recording (green) and idle (grey) color scheme."""
        self._is_recording = active

    # ── position persistence ─────────────────────────────────────────────

    def _position_on_screen(self):
        screen = self.screen()
        if screen:
            geo = screen.geometry()
            x = geo.right() - self._width - 20
            y = geo.bottom() - self._height - 20
            self.move(x, y)
            self._save_position()

    def _load_position(self):
        try:
            config_path = _get_config_dir() / "indicator_position.json"
            if config_path.exists():
                with open(config_path) as f:
                    pos = json.load(f)
                self.move(pos.get("x", 100), pos.get("y", 100))
                return
        except Exception:
            pass
        self._position_on_screen()

    def _save_position(self):
        try:
            config_path = _get_config_dir() / "indicator_position.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w") as f:
                json.dump({"x": self.x(), "y": self.y()}, f)
        except Exception:
            pass

    # ── animation ─────────────────────────────────────────────────────────

    def _animate(self):
        self._current_level += (self._target_level - self._current_level) * 0.3
        self._target_level *= 0.85

        self._bar_values.append(self._current_level)
        self._level_history.append(self._current_level)
        self._scroll_offset += 0.15

        self.update()

    # ── drag ──────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._is_dragging = True
            self._previous_focus_window = self._get_foreground_window()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._is_dragging and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._is_dragging:
            self._is_dragging = False
            self._drag_pos = None
            self._save_position()
            self._restore_previous_focus()
            event.accept()

    def _get_foreground_window(self):
        try:
            import ctypes
            return ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            return None

    def _restore_previous_focus(self):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            if self._previous_focus_window and user32.IsWindow(self._previous_focus_window):
                user32.SetForegroundWindow(self._previous_focus_window)
        except Exception:
            pass

    def mouseDoubleClickEvent(self, event):
        """Notify parent process on double-click."""
        if event.button() == Qt.MouseButton.LeftButton:
            try:
                sys.stdout.write('{"type":"doubleclick"}\n')
                sys.stdout.flush()
            except OSError:
                pass
            event.accept()

    def contextMenuEvent(self, event):
        """Notify parent process on right-click (for tray-style menu)."""
        try:
            sys.stdout.write('{"type":"rightclick"}\n')
            sys.stdout.flush()
        except OSError:
            pass
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self._width, self._height

        bg_color = QColor(15, 15, 25, 180)
        painter.setBrush(bg_color)
        painter.setPen(QPen(QColor(80, 80, 100, 100), 1))
        painter.drawRoundedRect(0, 0, w, h, 8, 8)

        bars_h = 45
        self._draw_waveform_bars(painter, 8, 8, w - 16, bars_h)

        font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(self._status_color)
        metrics = painter.fontMetrics()
        tw = metrics.horizontalAdvance(self._status_text)
        painter.drawText((w - tw) // 2, h - 30, self._status_text)

        if self._sub_text:
            sf = QFont("Segoe UI", 8)
            painter.setFont(sf)
            painter.setPen(self._sub_color)
            sw = metrics.horizontalAdvance(self._sub_text)
            painter.drawText((w - sw) // 2, h - 12, self._sub_text)

        painter.end()

    def _draw_waveform_bars(self, painter, x, y, width, height):
        bar_w = max(2, (width - (self._num_bars - 1) * 2) // self._num_bars)
        gap = 2
        tw = self._num_bars * (bar_w + gap) - gap
        sx = x + (width - tw) // 2
        mid_y = y + height / 2

        for i in range(self._num_bars):
            pos = (i + self._scroll_offset) * 0.4
            wave1 = math.sin(pos * 1.0) * 0.3
            wave2 = math.sin(pos * 2.3 + 1.5) * 0.2
            wave3 = math.sin(pos * 0.7 + 3.0) * 0.25
            wave4 = math.sin(pos * 3.7 + 0.8) * 0.15
            base_wave = (wave1 + wave2 + wave3 + wave4) * 0.5 + 0.5
            level_influence = self._current_level * 0.6
            val = base_wave * 0.4 + level_influence * base_wave
            if i < len(self._bar_values):
                val += self._bar_values[i] * 0.2
            val = max(0.05, min(1.0, val))

            bar_h = max(3, val * height * 0.85)
            bx = sx + i * (bar_w + gap)
            by = mid_y - bar_h / 2

            if self._is_recording:
                if val > 0.5:
                    color = QColor(132, 204, 22)
                    alpha = min(200, 100 + int(val * 100))
                elif val > 0.2:
                    color = QColor(100, 180, 40)
                    alpha = 80
                else:
                    color = QColor(60, 100, 40)
                    alpha = 50
            else:
                if val > 0.5:
                    color = QColor(180, 180, 180)
                    alpha = min(160, 60 + int(val * 80))
                elif val > 0.2:
                    color = QColor(140, 140, 140)
                    alpha = 60
                else:
                    color = QColor(100, 100, 100)
                    alpha = 40
            color.setAlpha(alpha)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(bx, by, bar_w, bar_h), 2, 2)


# ── stdin command reader (thread-based, works on all platforms) ────────────

class _StdinReader(QObject):
    """Reads JSON commands from stdin in a daemon thread."""
    command_received = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Signal the reader thread to stop."""
        self._running = False

    def _read_loop(self):
        """Blocking loop: read one JSON line at a time from stdin."""
        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break  # EOF — parent closed the pipe
                line = line.strip()
                if not line:
                    continue
                cmd = json.loads(line)
                self.command_received.emit(cmd)
            except (json.JSONDecodeError, OSError, ValueError):
                break


# ── process entry point ───────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.info("Visualizer process started (pid=%d)", os.getpid())

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Read hotkey from command-line arg (passed by FloatingIndicatorProcess)
    hotkey = sys.argv[1] if len(sys.argv) > 1 else "F8"

    window = _IndicatorWindow(hotkey_str=hotkey)
    window.start()

    reader = _StdinReader()

    def handle_command(cmd: dict):
        t = cmd.get("type", "")
        if t == "exit":
            logger.info("Exit command received, shutting down")
            window.stop()
            app.quit()
        elif t == "level":
            window.update_level(cmd.get("value", 0.0))
        elif t == "status":
            window.set_status(
                cmd.get("text", ""),
                cmd.get("color", "#84cc16"),
                cmd.get("sub_text", ""),
            )
        elif t == "idle":
            window.set_idle()
        elif t == "recording":
            window.set_recording(cmd.get("active", False))
        elif t == "hotkey":
            window._hotkey_str = cmd.get("text", "F8")
            window.set_idle()
        elif t == "show":
            window.show()
            window.raise_()
        elif t == "hide":
            window.hide()

    reader.command_received.connect(handle_command)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
