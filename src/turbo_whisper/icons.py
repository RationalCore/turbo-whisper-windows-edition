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


# App icon (M10, final): giant white microphone with cyan sound waves on a
# dark slate gradient. Static: does not change with app state.
_ICON_APP = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1E293B"/><stop offset="1" stop-color="#0F172A"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="512" height="512" rx="112" fill="url(#bg)"/>
  <path d="M 103 96 A 156 156 0 0 1 103 158" stroke="#22D3EE" stroke-width="22" fill="none" stroke-linecap="round" opacity="0.55"/>
  <path d="M 409 96 A 156 156 0 0 0 409 158" stroke="#22D3EE" stroke-width="22" fill="none" stroke-linecap="round" opacity="0.55"/>
  <path d="M 52 85 A 208 208 0 0 1 52 169" stroke="#22D3EE" stroke-width="22" fill="none" stroke-linecap="round" opacity="0.55"/>
  <path d="M 460 85 A 208 208 0 0 0 460 169" stroke="#22D3EE" stroke-width="22" fill="none" stroke-linecap="round" opacity="0.55"/>
  <rect x="178" y="8" width="156" height="233" rx="78" fill="#FFFFFF"/>
  <path d="M 53 206 A 225 225 0 0 0 459 206" stroke="#FFFFFF" stroke-width="42" fill="none" stroke-linecap="round"/>
  <rect x="239" y="312" width="34" height="166" fill="#FFFFFF"/>
  <rect x="177" y="477" width="159" height="27" rx="14" fill="#FFFFFF"/>
</svg>"""


def get_tray_icon(size: int = 64, recording: bool = False) -> QIcon:
    """Get the app icon for the system tray.

    The icon is static and deliberately does NOT change color while
    recording — a recolored tray icon made the app look like a different
    program. The floating indicator shows the recording state instead.

    A multi-resolution QIcon is built (16..64 px) so Windows always picks
    an exact size without blurry rescaling.

    Args:
        size: Icon size in pixels (kept for call compatibility)
        recording: kept for call compatibility, unused
    """
    icon = QIcon()
    for s in (16, 20, 24, 32, 48, 64):
        icon.addPixmap(_svg_to_icon(_ICON_APP, s).pixmap(s, s))
    return icon
