"""Pick a region of the screen, the way every other screenshot tool does.

Drag a box, release, done. Esc or a right-click cancels; a click with no drag
cancels too, because a stray click is far more often a miss than a request to
send a one-pixel picture.

Why this and not `ms-screenclip`:

  * it puts the image on the CLIPBOARD and tells the caller nothing — no path,
    no signal that the person finished, no way to tell "cancelled" from "still
    choosing". A toolbar that has to poll the clipboard and guess is a toolbar
    that sends the wrong picture eventually.
  * it is a Store app. It can be missing, and on a locked-down machine it can
    be blocked outright.

Qt already owns the screen here, so the whole thing is one frameless window per
monitor and a rubber band.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class _Overlay(QWidget):
    """One monitor's worth of dimmed screen, with a hole where the drag is."""

    def __init__(self, screen, on_done):
        super().__init__()
        self._on_done = on_done
        self._origin: QPoint | None = None
        self._rect = QRect()
        self.setScreen(screen)
        # Frameless, always on top, and no taskbar entry: this is a mode, not a
        # window someone should be able to alt-tab away from and lose.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(screen.geometry())
        # The pixels are grabbed BEFORE the overlay is shown. Grabbing after
        # would capture the dimming, and on a multi-monitor setup the other
        # overlays too.
        self._shot = screen.grabWindow(0)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if self._rect.isNull():
            return
        # The selection shows the screen as it really is, undimmed, so what you
        # see inside the box is exactly what gets sent.
        ratio = self._shot.devicePixelRatio() or 1.0
        src = QRect(int(self._rect.x() * ratio), int(self._rect.y() * ratio),
                    int(self._rect.width() * ratio), int(self._rect.height() * ratio))
        p.drawPixmap(self._rect, self._shot, src)
        p.setPen(QPen(QColor(120, 170, 255), 1))
        p.drawRect(self._rect.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            self._on_done(None)          # right-click is cancel, as everywhere else
            return
        self._origin = e.position().toPoint()
        self._rect = QRect(self._origin, self._origin)
        self.update()

    def mouseMoveEvent(self, e):
        if self._origin is None:
            return
        self._rect = QRect(self._origin, e.position().toPoint()).normalized()
        self.update()

    def mouseReleaseEvent(self, e):
        if self._origin is None or e.button() != Qt.LeftButton:
            return
        r = QRect(self._origin, e.position().toPoint()).normalized()
        self._origin = None
        # A click with no drag is a miss, not a request for a 3x2 picture.
        if r.width() < 8 or r.height() < 8:
            self._on_done(None)
            return
        ratio = self._shot.devicePixelRatio() or 1.0
        src = QRect(int(r.x() * ratio), int(r.y() * ratio),
                    int(r.width() * ratio), int(r.height() * ratio))
        self._on_done(self._shot.copy(src))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._on_done(None)


def grab(out_dir: str | Path, name: str = "screenshot") -> str | None:
    """Let the person pick a region. Returns the PNG's path, or None.

    Covers every monitor, because the thing worth showing is rarely on the one
    the toolbar happens to be parked on.
    """
    from PySide6.QtCore import QEventLoop
    result: dict = {}
    overlays: list[_Overlay] = []
    loop = QEventLoop()

    def done(pixmap):
        # Guarded: every monitor has an overlay and any of them can answer, so
        # the first one to finish wins and the rest are just closed. Without
        # this, releasing the mouse over one screen while another still has a
        # stale drag would overwrite the answer.
        if result.get("done"):
            return
        result["done"] = True
        result["pixmap"] = pixmap
        for o in overlays:
            o.close()
        loop.quit()

    for screen in QGuiApplication.screens():
        o = _Overlay(screen, done)
        overlays.append(o)
    for o in overlays:
        o.show()
        o.raise_()
    if overlays:
        overlays[0].activateWindow()

    # Modal without a modal dialog: the overlays ARE the mode, and this returns
    # when one of them answers. A QEventLoop rather than a dialog's exec(), so
    # the tray icon and any running recording keep ticking underneath.
    if overlays:
        loop.exec()
    overlays.clear()

    pixmap = result.get("pixmap")
    if pixmap is None or pixmap.isNull():
        return None

    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    path = out / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.png"
    return str(path) if pixmap.save(str(path), "PNG") else None
