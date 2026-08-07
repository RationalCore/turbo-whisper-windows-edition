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
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QGuiApplication
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
            | Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._width = 220
        self._height = 100
        self.setFixedSize(self._width, self._height)

        self._drag_pos = None
        self._is_dragging = False
        self._previous_focus_window = None
        self._clicked_minimize = False  # True if mouseDown was on minimize button

        # Minimize button geometry (top-right corner)
        self._btn_size = 16
        self._btn_margin = 4
        self._minimize_rect = QRectF(
            self._width - self._btn_size - self._btn_margin,
            self._btn_margin,
            self._btn_size,
            self._btn_size,
        )
        self._minimize_hover = False
        self.setMouseTracking(True)

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
        self._bg_alpha = 235  # matches default config indicator_opacity
        self._frame_count = 0

        # Must call setWindowOpacity on Windows to properly initialize a
        # layered window (WA_TranslucentBackground). Value 1.0 means "no
        # global multiplier" — transparency is controlled per-pixel in
        # paintEvent via self._bg_alpha.
        self.setWindowOpacity(1.0)

        self._timer = QTimer()
        self._timer.timeout.connect(self._animate)
        self._timer.setInterval(33)

        self._load_position()

    def start(self):
        self._timer.start()
        self.show()
        self.raise_()
        # Re-apply saved position after window is shown (screen geometry is reliable now)
        self._load_position()

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

    def set_opacity(self, value: int):
        """Set background opacity (15-255, 255=opaque).
        
        Only affects the window background/frame. Waveform bars and text
        remain fully opaque regardless of this setting.
        """
        self._bg_alpha = max(15, min(255, value))
        self.update()

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
                x = pos.get("x", 100)
                y = pos.get("y", 100)
                # Check if position is visible on ANY connected screen
                for screen in QGuiApplication.screens():
                    geo = screen.geometry()
                    if (x + self._width > geo.x() and x < geo.right() and
                            y + self._height > geo.y() and y < geo.bottom()):
                        self.move(x, y)
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

        # Periodically re-raise to stay on top of all windows
        self._frame_count += 1
        if self._frame_count % 60 == 0:
            self.raise_()

        self.update()

    # ── drag + click-to-close ──────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            if self._minimize_rect.contains(pos):
                self._clicked_minimize = True
                event.accept()
                return
            self._clicked_minimize = False
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._is_dragging = True
            self._previous_focus_window = self._get_foreground_window()
            event.accept()

    def mouseMoveEvent(self, event):
        # Track hover over minimize button
        hover = self._minimize_rect.contains(event.position())
        if hover != self._minimize_hover:
            self._minimize_hover = hover
            self.update()
        if self._is_dragging and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._clicked_minimize:
                self._clicked_minimize = False
                try:
                    sys.stdout.write('{"type":"minimize"}\n')
                    sys.stdout.flush()
                except OSError:
                    pass
                event.accept()
                return
            if self._is_dragging:
                self._is_dragging = False
                self._drag_pos = None
                self._save_position()
                try:
                    sys.stdout.write('{"type":"leftclick"}\n')
                    sys.stdout.flush()
                except OSError:
                    pass
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

        # 1. Full window background — opacity follows _bg_alpha
        bg_color = QColor(15, 15, 25, self._bg_alpha)
        painter.setBrush(bg_color)
        painter.setPen(QPen(QColor(80, 80, 100, self._bg_alpha), 1))
        painter.drawRoundedRect(0, 0, w, h, 8, 8)

        # 2. Opaque background under the waveform bars so they stay
        #    clearly visible at any opacity setting.
        bars_bg = QColor(15, 15, 25, min(255, self._bg_alpha + 20))
        painter.setBrush(bars_bg)
        painter.setPen(Qt.PenStyle.NoPen)
        bars_area_h = 48
        painter.drawRoundedRect(6, 6, w - 12, bars_area_h, 6, 6)

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

        # Minimize button (top-right corner)
        btn_color = QColor(120, 120, 140, 200) if self._minimize_hover else QColor(80, 80, 100, 140)
        painter.setBrush(btn_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self._minimize_rect)
        # Draw "−" line
        line_color = QColor(220, 220, 220) if self._minimize_hover else QColor(160, 160, 170)
        painter.setPen(QPen(line_color, 1.5))
        cx = self._minimize_rect.center().x()
        cy = self._minimize_rect.center().y()
        painter.drawLine(int(cx - 4), int(cy), int(cx + 4), int(cy))

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
                    alpha = 255
                elif val > 0.2:
                    color = QColor(100, 180, 40)
                    alpha = 255
                else:
                    color = QColor(60, 100, 40)
                    alpha = 255
            else:
                if val > 0.5:
                    color = QColor(255, 255, 255)
                    alpha = 200
                elif val > 0.2:
                    color = QColor(255, 255, 255)
                    alpha = 140
                else:
                    color = QColor(255, 255, 255)
                    alpha = 80
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

    try:
        app = QApplication(sys.argv)
    except Exception as e:
        print(f"[visualizer] FATAL: Cannot create QApplication: {e}", file=sys.stderr)
        sys.exit(1)
    app.setQuitOnLastWindowClosed(False)

    # Read hotkey from command-line arg (passed by FloatingIndicatorProcess).
    # When launched via PyInstaller EXE with --visualizer flag, skip the flag.
    hotkey = "F8"
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            hotkey = arg
            break

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
        elif t == "opacity":
            window.set_opacity(cmd.get("value", 180))

    reader.command_received.connect(handle_command)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
