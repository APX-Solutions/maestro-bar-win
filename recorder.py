"""Recording, and the one place Windows genuinely differs from the Mac.

Two differences worth naming. DirectShow addresses a microphone by NAME, not
by index, and the names differ on every machine, so the device is discovered
rather than configured. And ffmpeg on Windows does not finalise a file on a
signal the way it does on a Mac: the reliable stop is writing "q" to its
stdin, which is why the process is started with a pipe it never otherwise
uses.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import threading
from pathlib import Path

import config


def _slug(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^\w]+", "-", s.lower(), flags=re.UNICODE)).strip("-")


class Recorder:
    def __init__(self, on_change=None, on_message=None):
        self.proc: subprocess.Popen | None = None
        self.started_at: dt.datetime | None = None
        self.mode = "audio"
        self.file: Path | None = None
        self.client: str | None = None
        self.cfg: dict = {}
        self.on_change = on_change or (lambda: None)
        self.on_message = on_message or (lambda title, body: None)

    @property
    def is_recording(self) -> bool:
        return self.proc is not None

    @property
    def elapsed(self) -> str:
        if not self.started_at:
            return ""
        t = int((dt.datetime.now() - self.started_at).total_seconds())
        return f"{t // 60}:{t % 60:02d}"

    def toggle(self, mode: str, cfg: dict, client: str | None = None) -> None:
        self.stop() if self.is_recording else self.start(mode, cfg, client)

    def start(self, mode: str, cfg: dict, client: str | None = None) -> None:
        if self.is_recording:
            return
        exe = config.ffmpeg_path()
        if not exe:
            self.on_message("ffmpeg is missing",
                            "Recording needs ffmpeg. Install it once with:\n\n"
                            "    winget install Gyan.FFmpeg\n\n"
                            "then restart Maestro Bar.")
            return

        device = cfg.get("audio_device") or ""
        if not device:
            found = config.audio_devices()
            if found:
                device = found[0]
            elif mode != "screen":
                # Two very different causes, and the message used to blame the
                # hardware for both. Windows never prompts for the microphone,
                # so a blocked one looks exactly like an absent one.
                if config.microphone_allowed() is False:
                    self.on_message(
                        "Windows is blocking the microphone",
                        "The device is there; access is turned off. Switch "
                        "Maestro Bar on under Microphone privacy settings and "
                        "record again. Opening that page now.")
                    config.open_microphone_settings()
                else:
                    self.on_message("No microphone found",
                                    "Windows reports no recording device. Check that a "
                                    "microphone is plugged in and enabled in Sound "
                                    "settings, then use 'Choose microphone' in the tray.")
                return

        out_dir = config.expand(cfg.get("out_dir", "~/Recordings"))
        out_dir.mkdir(parents=True, exist_ok=True)
        name = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        if client:
            name += "-" + _slug(client)
        self.file = out_dir / (name + (".mp4" if mode == "screen" else ".m4a"))

        if mode == "screen":
            cmd = [exe, "-hide_banner", "-loglevel", "error",
                   "-f", "gdigrab", "-framerate", "15", "-i", "desktop"]
            if device:
                cmd += ["-f", "dshow", "-i", f"audio={device}"]
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "96k", "-y", str(self.file)]
        else:
            cmd = [exe, "-hide_banner", "-loglevel", "error",
                   "-f", "dshow", "-i", f"audio={device}",
                   "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "64k",
                   "-y", str(self.file)]

        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=config.NO_WINDOW)
        except OSError as e:
            self.on_message("Could not start recording", str(e))
            self.file = None
            return

        self.mode, self.client, self.cfg = mode, client, cfg
        self.started_at = dt.datetime.now()
        self.on_change()
        threading.Thread(target=self._wait, daemon=True).start()

    def stop(self) -> None:
        p = self.proc
        if not p:
            return
        try:
            # "q" is ffmpeg's own quit command. Killing it instead leaves an
            # unplayable file with no moov atom, which is how recordings get
            # silently lost.
            p.stdin.write(b"q")
            p.stdin.flush()
        except (OSError, ValueError, AttributeError):
            p.terminate()

    def _wait(self) -> None:
        p = self.proc
        if not p:
            return
        try:
            p.wait(timeout=600)
        except subprocess.TimeoutExpired:
            p.kill()
        self._finish()

    def _finish(self) -> None:
        recorded, tag, cfg = self.file, self.client, self.cfg
        self.proc = None
        self.started_at = None
        self.file = None
        self.client = None
        self.on_change()

        if not recorded or not recorded.exists():
            return
        if recorded.stat().st_size < 4096:
            # An empty file has two causes and they need different answers.
            # Windows gives no prompt when the microphone is blocked — the
            # recording just contains silence — so the app has to say which.
            if config.microphone_allowed() is False:
                self.on_message(
                    "Windows is blocking the microphone",
                    "Turn Maestro Bar on under Microphone privacy settings, "
                    "then record again. Opening that page now.")
                config.open_microphone_settings()
            else:
                self.on_message("Nothing was recorded",
                                "The file is empty. Pick a microphone under "
                                "'Choose microphone' in the tray menu.")
            return

        after_cfg = cfg.get("after_record") or {}
        after = after_cfg.get("cmd", "")

        # Default: transcribe and push in this process. Shelling out to a
        # python interpreter is what breaks once the app is a frozen .exe,
        # because there is no interpreter on the machine to shell out to.
        if not after and after_cfg.get("auto_push", True):
            self.on_message("Saved", f"{recorded.name}, sending to Maestro")
            threading.Thread(target=self._send, args=(recorded, tag, cfg),
                             daemon=True).start()
            return

        if after:
            cmd = (after.replace("{scripts}", str(config.bundled(".")))
                        .replace("{file}", str(recorded))
                        .replace("{client}", tag or ""))
            self.on_message("Saved", f"{recorded.name}, processing now")
            subprocess.Popen(cmd, shell=True, creationflags=config.NO_WINDOW)
        else:
            self.on_message("Saved", recorded.name)

    def _send(self, media, tag, cfg) -> None:
        """Upload the media and let Maestro read it. Never raises into the UI.

        The media goes as-is. Transcribing here — which push.py does, and which
        this used to do — throws the screen recording away and posts text, and
        a screen recording is the entire point of recording the screen. It also
        needed a speech model installed on every machine; when it was missing
        the transcript came out empty and nothing said why."""
        try:
            import send as send_mod
            ok, note = send_mod.send(media, cfg, tag or "")
            self.on_message("In Maestro" if ok else "Kept for later", note)
        except Exception as e:  # noqa: BLE001 — a send must never take the app down
            self.on_message("Kept for later",
                            f"{media.name} is safe on disk ({str(e)[:60]}). "
                            f"Use 'Retry unsent' when it is back.")

    def _push(self, media, tag, cfg) -> None:
        """Legacy: transcribe locally and post the text. Kept for the meetings
        path only — recordings go through _send. Never raises into the UI."""
        try:
            import push
            txt = media.with_suffix(".txt")
            if not txt.exists() or not txt.stat().st_size:
                txt.write_text(push.transcribe(media), encoding="utf-8")
            ok, note = push.push(txt, cfg, tag or "")
            if ok:
                self.on_message("In Maestro", f"{media.name} {note}".strip())
            else:
                push.hold(txt, config.expand(cfg.get("out_dir", "~/Recordings")))
                self.on_message("Kept for later", f"{note}; will retry with flush")
        except ImportError:
            self.on_message("Saved as audio",
                            "faster-whisper is not installed, so nothing was transcribed")
        except Exception as e:                       # noqa: BLE001
            self.on_message("Could not process the recording", str(e)[:120])
