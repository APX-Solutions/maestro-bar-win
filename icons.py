"""Icons drawn from inline SVG.

Windows has no SF Symbols, so the four marks the Mac strip uses are redrawn
here as paths. Keeping them in the source means the app has no image files to
lose and looks identical on every machine.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SVG = {
    "tray": "M3 14h4.5l1.5 2.5h6L16.5 14H21M4.5 4.5h15l1.5 9.5v5.5h-18V14z",
    "pencil": "M4 20h4L19.5 8.5l-4-4L4 16zM14.5 5.5l4 4",
    "record": "M12 3a9 9 0 100 18 9 9 0 000-18z M12 8.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7z",
    "monitor": "M3.5 4.5h17v11h-17zM9 19h6M12 15.5V19",
    "mic": "M12 3.5a2.5 2.5 0 012.5 2.5v5a2.5 2.5 0 01-5 0V6A2.5 2.5 0 0112 3.5z"
           "M6.5 10.5a5.5 5.5 0 0011 0M12 16v4",
    "send": "M12 20V5M6 11l6-6 6 6",
    "check": "M5 12.5l4.5 4.5L19 7",
    "left": "M14.5 5.5L8 12l6.5 6.5",
    "right": "M9.5 5.5L16 12l-6.5 6.5",
    "more": "M6 12h.01M12 12h.01M18 12h.01",
    "close": "M6 6l12 12M18 6L6 18",
}

_FILLED = {"record"}


def icon(name: str, color: str = "#e8e8e8", size: int = 22) -> QIcon:
    path = _SVG.get(name, _SVG["tray"])
    fill = color if name in _FILLED else "none"
    rule = ' fill-rule="evenodd"' if name in _FILLED else ""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<path d="{path}" fill="{fill}"{rule} stroke="{color}" stroke-width="1.6" '
        f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return QIcon(pm)
