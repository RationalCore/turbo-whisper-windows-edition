"""Lucide icons for Turbo Whisper UI."""

from PyQt6.QtCore import QByteArray
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer


def _svg_to_icon(svg_content: str, size: int = 24, color: str = "#888888") -> QIcon:
    """Convert SVG string to QIcon with specified color."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage

    # Replace stroke color in SVG
    svg_with_color = svg_content.replace('stroke="currentColor"', f'stroke="{color}"')

    # Create pixmap from SVG with proper transparency
    svg_bytes = QByteArray(svg_with_color.encode())
    renderer = QSvgRenderer(svg_bytes)

    # Use QImage for proper alpha channel support
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    renderer.render(painter)
    painter.end()

    pixmap = QPixmap.fromImage(image)
    return QIcon(pixmap)


# Lucide icon SVGs (24x24 viewBox, stroke-based)
ICON_POWER = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M12 2v10"/>
  <path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>
</svg>"""

ICON_COPY = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>
  <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>
</svg>"""

ICON_EYE = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/>
  <circle cx="12" cy="12" r="3"/>
</svg>"""

ICON_EYE_OFF = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49"/>
  <path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"/>
  <path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"/>
  <path d="m2 2 20 20"/>
</svg>"""

ICON_CHEVRON_DOWN = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="m6 9 6 6 6-6"/>
</svg>"""

ICON_CHEVRON_UP = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="m18 15-6-6-6 6"/>
</svg>"""

ICON_CHECK = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M20 6 9 17l-5-5"/>
</svg>"""

ICON_PLAY = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <polygon points="6 3 20 12 6 21 6 3"/>
</svg>"""

ICON_STOP = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <rect width="14" height="14" x="5" y="5" rx="2" ry="2"/>
</svg>"""


def get_close_icon(size: int = 20, color: str = "#888888") -> QIcon:
    """Get the power/close icon."""
    return _svg_to_icon(ICON_POWER, size, color)


def get_copy_icon(size: int = 20, color: str = "#888888") -> QIcon:
    """Get the copy icon."""
    return _svg_to_icon(ICON_COPY, size, color)


def get_eye_icon(size: int = 20, color: str = "#888888") -> QIcon:
    """Get the eye (visible) icon."""
    return _svg_to_icon(ICON_EYE, size, color)


def get_eye_off_icon(size: int = 20, color: str = "#888888") -> QIcon:
    """Get the eye-off (hidden) icon."""
    return _svg_to_icon(ICON_EYE_OFF, size, color)


def get_chevron_down_icon(size: int = 20, color: str = "#888888") -> QIcon:
    """Get the chevron-down icon."""
    return _svg_to_icon(ICON_CHEVRON_DOWN, size, color)


def get_chevron_up_icon(size: int = 20, color: str = "#888888") -> QIcon:
    """Get the chevron-up icon."""
    return _svg_to_icon(ICON_CHEVRON_UP, size, color)


def get_check_icon(size: int = 20, color: str = "#888888") -> QIcon:
    """Get the check/tick icon."""
    return _svg_to_icon(ICON_CHECK, size, color)


def get_play_icon(size: int = 20, color: str = "#888888") -> QIcon:
    """Get the play icon."""
    return _svg_to_icon(ICON_PLAY, size, color)


def get_stop_icon(size: int = 20, color: str = "#888888") -> QIcon:
    """Get the stop icon."""
    return _svg_to_icon(ICON_STOP, size, color)


# V5 Rounded Modern icon — microphone on green background
_ICON_V5_GREEN = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.5" y2="1">
      <stop offset="0%" stop-color="#84cc16"/>
      <stop offset="100%" stop-color="#4d7c0f"/>
    </linearGradient>
  </defs>
  <rect x="16" y="16" width="224" height="224" rx="48" ry="48" fill="url(#bg)"/>
  <rect x="108" y="60" width="40" height="65" rx="20" fill="#ffffff"/>
  <path d="M88 115 Q88 150 128 150 Q168 150 168 115" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/>
  <line x1="128" y1="150" x2="128" y2="170" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/>
  <line x1="105" y1="170" x2="151" y2="170" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/>
  <path d="M75 90 Q58 128 75 166" fill="none" stroke="#ffffff" stroke-width="3.5" stroke-linecap="round" opacity="0.7"/>
  <path d="M181 90 Q198 128 181 166" fill="none" stroke="#ffffff" stroke-width="3.5" stroke-linecap="round" opacity="0.7"/>
  <text x="128" y="210" font-family="Arial,sans-serif" font-size="28" font-weight="bold" fill="#ffffff" fill-opacity="0.25" text-anchor="middle">W</text>
</svg>"""

_ICON_V5_ORANGE = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.5" y2="1">
      <stop offset="0%" stop-color="#f97316"/>
      <stop offset="100%" stop-color="#c2410c"/>
    </linearGradient>
  </defs>
  <rect x="16" y="16" width="224" height="224" rx="48" ry="48" fill="url(#bg)"/>
  <rect x="108" y="60" width="40" height="65" rx="20" fill="#ffffff"/>
  <path d="M88 115 Q88 150 128 150 Q168 150 168 115" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/>
  <line x1="128" y1="150" x2="128" y2="170" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/>
  <line x1="105" y1="170" x2="151" y2="170" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/>
  <path d="M75 90 Q58 128 75 166" fill="none" stroke="#ffffff" stroke-width="3.5" stroke-linecap="round" opacity="0.7"/>
  <path d="M181 90 Q198 128 181 166" fill="none" stroke="#ffffff" stroke-width="3.5" stroke-linecap="round" opacity="0.7"/>
  <text x="128" y="210" font-family="Arial,sans-serif" font-size="28" font-weight="bold" fill="#ffffff" fill-opacity="0.25" text-anchor="middle">W</text>
</svg>"""


def get_tray_icon(size: int = 64, recording: bool = False) -> QIcon:
    """Get the V5 rounded-mic icon for the system tray.

    Args:
        size: Icon size in pixels
        recording: If True, green. If False, orange.
    """
    svg = _ICON_V5_GREEN if recording else _ICON_V5_ORANGE
    return _svg_to_icon(svg, size)
