"""Maestro Bar for Windows.

A thin strip of icons parked against the edge of the screen. Clicking one
opens a small flyout beside it. The strip can be dragged anywhere and snaps
back to the nearer edge. Everything it shows comes from bar.json, the same
file the Mac app reads.

Run:    python maestro_bar.py
Build:  build.bat   (produces dist\\MaestroBar.exe)
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPushButton,
                               QSystemTrayIcon, QTextEdit, QToolButton,
                               QVBoxLayout, QWidget)

import api
import config
from icons import icon
from recorder import Recorder

ITEM = 34            # one icon cell on the strip
BG = QColor(58, 58, 60, 236)
FG = "#e8e8e8"
DIM = "#9a9a9e"


class Bridge(QObject):
    """pynput runs on its own thread; Qt work has to be handed back."""
    hotkey = Signal()
    api_result = Signal(object, int, str)
    count = Signal(str, int)
    recorder_changed = Signal()
    recorder_message = Signal(str, str)


# --------------------------------------------------------------------------
# the flyout
# --------------------------------------------------------------------------

class Flyout(QWidget):
    def __init__(self, app: "MaestroBar", section: dict):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.app = app
        self.section = section
        self.rows: list[dict] = []
        self.index = 0
        self.loading = bool(section.get("list"))

        width = int(app.sidebar.get("flyout_width", 330))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)

        self.title = QLabel("")
        self.title.setWordWrap(True)
        self.title.setStyleSheet(f"color:{FG};font-size:13px;font-weight:600;")
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet(f"color:{DIM};font-size:11px;")
        self.counter = QLabel("")
        self.counter.setStyleSheet("color:#7a7a7e;font-size:11px;")

        self.body = QTextEdit()
        self.body.setReadOnly(True)
        self.body.setFrameStyle(0)
        self.body.setStyleSheet(
            f"background:transparent;color:{FG};font-size:11px;border:none;")

        self.input = QLineEdit()
        self.input.setStyleSheet(
            "background:rgba(255,255,255,0.07);border:1px solid rgba(255,255,255,0.15);"
            f"border-radius:8px;padding:6px 8px;color:{FG};font-size:11px;")
        self.input.returnPressed.connect(self.send)

        compose = section.get("compose")
        if section.get("list"):
            outer.addWidget(self.title)
            outer.addWidget(self.subtitle)
            outer.addWidget(self.body, 1)
            outer.addLayout(self._action_bar())
            if compose:
                self.input.setPlaceholderText(compose.get("placeholder", "Start typing"))
                outer.addWidget(self.input)
            self.setFixedSize(width, 240 if compose else 205)
            self.load()
        else:
            head = QLabel(section.get("title", "Capture"))
            head.setStyleSheet(f"color:{DIM};font-size:11px;font-weight:600;")
            outer.addWidget(head)
            self.input.setPlaceholderText((compose or {}).get("placeholder", "Start typing"))
            outer.addWidget(self.input)
            row = QHBoxLayout()
            row.addStretch(1)
            if (compose or {}).get("record", True):
                self.mic = self._icon_button("mic", "Record", self.app.toggle_audio)
                row.addWidget(self.mic)
            row.addWidget(self._icon_button("send", "Send", self.send))
            outer.addLayout(row)
            self.setFixedSize(width, 104)

        self.render()

    # -- chrome ------------------------------------------------------------
    def _icon_button(self, name: str, tip: str, slot) -> QToolButton:
        b = QToolButton()
        b.setIcon(icon(name, FG, 18))
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setFixedSize(26, 24)
        b.setStyleSheet("QToolButton{border:none;}"
                        "QToolButton:hover{background:rgba(255,255,255,0.12);border-radius:5px;}")
        b.clicked.connect(slot)
        return b

    def _action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(2)
        bar.addWidget(self._icon_button("left", "Previous", lambda: self.step(-1)))
        actions = self.section.get("actions") or []
        if actions:
            bar.addWidget(self._icon_button(
                "check", actions[0].get("label", "Do"),
                lambda: self.perform(actions[0])))
        if len(actions) > 1:
            more = self._icon_button("more", "More", lambda: None)
            menu = QMenu(more)
            for a in actions[1:]:
                act = QAction(a.get("label", "..."), menu)
                act.triggered.connect(lambda _=False, a=a: self.perform(a))
                menu.addAction(act)
            more.setMenu(menu)
            more.setPopupMode(QToolButton.InstantPopup)
            bar.addWidget(more)
        bar.addWidget(self._icon_button("right", "Next", lambda: self.step(1)))
        bar.addStretch(1)
        bar.addWidget(self.counter)
        return bar

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRect(0, 0, self.width(), self.height()), 12, 12)
        p.fillPath(path, BG)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.app.close_flyout()
        else:
            super().keyPressEvent(e)

    # -- data --------------------------------------------------------------
    def load(self):
        path = self.section.get("list")
        if not path or not self.app.cfg.get("api"):
            self.loading = False
            self.render()
            return
        self.loading = True
        self.render()
        self.app.api.get(path, lambda payload, code:
                         self.app.bridge.api_result.emit(payload, code, "list"))

    def took_list(self, payload, code):
        self.loading = False
        self.rows = api.rows(payload) if code == 200 else []
        self.index = 0
        self.app.set_count(self.section.get("id", ""), len(self.rows))
        self.render()

    def step(self, delta: int):
        if self.rows:
            self.index = max(0, min(len(self.rows) - 1, self.index + delta))
            self.render()

    def current(self) -> dict | None:
        return self.rows[self.index] if 0 <= self.index < len(self.rows) else None

    def perform(self, action: dict):
        row = self.current()
        if not row or not action.get("path"):
            return
        path = api.substitute(action["path"], row)
        label = action.get("label", "Action")

        def done(_payload, code):
            if 200 <= code < 300:
                if action.get("toast"):
                    self.app.notify("Maestro", action["toast"])
            else:
                self.app.notify("Maestro", f"{label} failed ({code or 'no reply'})")
                self.load()

        self.app.api.call(action.get("method", "POST"), path, action.get("body"), done)

        if action.get("advance", True):
            # Optimistic: the card leaves at once and a failure reloads the
            # list. Waiting for the round trip makes triage feel broken.
            del self.rows[self.index]
            self.index = min(self.index, max(0, len(self.rows) - 1))
            self.app.set_count(self.section.get("id", ""), len(self.rows))
            self.render()

    def send(self):
        compose = self.section.get("compose") or {}
        text = self.input.text().strip()
        if not text:
            return

        # Not every destination exists as an endpoint yet. Appending to a file
        # keeps the box useful now, and only the config changes later.
        if not compose.get("path"):
            target = compose.get("file")
            if not target:
                return
            self.input.clear()
            p = config.expand(target)
            p.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(f"\n## {stamp}\n{text}\n")
            self.app.notify("Maestro", compose.get("toast", "Captured"))
            return

        body = {compose.get("field", "text"): text, "section": self.section.get("id", "")}
        path = compose["path"]
        row = self.current()
        if row:
            path = api.substitute(path, row)
            if api.row_id(row):
                body["item_id"] = api.row_id(row)
        if "{" in path:
            self.app.notify("Maestro", "That box needs a card open")
            return
        self.input.clear()
        self.app.api.post(path, body, lambda _p, code: self.app.notify(
            "Maestro", compose.get("toast", "Sent") if 200 <= code < 300
            else f"Could not send ({code or 'no reply'})"))

    # -- drawing -----------------------------------------------------------
    def render(self):
        if not self.section.get("list"):
            return
        fields = self.section.get("fields") or {}
        row = self.current()
        if row:
            self.title.setText(api.field(row, fields.get("title", ["title"])) or "Untitled")
            sub = api.field(row, fields.get("subtitle", ["client_name"]))
            self.subtitle.setText(sub)
            self.subtitle.setVisible(bool(sub))
            self.body.setPlainText(api.field(row, fields.get("body", ["detail"])))
            self.counter.setText(f"{self.index + 1} of {len(self.rows)}")
        else:
            self.title.setText("Loading" if self.loading else "Nothing waiting")
            self.subtitle.setVisible(False)
            self.body.setPlainText(
                "" if self.loading else
                ("No API is configured." if not self.app.cfg.get("api") else ""))
            self.counter.setText("")


# --------------------------------------------------------------------------
# the strip
# --------------------------------------------------------------------------

class Strip(QWidget):
    def __init__(self, app: "MaestroBar"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.app = app
        self._drag: QPoint | None = None
        self.buttons: list[tuple[QToolButton, str]] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(7, 6, 7, 8)
        lay.setSpacing(4)

        grip = QLabel()
        grip.setFixedHeight(3)
        grip.setStyleSheet("background:rgba(255,255,255,0.35);border-radius:1px;")
        grip.setFixedWidth(16)
        lay.addWidget(grip, 0, Qt.AlignHCenter)
        lay.addSpacing(2)

        for i, s in enumerate(app.sidebar.get("sections", [])):
            b = self._button(s.get("symbol_win") or self._symbol(s), s.get("title", ""))
            b.clicked.connect(lambda _=False, i=i: app.toggle_flyout(i))
            lay.addWidget(b, 0, Qt.AlignHCenter)
            self.buttons.append((b, s.get("id", "")))

        for r in app.sidebar.get("record", []):
            mode = r.get("mode", "audio")
            b = self._button("monitor" if mode == "screen" else "record",
                             r.get("label", "Record"))
            b.clicked.connect(lambda _=False, m=mode: app.toggle_record(m))
            lay.addWidget(b, 0, Qt.AlignHCenter)
            self.buttons.append((b, "record:" + mode))

        self.setFixedWidth(int(app.sidebar.get("width", 44)))
        self.adjustSize()

    @staticmethod
    def _symbol(section: dict) -> str:
        """Map the Mac's SF Symbol names onto the drawn set."""
        name = (section.get("symbol") or "").lower()
        if "mic" in name or "pencil" in name or "square.and" in name:
            return "pencil"
        if "display" in name or "rectangle" in name:
            return "monitor"
        if "record" in name:
            return "record"
        return "tray"

    def _button(self, symbol: str, tip: str) -> QToolButton:
        b = QToolButton(self)
        b.setIcon(icon(symbol, FG))
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setFixedSize(30, 30)
        b.setStyleSheet("QToolButton{border:none;}"
                        "QToolButton:hover{background:rgba(255,255,255,0.14);border-radius:7px;}")
        return b

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRect(0, 0, self.width(), self.height()), 12, 12)
        p.fillPath(path, BG)
        # a dot in the corner of any section that has something waiting
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 69, 58))
        for b, key in self.buttons:
            if self.app.counts.get(key, 0) > 0:
                g = b.geometry()
                p.drawEllipse(g.right() - 7, g.top() + 1, 7, 7)

    # -- dragging, and snapping back to an edge ----------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
            self.app.place_flyout()

    def mouseReleaseEvent(self, _):
        self._drag = None
        self.snap()

    def snap(self):
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        f = self.frameGeometry()
        to_left = abs(f.left() - area.left())
        to_right = abs(area.right() - f.right())
        self.app.on_right = to_right <= to_left
        x = area.right() - f.width() - 8 if self.app.on_right else area.left() + 8
        y = min(max(f.top(), area.top() + 8), area.bottom() - f.height() - 8)
        self.move(x, y)
        config.write_state({"x": x, "y": y, "on_right": self.app.on_right})
        self.app.place_flyout()


# --------------------------------------------------------------------------
# the application
# --------------------------------------------------------------------------

class MaestroBar:
    def __init__(self, qt: QApplication):
        self.qt = qt
        self.bridge = Bridge()
        self.counts: dict[str, int] = {}
        self.on_right = True
        self.flyout: Flyout | None = None
        self.open_index: int | None = None

        config.install_default_if_missing()
        self.cfg, self.cfg_source = config.load()
        self.sidebar = self.cfg["sidebar"]
        self.api = api.Api(self.cfg.get("api", ""), self.cfg.get("token_service", "maestro-token"))

        self.recorder = Recorder(
            on_change=self.bridge.recorder_changed.emit,
            on_message=self.bridge.recorder_message.emit)

        self.bridge.hotkey.connect(self.toggle_strip)
        self.bridge.recorder_changed.connect(self.recording_changed)
        self.bridge.recorder_message.connect(self.notify)
        self.bridge.api_result.connect(self._api_result)
        self.bridge.count.connect(self.set_count)

        self.tray = QSystemTrayIcon(icon("tray", "#d0d0d0", 32))
        self.tray.setToolTip("Maestro Bar")
        self.tray.setContextMenu(self.build_tray_menu())
        self.tray.activated.connect(
            lambda reason: self.toggle_strip()
            if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

        self.strip = Strip(self)
        self.place_strip()
        self.strip.show()

        self.tick = QTimer()
        self.tick.timeout.connect(self.paint_tick)
        self.tick.start(1000)

        self.counts_timer = QTimer()
        self.counts_timer.timeout.connect(self.refresh_counts)
        self.counts_timer.start(max(20, int(self.sidebar.get("refresh_seconds", 90))) * 1000)
        self.refresh_counts()
        self.install_hotkey()

    # -- tray --------------------------------------------------------------
    def retry_unsent(self) -> None:
        """Push anything that could not be delivered — no token yet, no network,
        S3 refused. The media is still on disk; only the delivery failed, and a
        recording nobody can replay is a conversation lost."""
        def work() -> None:
            try:
                import send as send_mod
                sent, failed = send_mod.flush(self.cfg)
                if sent or failed:
                    self.notify("Retry finished",
                                f"{sent} sent" + (f", {failed} still waiting" if failed else ""))
                else:
                    self.notify("Nothing waiting", "Every recording has been delivered.")
            except Exception as e:  # noqa: BLE001 — a retry must not take the app down
                self.notify("Retry failed", str(e)[:120])

        threading.Thread(target=work, daemon=True).start()

    def build_tray_menu(self) -> QMenu:
        m = QMenu()
        m.addAction(QAction("Show or hide the strip", m, triggered=self.toggle_strip))
        m.addSeparator()
        m.addAction(QAction("Set API token…", m, triggered=self.ask_token))
        m.addAction(QAction("Choose microphone…", m, triggered=self.choose_microphone))
        m.addAction(QAction("Check setup", m, triggered=self.check_setup))
        m.addSeparator()
        m.addAction(QAction("Open recordings", m, triggered=self.open_recordings))
        m.addAction(QAction("Retry unsent recordings", m, triggered=self.retry_unsent))
        m.addAction(QAction("Reload config", m, triggered=self.reload))
        m.addSeparator()
        m.addAction(QAction("Quit", m, triggered=self.quit))
        return m

    def notify(self, title: str, body: str):
        self.tray.showMessage(title, body, QSystemTrayIcon.Information, 4000)

    def ask_token(self):
        service = self.cfg.get("token_service", "maestro-token")
        value, ok = QInputDialog.getText(
            None, "Maestro API token",
            "Paste the token you were given.",
            QLineEdit.Password)
        if not (ok and value.strip()):
            return
        tok = value.strip()

        # The FILE is what uploads read, so write it there first and always.
        # Credential Manager was the only store, and when keyring cannot
        # persist — no Windows backend resolved, a locked-down profile — the
        # token went nowhere and the app reported "no token" right after
        # someone had just set one.
        #
        # The Mac learned this the hard way too: its token moved out of the
        # Keychain to ~/.maestro/token because a re-signed app lost the ACL and
        # uploads stopped. A plain file survives what a credential store does
        # not.
        f = Path.home() / ".maestro" / "token"
        wrote_file = False
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(tok, encoding="utf-8")
            wrote_file = True
        except OSError:
            pass

        wrote_vault = api.write_token(service, tok)
        if wrote_file:
            self.notify("Maestro", "Token saved" if wrote_vault
                        else "Token saved to ~/.maestro/token")
        elif wrote_vault:
            self.notify("Maestro", "Token saved to Credential Manager only")
        else:
            self.notify("Maestro", "Could not save the token anywhere — "
                                   "check permissions on your user folder")

    def choose_microphone(self):
        devices = config.audio_devices()
        if not devices:
            QMessageBox.information(
                None, "Maestro Bar",
                "Windows reports no recording devices.\n\n"
                "If ffmpeg is missing, install it with:\n    winget install Gyan.FFmpeg")
            return
        name, ok = QInputDialog.getItem(None, "Choose microphone", "Recording device:",
                                        devices, 0, False)
        if ok and name:
            import json
            config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8")) \
                if config.CONFIG_PATH.exists() else {}
            data["audio_device"] = name
            config.CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
            self.reload()
            self.notify("Maestro", f"Recording from {name}")

    def check_setup(self):
        import send as send_mod
        token = api.read_token(self.cfg.get("token_service", "maestro-token"))
        file_token = send_mod.token(self.cfg)
        ff = config.ffmpeg_path()
        mic = config.microphone_allowed()
        mic_text = {True: "allowed", False: "BLOCKED in Windows settings"}.get(mic, "unknown")
        lines = [
            f"Config: {self.cfg_source}",
            f"API: {self.cfg.get('api') or 'not set'}",
            # The FILE is what the upload uses, so report it first. A token that
            # lives only in Credential Manager is a token uploads cannot see.
            f"Token: {'file (~/.maestro/token)' if file_token else ('Credential Manager only' if token else 'MISSING')}",
            f"ffmpeg: {ff or 'NOT FOUND — recording will not work'}",
            # Windows never prompts for this. A blocked microphone shows up only
            # as silence, so it belongs on the setup screen.
            f"Microphone permission: {mic_text}",
            f"Microphone device: {self.cfg.get('audio_device') or '(first one found)'}",
            f"Recordings: {config.expand(self.cfg.get('out_dir', '~/Recordings'))}",
        ]
        QMessageBox.information(None, "Maestro Bar", "\n".join(lines))

    def open_recordings(self):
        p = config.expand(self.cfg.get("out_dir", "~/Recordings"))
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(p)                                   # noqa: S606 — a folder, on Windows

    def quit(self):
        if self.recorder.is_recording:
            self.recorder.stop()
        self.qt.quit()

    # -- hotkey ------------------------------------------------------------
    def install_hotkey(self):
        combo = self.sidebar.get("hotkey", "<ctrl>+<alt>+m")
        try:
            from pynput import keyboard
            self._listener = keyboard.GlobalHotKeys({combo: self.bridge.hotkey.emit})
            self._listener.daemon = True
            self._listener.start()
        except Exception as e:                            # noqa: BLE001
            print(f"[hotkey] {combo} not registered: {e}")

    # -- strip and flyout --------------------------------------------------
    def place_strip(self):
        st = config.read_state()
        screen = QApplication.primaryScreen().availableGeometry()
        if "x" in st and "y" in st:
            self.on_right = bool(st.get("on_right", True))
            self.strip.move(int(st["x"]), int(st["y"]))
            return
        self.on_right = self.sidebar.get("edge", "right") != "left"
        w = self.strip.width()
        x = screen.right() - w - 8 if self.on_right else screen.left() + 8
        self.strip.move(x, screen.center().y() - self.strip.height() // 2)

    def toggle_strip(self):
        if self.strip.isVisible():
            self.close_flyout()
            self.strip.hide()
        else:
            self.strip.show()
            self.strip.raise_()

    def toggle_flyout(self, i: int):
        if self.open_index == i:
            self.close_flyout()
            return
        self.close_flyout()
        sections = self.sidebar.get("sections", [])
        if not (0 <= i < len(sections)):
            return
        self.open_index = i
        self.flyout = Flyout(self, sections[i])
        self.place_flyout()
        self.flyout.show()
        self.flyout.raise_()
        self.flyout.activateWindow()
        self.flyout.input.setFocus()

    def close_flyout(self):
        if self.flyout:
            self.flyout.close()
        self.flyout = None
        self.open_index = None

    def place_flyout(self):
        if not self.flyout:
            return
        area = (self.strip.screen() or QApplication.primaryScreen()).availableGeometry()
        s = self.strip.frameGeometry()
        w, h = self.flyout.width(), self.flyout.height()
        x = s.left() - w - 8 if self.on_right else s.right() + 8
        y = min(max(s.bottom() - h, area.top() + 8), area.bottom() - h - 8)
        self.flyout.move(min(max(x, area.left() + 8), area.right() - w - 8), y)

    def _api_result(self, payload, code, kind):
        if kind == "list" and self.flyout:
            self.flyout.took_list(payload, code)

    def set_count(self, section_id: str, n: int):
        self.counts[section_id] = n
        self.strip.update()

    def refresh_counts(self):
        if not self.cfg.get("api"):
            return
        for s in self.sidebar.get("sections", []):
            path, sid = s.get("list"), s.get("id", "")
            if not path:
                continue
            self.api.get(path, lambda payload, code, sid=sid: self.bridge.count.emit(
                sid, len(api.rows(payload)) if code == 200 else 0))

    # -- recording ---------------------------------------------------------
    def toggle_record(self, mode: str):
        self.recorder.toggle(mode, self.cfg)

    def toggle_audio(self):
        self.toggle_record("audio")

    def recording_changed(self):
        self.strip.update()
        self.tray.setToolTip("Maestro Bar — recording" if self.recorder.is_recording
                             else "Maestro Bar")

    def paint_tick(self):
        if self.recorder.is_recording:
            self.tray.setToolTip(f"Maestro Bar — recording {self.recorder.elapsed}")
            for b, key in self.strip.buttons:
                if key == "record:" + self.recorder.mode:
                    b.setIcon(icon("monitor" if self.recorder.mode == "screen" else "record",
                                   "#ff453a"))
        else:
            for b, key in self.strip.buttons:
                if key.startswith("record:"):
                    b.setIcon(icon("monitor" if key.endswith("screen") else "record", FG))

    def reload(self):
        was_visible = self.strip.isVisible()
        self.close_flyout()
        self.cfg, self.cfg_source = config.load()
        self.sidebar = self.cfg["sidebar"]
        self.api.update(self.cfg.get("api", ""), self.cfg.get("token_service", "maestro-token"))
        self.strip.close()
        self.strip = Strip(self)
        self.place_strip()
        if was_visible:
            self.strip.show()
        self.refresh_counts()
        self.notify("Maestro", "Config reloaded")


def main() -> int:
    qt = QApplication(sys.argv)
    qt.setQuitOnLastWindowClosed(False)     # a tray app outlives its windows
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Maestro Bar", "This needs a system tray.")
        return 1
    bar = MaestroBar(qt)          # a name it can be kept alive by
    assert bar is not None
    return qt.exec()


if __name__ == "__main__":
    sys.exit(main())
