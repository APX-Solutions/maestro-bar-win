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
    def __init__(self, on_change=None, on_message=None, on_sent=None):
        self.proc: subprocess.Popen | None = None
        self.started_at: dt.datetime | None = None
        self.mode = "audio"
        self.file: Path | None = None
        self.client: str | None = None
        self.cfg: dict = {}
        # Where ffmpeg's stderr goes. Without these the first failure raises an
        # AttributeError inside the failure handler, which is the worst possible
        # moment to discover an attribute is missing.
        self.log_file: Path | None = None
        self._log_fh = None
        self._retrying = False
        self.on_change = on_change or (lambda: None)
        self.on_message = on_message or (lambda title, body: None)
        self.on_sent = on_sent or (lambda: None)

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

    def start(self, mode: str, cfg: dict, client: str | None = None,
              page_url: str = "") -> None:
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
        # "-" means: record the screen with no audio at all. Set by the retry
        # after a microphone took the whole command down with it.
        if device == "-":
            device = ""
        elif not device:
            # Not devices[0]. That list is ordered by Windows, not by what
            # works: a headset that is listed but not connected can sit at the
            # top, and opening it kills the recording the instant it starts.
            # pick_audio_device probes until one actually opens.
            device = config.pick_audio_device()
            if device:
                pass
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
        # Written now, while the answer is in hand, rather than at the end:
        # a crash mid-recording still leaves the page beside the media, and
        # send.py reads it from there on the first try or on a retry days
        # later. Cleared first so a skipped prompt cannot inherit the URL
        # from the previous recording.
        self.page_url = (page_url or "").strip()
        try:
            side = self.file.with_suffix(self.file.suffix + ".url")
            side.unlink(missing_ok=True)
            if self.page_url:
                side.write_text(self.page_url, encoding="utf-8")
        except OSError:
            pass  # a note we could not write is not worth losing a recording over

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

        # ffmpeg's stderr was going to DEVNULL, so when a recording died on
        # startup there was nothing to look at — the app just stopped. It is
        # the only thing that ever says WHY, so it goes to a file next to the
        # recordings, named after it.
        log_dir = out_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = log_dir / (name + ".log")
        try:
            self._log_fh = self.log_file.open("wb")
            self._log_fh.write(("cmd: " + " ".join(cmd) + "\n\n").encode())
            self._log_fh.flush()
        except OSError:
            self._log_fh = None

        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=(self._log_fh or subprocess.DEVNULL),
                creationflags=config.NO_WINDOW)
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

        try:
            if self._log_fh:
                self._log_fh.close()
        except OSError:
            pass

        # A recording that ends the instant it starts did not record anything;
        # it failed. Saying "saved" and handing the pipeline an empty file is
        # how this looked like a mystery instead of an error.
        ran = (dt.datetime.now() - self.started_at).total_seconds() if self.started_at else 0
        if p.returncode not in (0, 255) or ran < 1.5:
            self._failed_to_start(ran)
            return
        self._finish()

    def _failed_to_start(self, ran: float) -> None:
        """Report the reason ffmpeg gave, and retry the screen without audio.

        A microphone that cannot be opened — blocked by Windows, or held by
        another app — takes the WHOLE command down, so the screen capture dies
        with it. The screen needs no permission and would have worked alone,
        so losing it to a microphone problem is the wrong trade."""
        reason = ""
        try:
            if self.log_file and self.log_file.exists():
                lines = [ln for ln in self.log_file.read_text(
                    encoding="utf-8", errors="replace").splitlines() if ln.strip()]
                reason = lines[-1][:160] if lines else ""
        except OSError:
            pass

        mode, client, cfg = self.mode, self.client, self.cfg
        self.proc = self.file = self.started_at = None
        self.on_change()

        if mode == "screen" and not getattr(self, "_retrying", False):
            self._retrying = True
            self.on_message("Recording the screen without audio",
                            reason or "The microphone could not be opened.")
            try:
                cfg2 = dict(cfg or {})
                cfg2["audio_device"] = "-"        # sentinel: skip audio entirely
                self.start(mode, client, cfg2)
            finally:
                self._retrying = False
            return

        self.on_message(
            "Recording stopped immediately",
            (reason or "ffmpeg exited straight away.")
            + f"\n\nFull log: {self.log_file}")

    def _finish(self) -> None:
        recorded, tag, cfg = self.file, self.client, self.cfg
        self.page_url = ""  # send.py reads it from the sidecar, not from here
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
            self.on_sent()
            return

        if after:
            cmd = (after.replace("{scripts}", str(config.bundled(".")))
                        .replace("{file}", str(recorded))
                        .replace("{client}", tag or ""))
            self.on_message("Saved", f"{recorded.name}, processing now")
            subprocess.Popen(cmd, shell=True, creationflags=config.NO_WINDOW)
            self.on_sent()
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
