"""Maestro Bar for Windows.

A pill at the top of the screen with a panel beneath it: recording, the
review queue, capture and a box to ask the company brain. The page itself
(ui/) is shared with the Mac app; this file owns the window, the token, the
recorder and every HTTP call. Everything it shows comes from bar.json, the
same file the Mac app reads.

Run:    python maestro_bar.py
Build:  build.bat   (produces dist\\MaestroBar.exe)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import (QFile, QIODevice, QObject, QPoint, Qt, QTimer, QUrl,
                            Signal, Slot)
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QInputDialog, QLineEdit, QMenu,
                               QMessageBox, QSystemTrayIcon, QVBoxLayout, QWidget)

import api
import config
from icons import icon
from recorder import Recorder



class Bridge(QObject):
    """pynput runs on its own thread; Qt work has to be handed back."""
    hotkey = Signal()
    api_result = Signal(object, int, str)
    count = Signal(str, int)
    to_web = Signal(object)
    recorder_changed = Signal()
    recorder_message = Signal(str, str)


# --------------------------------------------------------------------------
# the bar: one web page, shared with the Mac app
# --------------------------------------------------------------------------

class WebBridge(QObject):
    """The page's end of the conversation. `send` is what app.js calls;
    `toWeb` is what the app emits. Both carry one JSON object as a string."""
    toWeb = Signal(str)

    def __init__(self, app: "MaestroBar"):
        super().__init__()
        self.app = app

    @Slot(str)
    def send(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if isinstance(msg, dict):
            self.app.from_web(msg)


def ui_dir() -> Path:
    """ui/ beside the script, or inside the exe. It is a copy of
    MaestroBar/ui in the Mac repo, made by scripts/sync-ui.sh there."""
    return config.bundled("ui")


class BarWindow(QWidget):
    """A transparent window the size of the page. The page draws the pill and
    the panel and reports its own height; the window follows."""

    def __init__(self, app: "MaestroBar"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.app = app
        self._drag_last: QPoint | None = None
        self._drag_timer = QTimer(self)
        self._drag_timer.setInterval(12)
        self._drag_timer.timeout.connect(self._drag_step)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        self.view.page().setBackgroundColor(Qt.transparent)
        lay.addWidget(self.view)

        self.bridge = WebBridge(app)
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("maestro", self.bridge)
        self.view.page().setWebChannel(self.channel)

        # Qt's own client script, injected before the page runs so app.js
        # finds QWebChannel on window without shipping a copy of it.
        f = QFile(":/qtwebchannel/qwebchannel.js")
        if f.open(QIODevice.ReadOnly):
            script = QWebEngineScript()
            script.setSourceCode(bytes(f.readAll()).decode("utf-8"))
            script.setName("qwebchannel")
            script.setInjectionPoint(QWebEngineScript.DocumentCreation)
            script.setWorldId(QWebEngineScript.MainWorld)
            script.setRunsOnSubFrames(False)
            self.view.page().scripts().insert(script)
            f.close()

        index = ui_dir() / "index.html"
        if index.is_file():
            self.view.load(QUrl.fromLocalFile(str(index)))
        else:
            print(f"[bar] {index} is missing; nothing to show")
        self.setFixedSize(600, 86)

    def resize_to(self, w: int, h: int, centre: int | None = None) -> None:
        """The page measures itself and the window follows. The parked edge
        stays put, so opening the panel grows the window inwards rather than
        pushing the strip off the screen.

        `centre` is how far down the strip's own middle sits. Until someone
        drags the bar, that middle is held level with the middle of the screen
        — the strip's middle, not the window's, which is mostly panel once the
        panel is open."""
        if w <= 0 or h <= 0:
            return
        area = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        right, top = self.x() + self.width(), self.y()
        self.setFixedSize(w, h)
        x = right - w if self.app.on_right else self.x()
        if centre is not None and not self.app.placed:
            top = area.center().y() - centre
        x = min(max(x, area.left()), area.right() - w + 1)
        top = min(max(top, area.top()), max(area.top(), area.bottom() - h + 1))
        self.move(x, top)

    # -- dragging: the page cannot move the window, so it asks ---------------
    def start_drag(self) -> None:
        self._drag_last = QCursor.pos()
        self._drag_timer.start()

    def _drag_step(self) -> None:
        if not (QApplication.mouseButtons() & Qt.LeftButton):
            self._drag_timer.stop()
            self.snap()
            return
        p = QCursor.pos()
        if self._drag_last is not None:
            d = p - self._drag_last
            if d.x() or d.y():
                self.move(self.pos() + d)
        self._drag_last = p

    def snap(self) -> None:
        """Dropped anywhere, the bar returns to the nearer edge — the
        placement it has always had, and the one that leaves the middle of the
        screen free."""
        area = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        f = self.frameGeometry()
        self.app.on_right = (area.right() - f.right()) <= (f.left() - area.left())
        x = area.right() - f.width() + 1 if self.app.on_right else area.left()
        y = min(max(f.top(), area.top()), max(area.top(), area.bottom() - f.height() + 1))
        self.move(x, y)
        self.app.placed = True
        # The anchor is the corner against the parked edge, not the left one:
        # the window is narrow while folded and wide while open, and only the
        # parked corner is the same point in both.
        config.write_state({"anchor": x + f.width() if self.app.on_right else x,
                            "top": y, "on_right": self.app.on_right})
        self.app.send({"type": "edge", "edge": "right" if self.app.on_right else "left"})

    # -- invisibility, the Windows way ----------------------------------------
    def apply_invisibility(self, on: bool) -> None:
        """Left out of screen shares and of the app's own screen recording,
        the way Zoom's overlays are. WDA_EXCLUDEFROMCAPTURE needs Windows 10
        2004 or newer; older builds simply ignore the call."""
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x11 if on else 0x0)
        except Exception:  # noqa: BLE001 — a cosmetic feature must never take the bar down
            pass

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.app.send({"type": "escape"})
        else:
            super().keyPressEvent(e)


# --------------------------------------------------------------------------
# the application
# --------------------------------------------------------------------------

class MaestroBar:
    def __init__(self, qt: QApplication):
        self.qt = qt
        self.bridge = Bridge()
        self.counts: dict[str, int] = {}
        self.on_right = True          # which edge the strip is parked against
        self.placed = False           # has anyone dragged it themselves yet
        self.rows: dict[str, list[dict]] = {}       # section id → raw rows
        self.page_ready = False
        self.queued: list[dict] = []

        config.install_default_if_missing()
        self.cfg, self.cfg_source = config.load()
        self.sidebar = self.cfg["sidebar"]
        self.api = api.Api(self.cfg.get("api", ""), self.cfg.get("token_service", "maestro-token"))

        self.recorder = Recorder(
            on_change=self.bridge.recorder_changed.emit,
            on_message=self.bridge.recorder_message.emit)

        self.bridge.hotkey.connect(self.toggle_bar)
        self.bridge.recorder_changed.connect(self.recording_changed)
        self.bridge.recorder_message.connect(self.notify)
        self.bridge.api_result.connect(self._api_result)
        self.bridge.count.connect(self.set_count)
        self.bridge.to_web.connect(self._to_web)

        self.tray = QSystemTrayIcon(icon("tray", "#d0d0d0", 32))
        self.tray.setToolTip("Maestro Bar")
        self.tray.setContextMenu(self.build_tray_menu())
        self.tray.activated.connect(
            lambda reason: self.toggle_bar()
            if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

        self.bar = BarWindow(self)
        self.place_bar()
        self.bar.show()
        self.bar.apply_invisibility(bool(self.sidebar.get("invisible", True)))

        self.tick = QTimer()
        self.tick.timeout.connect(self.paint_tick)
        self.tick.start(1000)

        self.counts_timer = QTimer()
        self.counts_timer.timeout.connect(self.refresh_counts)
        self.counts_timer.start(max(20, int(self.sidebar.get("refresh_seconds", 90))) * 1000)
        self.refresh_counts()
        self.install_hotkey()

    # -- tray --------------------------------------------------------------
    def open_logs(self) -> None:
        """The folder with one ffmpeg log per recording. When a recording ends
        the moment it starts, the newest file here says why."""
        d = config.expand(self.cfg.get("out_dir", "~/Recordings")) / "logs"
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # noqa: S606 — a folder we just created

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
        m.addAction(QAction("Show or hide the strip", m, triggered=self.toggle_bar))
        m.addSeparator()
        m.addAction(QAction("Set API token…", m, triggered=self.ask_token))
        m.addAction(QAction("Choose microphone…", m, triggered=self.choose_microphone))
        m.addAction(QAction("Check setup", m, triggered=self.check_setup))
        m.addSeparator()
        m.addAction(QAction("Open recordings", m, triggered=self.open_recordings))
        m.addAction(QAction("Open logs", m, triggered=self.open_logs))
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

    # -- the window and the page ---------------------------------------

    # -- the window --------------------------------------------------------
    @staticmethod
    def screen_at_cursor():
        """The screen the pointer is on, not the one with keyboard focus.

        With two displays, focus put the bar on one screen and the clamp then
        pulled it to the edge of the other. Where the pointer is is both
        stable and what someone means by "here"."""
        return QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()

    def place_bar(self):
        st = config.read_state()
        area = self.screen_at_cursor().availableGeometry()
        self.on_right = bool(st.get("on_right", True))
        self.placed = "anchor" in st and "top" in st
        if self.placed:
            anchor, top = int(st["anchor"]), int(st["top"])
            x = anchor - self.bar.width() if self.on_right else anchor
            if area.adjusted(-40, -40, 40, 40).contains(QPoint(x, top)):
                self.bar.move(x, top)
                return
            self.placed = False
        # The right edge, level with the middle of the screen: where the strip
        # parked before this, and clear of what is being read.
        self.bar.move(area.right() - self.bar.width() + 1,
                      area.center().y() - self.bar.height() // 2)

    def toggle_bar(self):
        if self.bar.isVisible():
            self.bar.hide()
        else:
            self.bar.show()
            self.bar.raise_()
            self.send_state()
            self.send({"type": "expand"})

    # -- talking to the page -----------------------------------------------
    def send(self, msg: dict) -> None:
        """Any thread may call this; the page is reached on the Qt thread."""
        self.bridge.to_web.emit(msg)

    def _to_web(self, msg: dict) -> None:
        if not self.page_ready:
            self.queued.append(msg)
            return
        self.bar.bridge.toWeb.emit(json.dumps(msg, ensure_ascii=False))

    @staticmethod
    def pretty_hotkey(combo: str) -> str:
        parts = [p.strip("<>") for p in str(combo).split("+") if p.strip()]
        return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in parts)

    def send_state(self) -> None:
        sections = []
        for s in self.sidebar.get("sections", []):
            d = {"id": s.get("id", ""), "title": s.get("title", ""), "symbol": s.get("symbol", ""),
                 "hasList": bool(s.get("list")),
                 "actions": [{"label": a.get("label", "OK"), "symbol": a.get("symbol", "")}
                             for a in (s.get("actions") or [])]}
            c = s.get("compose")
            if c:
                d["compose"] = {"placeholder": c.get("placeholder", "Start typing"),
                                "record": bool(c.get("record", True))}
            sections.append(d)
        msg = {"type": "state", "platform": "win",
               "hotkey": self.pretty_hotkey(self.sidebar.get("hotkey", "<ctrl>+<alt>+m")),
               "api": bool(self.cfg.get("api")),
               "edge": "right" if self.on_right else "left",
               "sections": sections,
               "records": [{"mode": r.get("mode", "audio"), "label": r.get("label", "Record")}
                           for r in self.sidebar.get("record", [])],
               "counts": self.counts,
               "recording": self.recording_dict()}
        ask_path = self.sidebar.get("ask_path", "/brain/ask")
        if ask_path and self.cfg.get("api"):
            msg["ask"] = {"placeholder": self.sidebar.get(
                "ask_placeholder", "Ask about clients, meetings, decisions")}
        self.send(msg)

    def recording_dict(self) -> dict:
        return {"active": self.recorder.is_recording, "mode": self.recorder.mode,
                "elapsed": self.recorder.elapsed}

    def say(self, text: str) -> None:
        """An outcome is shown where the click was when the bar is up, and as
        a notification when it is not."""
        if self.bar.isVisible() and self.page_ready:
            self.send({"type": "toast", "text": text})
        else:
            self.notify("Maestro", text)

    def from_web(self, m: dict) -> None:
        t = m.get("type")
        if t == "ready":
            self.page_ready = True
            self.send_state()
            pending, self.queued = self.queued, []
            for q in pending:
                self._to_web(q)
        elif t == "size":
            centre = m.get("centre")
            self.bar.resize_to(int(m.get("width", 600)), int(m.get("height", 86)),
                               int(centre) if centre is not None else None)
        elif t == "drag":
            self.bar.start_drag()
        elif t == "hide":
            self.bar.hide()
        elif t == "focus":
            self.bar.activateWindow()
            self.bar.view.setFocus()
        elif t == "open":
            self.load_rows(str(m.get("section", "")))
        elif t == "action":
            self.perform(str(m.get("section", "")), int(m.get("index", 0)), m.get("id"))
        elif t == "compose":
            self.compose(str(m.get("section", "")), str(m.get("text", "")), m.get("id"))
        elif t == "ask":
            self.ask(str(m.get("text", "")))
        elif t == "record":
            self.toggle_record(str(m.get("mode", "audio")))
        elif t == "open_url":
            url = str(m.get("url") or "")
            if not url:
                for it in self.cfg.get("items", []) or []:
                    if it.get("type") == "open" and it.get("url"):
                        url = it["url"]
                        break
            if url:
                import webbrowser
                webbrowser.open(url)
        elif t == "copy":
            QApplication.clipboard().setText(str(m.get("text", "")))

    # -- data --------------------------------------------------------------
    def section(self, sid: str) -> dict | None:
        for s in self.sidebar.get("sections", []):
            if s.get("id") == sid:
                return s
        return None

    def card(self, row: dict, s: dict) -> dict:
        fields = s.get("fields") or {}
        return {"id": api.row_id(row),
                "title": api.field(row, fields.get("title", ["title"])) or "Untitled",
                "subtitle": api.field(row, fields.get("subtitle", ["client_name"])),
                "body": api.field(row, fields.get("body", ["detail"]))}

    def load_rows(self, sid: str) -> None:
        s = self.section(sid)
        if not s or not s.get("list"):
            return
        if not self.cfg.get("api"):
            self.send({"type": "rows", "section": sid, "rows": []})
            return
        self.api.get(s["list"], lambda payload, code:
                     self.bridge.api_result.emit(payload, code, "rows:" + sid))

    def _api_result(self, payload, code, kind):
        if kind.startswith("rows:"):
            sid = kind[5:]
            s = self.section(sid)
            if not s:
                return
            raw = api.rows(payload) if code == 200 else []
            self.rows[sid] = raw
            self.counts[sid] = len(raw)
            self.send({"type": "rows", "section": sid, "rows": [self.card(r, s) for r in raw]})

    def find_row(self, sid: str, rid) -> dict | None:
        if rid is None:
            return None
        for r in self.rows.get(sid, []):
            if api.row_id(r) == str(rid):
                return r
        return None

    def perform(self, sid: str, index: int, rid) -> None:
        s = self.section(sid)
        actions = (s or {}).get("actions") or []
        row = self.find_row(sid, rid)
        if not s or not (0 <= index < len(actions)) or not row:
            return
        action = actions[index]
        if not action.get("path"):
            return
        path = api.substitute(action["path"], row)
        label = action.get("label", "Action")
        if action.get("advance", True):
            # The page has already dropped the card. Keep the cache in step.
            self.rows[sid] = [r for r in self.rows.get(sid, []) if api.row_id(r) != str(rid)]
            self.counts[sid] = len(self.rows[sid])

        def done(_payload, code):
            if 200 <= code < 300:
                if action.get("toast"):
                    self.say(action["toast"])
            else:
                self.say(f"{label} failed ({code or 'no reply'})")
                self.load_rows(sid)      # the truth comes back from the server

        self.api.call(action.get("method", "POST"), path, action.get("body"), done)

    def compose(self, sid: str, text: str, rid) -> None:
        s = self.section(sid)
        c = (s or {}).get("compose") or {}
        text = text.strip()
        if not s or not c or not text:
            return
        # Not every destination exists as an endpoint yet. Appending to a file
        # keeps the box useful now, and only the config changes later.
        if not c.get("path"):
            target = c.get("file")
            if not target:
                return
            p = config.expand(target)
            p.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(f"\n## {stamp}\n{text}\n")
            self.say(c.get("toast", "Captured"))
            return
        body = {c.get("field", "text"): text, "section": sid}
        path = c["path"]
        row = self.find_row(sid, rid)
        if row:
            path = api.substitute(path, row)
            if api.row_id(row):
                body["item_id"] = api.row_id(row)
        if "{" in path:
            self.say("That box needs a card open")
            return
        self.api.post(path, body, lambda _p, code: self.say(
            c.get("toast", "Sent") if 200 <= code < 300
            else f"Could not send ({code or 'no reply'})"))

    def ask(self, text: str) -> None:
        """The Ask box goes to the company brain, which answers with citations."""
        path = self.sidebar.get("ask_path", "/brain/ask")
        text = text.strip()
        if not text:
            return
        if not path or not self.cfg.get("api"):
            self.send({"type": "answer_error", "text": "No API is configured."})
            return

        def done(payload, code):
            if 200 <= code < 300 and isinstance(payload, dict):
                self.send({"type": "answer", "text": payload.get("answer") or "",
                           "citations": payload.get("citations") or []})
            else:
                self.send({"type": "answer_error",
                           "text": "Maestro did not answer. Check the connection."
                           if not code else f"The brain answered {code}."})

        self.api.post(path, {"query": text}, done)

    def set_count(self, section_id: str, n: int):
        self.counts[section_id] = n
        self.send({"type": "counts", "counts": self.counts})

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
    def ask_page_url(self) -> str:
        """Where the bug is, asked once before capture starts.

        A screen recording shows the page but not dependably its address — the
        URL bar is small, often cropped, and the model reads what was SAID. So
        the one fact a recording cannot carry is asked for directly.

        Prefilled from the clipboard when it holds a URL, which it usually does:
        someone reporting a page bug copied the address on the way here. That
        turns the prompt into a single Enter.

        Skipping is a real answer, not a failure. Escape, Cancel, or an empty
        box all record with no URL. Nothing here can stop a recording.
        """
        clip = ""
        try:
            clip = (QApplication.clipboard().text() or "").strip()
        except Exception:  # noqa: BLE001 — no clipboard is not a reason to refuse
            clip = ""
        if not clip.lower().startswith(("http://", "https://")) or len(clip) > 2000:
            clip = ""
        try:
            text, ok = QInputDialog.getText(
                None, "Where is this?",
                "Paste the page URL, or press Escape to skip:", text=clip)
        except Exception:  # noqa: BLE001
            return ""
        return (text or "").strip() if ok else ""

    def toggle_record(self, mode: str):
        # Only when starting. Asking on the way out would put the dialog in
        # front of someone trying to stop, and the recording keeps rolling
        # while they read it.
        if self.recorder.is_recording:
            self.recorder.stop()
            return
        self.recorder.start(mode, self.cfg, page_url=self.ask_page_url())

    def toggle_audio(self):
        self.toggle_record("audio")

    def recording_changed(self):
        self.send({"type": "recording", **self.recording_dict()})
        self.tray.setToolTip("Maestro Bar — recording" if self.recorder.is_recording
                             else "Maestro Bar")

    def paint_tick(self):
        if self.recorder.is_recording:
            self.tray.setToolTip(f"Maestro Bar — recording {self.recorder.elapsed}")
            self.send({"type": "recording", **self.recording_dict()})

    def reload(self):
        self.cfg, self.cfg_source = config.load()
        self.sidebar = self.cfg["sidebar"]
        self.api.update(self.cfg.get("api", ""), self.cfg.get("token_service", "maestro-token"))
        self.rows = {}
        self.counts = {}
        self.bar.apply_invisibility(bool(self.sidebar.get("invisible", True)))
        self.send_state()
        self.refresh_counts()
        self.say("Config reloaded")


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
