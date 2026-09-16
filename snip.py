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

from PySide6.QtCore import QPoint, QPropertyAnimation, QRect, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


_TOOLBAR_CSS = """
QWidget#snipbar { background: rgba(28, 30, 36, 235); border: 1px solid rgba(255,255,255,40);
                  border-radius: 12px; }
QLabel { color: rgba(255,255,255,150); font-size: 12px; padding: 0 8px; }
QPushButton { color: #fff; background: transparent; border: 1px solid transparent;
              border-radius: 8px; padding: 6px 12px; font-size: 12px; }
QPushButton:hover { background: rgba(255,255,255,24); }
QPushButton[active="true"] { background: #2f6df6; }
"""


class _Toolbar(QWidget):
    """The pill at the top of the screen, the way the Windows snipping tool
    has one: which kind of shot this is, and how to get out. Drawn on the
    screen the pointer is on, because that is where the eyes are."""

    def __init__(self, parent: "_Overlay", on_full):
        super().__init__(parent)
        self.setObjectName("snipbar")
        # A bare QWidget paints no background of its own, stylesheet or not,
        # until told that the stylesheet is the one painting it.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_TOOLBAR_CSS)
        self.setCursor(Qt.ArrowCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        region = QPushButton("Region")
        region.setProperty("active", "true")
        region.setToolTip("Drag a box around what to show")
        region.setFocusPolicy(Qt.NoFocus)
        full = QPushButton("Full screen")
        full.setToolTip("The whole of this screen, no drag")
        full.setFocusPolicy(Qt.NoFocus)
        full.clicked.connect(on_full)
        lay.addWidget(region)
        lay.addWidget(full)
        lay.addWidget(QLabel("Drag to select  ·  Esc to cancel"))
        self.adjustSize()

    def place(self, area: QRect) -> None:
        self.adjustSize()
        self.move(area.x() + (area.width() - self.width()) // 2, area.y() + 18)


class _Overlay(QWidget):
    """One monitor's worth of dimmed screen, with a hole where the drag is."""

    def __init__(self, screen, on_done, with_toolbar: bool = False):
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
        self._toolbar = None
        if with_toolbar:
            # Full screen from here is the same pixels, already in hand: the
            # whole grab, untouched by the dimming.
            self._toolbar = _Toolbar(self, lambda: self._on_done(self._shot))
            self._toolbar.place(self.rect())

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
    the toolbar happens to be parked on. The pill with "Region · Full screen"
    sits on the screen the pointer is on.
    """
    from PySide6.QtCore import QEventLoop
    result: dict = {}
    overlays: list[_Overlay] = []
    loop = QEventLoop()
    here = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

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
        o = _Overlay(screen, done, with_toolbar=(screen is here))
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
    if pixmap is not None and not pixmap.isNull():
        flash(here)
    return _save(pixmap, out_dir, name)


def grab_full(out_dir: str | Path, screen=None, name: str = "screenshot") -> str | None:
    """The whole of one screen, no drag. Returns the PNG's path, or None.

    One screen, not all of them stitched: the thing worth showing is on the
    screen the pointer is on, and a picture of three monitors is mostly a
    picture of two that do not matter.
    """
    screen = screen or QGuiApplication.primaryScreen()
    if screen is None:
        return None
    shot = screen.grabWindow(0)
    flash(screen)
    return _save(shot, out_dir, name)


def _save(pixmap, out_dir: str | Path, name: str) -> str | None:
    if pixmap is None or pixmap.isNull():
        return None
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    path = out / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.png"
    return str(path) if pixmap.save(str(path), "PNG") else None


def flash(screen) -> None:
    """A white blink over one screen, gone in a quarter of a second.

    A full-screen shot has no drag and no crosshair, so without this nothing
    visibly happens between the click and the bar coming back — and a thing
    that gives no sign of working gets clicked again. The blink is what every
    camera does, and it costs nothing: one frameless window fading out."""
    if screen is None:
        return
    w = QWidget()
    w.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                     | Qt.WindowTransparentForInput)
    w.setStyleSheet("background: white;")
    w.setGeometry(screen.geometry())
    w.setWindowOpacity(0.85)
    w.show()
    anim = QPropertyAnimation(w, b"windowOpacity", w)
    anim.setDuration(260)
    anim.setStartValue(0.85)
    anim.setEndValue(0.0)
    # A top-level widget with no parent lives exactly as long as its Python
    # reference, and this function returns before the fade has begun. Held
    # here until the animation lets go of it.
    _flashes.append(w)

    def gone():
        w.close()
        if w in _flashes:
            _flashes.remove(w)
    anim.finished.connect(gone)
    anim.start()


_flashes: list = []
