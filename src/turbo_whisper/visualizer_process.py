"""Visualizer subprocess — runs FloatingIndicator in its own Qt event loop.

Started by FloatingIndicatorProcess (main.py) via QProcess.
Communicates via JSON lines on stdin:
  {"type":"level","value":0.5}
  {"type":"status","text":"Recording...","color":"#ef4444","sub_text":""}
  {"type":"idle"}
  {"type":"hotkey","text":"F8"}
  {"type":"exit"}
  {"type":"show"}
  {"type":"hide"}
  {"type":"opacity","value":180}
  {"type":"recording","active":true}
  {"type":"always_on_top","on":false}

CLI: <hotkey> [--version X.Y.Z]
"""

import json
import logging
import math
import os
import sys
import threading
from collections import deque
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QRect, QRectF, QObject, pyqtSignal
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

    def __init__(self, hotkey_str: str = "F8", version: str = ""):
        super().__init__()
        self._hotkey_str = hotkey_str
        self._app_version = version

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._full_size = (220, 88)
        self._compact_size = (110, 44)
        self._compact = False  # True = half-size window (waveform only)
        self._width, self._height = self._full_size
        self.setFixedSize(self._width, self._height)

        self._drag_pos = None
        self._is_dragging = False
        self._resizing = False
        self._resize_anchor = (0, 0)
        self._resize_press = (0, 0)
        self._resize_sx = 1
        self._resize_sy = 1
        self._resize_by_width = True
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
        self._always_on_top = True
        self._user_hidden = False  # True when user disabled "Show Indicator"

        # Must call setWindowOpacity on Windows to properly initialize a
        # layered window (WA_TranslucentBackground). Value 1.0 means "no
        # global multiplier" — transparency is controlled per-pixel in
        # paintEvent via self._bg_alpha.
        self.setWindowOpacity(1.0)

        self._timer = QTimer()
        self._timer.timeout.connect(self._animate)
        self._timer.setInterval(33)

        self._load_position()
        self._watch_screens()

    def start(self):
        self._timer.start()
        self.show()
        self.raise_()
        # Also run on every showEvent; this covers first show immediately
        self._ensure_on_screen()

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

    def set_always_on_top(self, on: bool):
        """Toggle always-on-top z-order.

        When off, the indicator sits behind the settings window.
        Never shows the window if the user explicitly hid it via
        "Show Indicator" — that takes priority.
        """
        self._always_on_top = on
        if self._user_hidden:
            return
        if on:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.show()
            self.raise_()
        else:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            self.show()

    # ── position persistence ─────────────────────────────────────────────

    def _apply_size(self, compact: bool):
        """Switch between full and compact (half) size, keeping top-left."""
        self._compact = compact
        self._width, self._height = self._compact_size if compact else self._full_size
        self.setFixedSize(self._width, self._height)
        self._minimize_rect = QRectF(
            self._width - self._btn_size - self._btn_margin,
            self._btn_margin,
            self._btn_size,
            self._btn_size,
        )
        self.update()

    def _place_at_anchor(self):
        """Keep the resize-anchor corner fixed while the size changed."""
        ax, ay = self._resize_anchor
        x = ax if self._resize_sx > 0 else ax - self._width + 1
        y = ay if self._resize_sy > 0 else ay - self._height + 1
        self.move(x, y)

    def showEvent(self, event):
        """Every show goes through position validation."""
        super().showEvent(event)
        self._ensure_on_screen()

    def _watch_screens(self):
        """Re-validate the position whenever the monitor layout changes."""
        app = QGuiApplication.instance()
        if app is None:
            return
        app.screenAdded.connect(self._watch_screen)
        app.screenRemoved.connect(self._on_screen_change)
        app.primaryScreenChanged.connect(self._on_screen_change)
        for screen in app.screens():
            self._watch_screen(screen)

    def _watch_screen(self, screen):
        screen.availableGeometryChanged.connect(self._on_screen_change)
        screen.geometryChanged.connect(self._on_screen_change)

    def _on_screen_change(self, *_args):
        if self.isVisible():
            self._ensure_on_screen()

    def _ensure_on_screen(self, *_args):
        """Guarantee the window is fully inside one of the available screens.

        Runs on every show and on every monitor layout change. Never saves
        the corrected position — only user-dragged positions are persisted,
        so a temporarily disconnected monitor does not lose the position.
        """
        screens = QGuiApplication.screens()
        if not screens:
            logger.info("No screens available for validation")
            return
        w = self.width() or self._width
        h = self.height() or self._height
        x, y = self.x(), self.y()
        win = QRect(x, y, w, h)

        for screen in screens:
            if screen.availableGeometry().contains(win):
                return  # fully visible on some screen — keep user position

        # Not fully inside any single screen — clamp into the closest one
        def _overlap(screen):
            inter = screen.availableGeometry().intersected(win)
            return inter.width() * inter.height()

        best = max(screens, key=_overlap)
        if _overlap(best) == 0:
            # Fully outside every screen (e.g. a monitor was unplugged)
            logger.info("Position (%d, %d) is off-screen, using default", x, y)
            self._position_on_screen()
            return
        geo = best.availableGeometry()
        nx = min(max(x, geo.x()), geo.x() + geo.width() - w)
        ny = min(max(y, geo.y()), geo.y() + geo.height() - h)
        logger.info("Clamping position (%d, %d) -> (%d, %d) on screen %s",
                    x, y, nx, ny, best.name())
        self.move(nx, ny)

    def _position_on_screen(self):
        """Place in the bottom-right corner of the primary screen (not saved)."""
        screen = QGuiApplication.primaryScreen() or self.screen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.right() - self._width - 20
            y = geo.bottom() - self._height - 20
            self.move(x, y)

    def _load_position(self):
        """Load position from config. Applies unconditionally; validation in _ensure_on_screen."""
        try:
            config_path = _get_config_dir() / "indicator_position.json"
            if config_path.exists():
                with open(config_path) as f:
                    pos = json.load(f)
                x = pos.get("x", 100)
                y = pos.get("y", 100)
                self._apply_size(bool(pos.get("compact", False)))
                logger.info("Loaded position from config: (%d, %d) compact=%s", x, y, self._compact)
                self.move(x, y)
                return
        except Exception as e:
            logger.info("Error loading position: %s", e)

    def _save_position(self):
        try:
            config_path = _get_config_dir() / "indicator_position.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w") as f:
                json.dump({"x": self.x(), "y": self.y(), "compact": self._compact}, f)
        except Exception:
            pass

    # ── animation ─────────────────────────────────────────────────────────

    def _animate(self):
        self._current_level += (self._target_level - self._current_level) * 0.3
        self._target_level *= 0.85

        self._bar_values.append(self._current_level)
        self._level_history.append(self._current_level)
        self._scroll_offset += 0.15

        self._frame_count += 1
        self.update()

    # ── drag + click-to-close + resize ──────────────────────────────────

    _RESIZE_MARGIN = 10

    def _resize_zone(self, pos):
        """Borders near the cursor: (near_left, near_right, near_top, near_bottom)."""
        m = self._RESIZE_MARGIN
        w, h = self.width(), self.height()
        zone = (pos.x() < m, pos.x() > w - m, pos.y() < m, pos.y() > h - m)
        return zone if any(zone) else None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            if self._minimize_rect.contains(pos):
                self._clicked_minimize = True
                event.accept()
                return
            self._clicked_minimize = False

            zone = self._resize_zone(pos)
            if zone:
                near_left, near_right, near_top, near_bottom = zone
                g = self.frameGeometry()
                # Anchor = corner opposite to the grabbed border
                self._resize_sx = 1 if not near_left else -1
                self._resize_sy = 1 if not near_top else -1
                self._resize_by_width = near_left or near_right
                ax = g.left() if self._resize_sx > 0 else g.right()
                ay = g.top() if self._resize_sy > 0 else g.bottom()
                self._resize_anchor = (ax, ay)
                self._resize_press = (event.globalPosition().toPoint().x(),
                                      event.globalPosition().toPoint().y())
                self._resizing = True
                self._is_dragging = False
                event.accept()
                return

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

        if self._resizing:
            # Discrete jump between the two sizes: dragging slightly inward
            # snaps to compact immediately, outward — back to full.
            gpos = event.globalPosition().toPoint()
            ax, ay = self._resize_anchor
            px, py = self._resize_press
            if self._resize_by_width:
                cur = (gpos.x() - ax) * self._resize_sx
                press = (px - ax) * self._resize_sx
            else:
                cur = (gpos.y() - ay) * self._resize_sy
                press = (py - ay) * self._resize_sy
            threshold = 6
            if cur < press - threshold and not self._compact:
                self._apply_size(True)
                self._place_at_anchor()
                self._save_position()
            elif cur > press + threshold and self._compact:
                self._apply_size(False)
                self._place_at_anchor()
                self._save_position()
            event.accept()
            return

        if self._is_dragging and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return

        # Cursor feedback near borders (resize zones)
        if not self._is_dragging:
            zone = self._resize_zone(event.position())
            if zone:
                l, r, t, b = zone
                if (l or r) and (t or b):
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif l or r:
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                else:
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                self.unsetCursor()

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
            if self._resizing:
                # Size is already applied discretely during the drag
                self._resizing = False
                self._save_position()
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
        if self._compact:
            bars_y, bars_bg_h, bars_h = 4, h - 8, h - 12
        else:
            bars_y, bars_bg_h, bars_h = 6, 46, 43
        painter.drawRoundedRect(6, bars_y, w - 12, bars_bg_h, 6, 6)
        self._draw_waveform_bars(painter, 8, bars_y + 2, w - 16, bars_h)

        # 3. Status texts + version — full mode only.
        #    Compact mode shows just the bar (plus the minimize button).
        if not self._compact:
            font = QFont("Segoe UI", 10, QFont.Weight.Bold)
            painter.setFont(font)
            painter.setPen(self._status_color)
            metrics = painter.fontMetrics()
            tw = metrics.horizontalAdvance(self._status_text)
            painter.drawText((w - tw) // 2, h - 20, self._status_text)

            if self._sub_text:
                sf = QFont("Segoe UI", 8)
                painter.setFont(sf)
                painter.setPen(self._sub_color)
                sw = metrics.horizontalAdvance(self._sub_text)
                painter.drawText((w - sw) // 2, h - 5, self._sub_text)

            # Version label (bottom-right)
            if self._app_version:
                vfont = QFont("Segoe UI", 7)
                painter.setFont(vfont)
                painter.setPen(QColor(150, 150, 165, 160))
                vtext = self._app_version if self._app_version.startswith("v") else f"v{self._app_version}"
                vw = painter.fontMetrics().horizontalAdvance(vtext)
                painter.drawText(w - vw - 8, h - 5, vtext)

        # Minimize button (top-right corner). In compact mode it floats over
        # the bars — an opaque backing keeps it clearly visible.
        if self._compact:
            painter.setBrush(QColor(15, 15, 25, 235))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(self._minimize_rect.adjusted(-3, -3, 3, 3))
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
    version = ""
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--version" and i + 1 < len(args):
            version = args[i + 1]
            i += 2
            continue
        if not arg.startswith("--"):
            hotkey = arg
        i += 1

    window = _IndicatorWindow(hotkey_str=hotkey, version=version)
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
            window._user_hidden = False
            window.show()
            window.raise_()
        elif t == "hide":
            window._user_hidden = True
            window.hide()
        elif t == "opacity":
            window.set_opacity(cmd.get("value", 180))
        elif t == "always_on_top":
            window.set_always_on_top(cmd.get("on", True))

    reader.command_received.connect(handle_command)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
