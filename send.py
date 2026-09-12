"""Send a recording to Maestro as-is. The Windows twin of send.sh.

The media IS the report: Maestro reads it — video included when the screen was
captured — decides whether it is a bug, a feature request or thinking aloud,
and raises the card. Nothing is transcribed here and nothing is typed.

This replaces push.py for recordings. push.py transcribes locally with whisper
and posts the TEXT to /meetings/ingest, which throws the screen recording away
— and a screen recording is the whole point of recording the screen. It also
needs a Python speech model installed on every machine, which is how the Mac
side ended up with people seeing "transcript came out empty" and no reason why.

Nothing is ever lost. If any step fails the file stays where it is and its path
is queued in Recordings\\unsent-media for flush() to retry.
"""

from __future__ import annotations

import mimetypes
import os
import sys
from pathlib import Path

import requests

TIMEOUT_SIGN = 30
TIMEOUT_UPLOAD = 900     # a screen recording is large and links are not always fast
TIMEOUT_INGEST = 900     # the server downloads the media and waits for a verdict


def _unsent_dir(cfg: dict) -> Path:
    d = Path(os.path.expandvars(cfg.get("out_dir") or "~/Recordings")).expanduser() / "unsent-media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _queue(cfg: dict, path: Path) -> None:
    """Remember a path, not a copy. A screen recording is hundreds of megabytes
    and duplicating it to retry later would fill the disk."""
    q = _unsent_dir(cfg) / "queue"
    try:
        seen = q.read_text(encoding="utf-8").splitlines() if q.exists() else []
        if str(path) not in seen:
            with q.open("a", encoding="utf-8") as fh:
                fh.write(str(path) + "\n")
    except OSError:
        pass


def token(cfg: dict) -> str:
    """The machine token, file first.

    %USERPROFILE%\\.maestro\\token is the same place the Mac keeps it, and it is
    checked first for the same reason: it survives a reinstall, where a
    credential bound to an application identity does not."""
    f = Path.home() / ".maestro" / "token"
    try:
        if f.is_file():
            # utf-8-SIG, not utf-8: Notepad saves UTF-8 with a byte order mark,
            # and ﻿ is not whitespace, so .strip() leaves it on the front
            # of the token. The request then fails as "invalid token" while the
            # file on disk looks exactly right.
            t = f.read_text(encoding="utf-8-sig").strip()
            if t:
                return t
    except OSError:
        pass

    env = (os.environ.get("MAESTRO_TOKEN") or "").strip()
    if env:
        return env

    # Credential Manager last, but it MUST be here: "Set API token…" in the tray
    # writes there, so without this someone who set their token the obvious way
    # would still be told there is no token.
    try:
        import api
        return (api.read_token(cfg.get("token_service", "maestro-token")) or "").strip()
    except Exception:  # noqa: BLE001 — a broken vault must not stop an upload
        return ""


def page_url_for(media: Path) -> str:
    """The page URL captured before recording, kept beside the media.

    A sidecar rather than a field on the queue: the queue is a list of paths and
    a failed send re-reads it later, possibly after a restart. Writing the URL
    next to the file it belongs to means one source of truth, and no queue
    format to migrate."""
    f = media.with_suffix(media.suffix + ".url")
    try:
        return f.read_text(encoding="utf-8-sig").strip() if f.is_file() else ""
    except OSError:
        return ""


def send(path: str | Path, cfg: dict, client: str = "") -> tuple[bool, str]:
    """Upload the media and have Maestro read it. Returns (ok, message)."""
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return False, "the recording is missing or empty"

    base = (cfg.get("api") or "").rstrip("/")
    tok = token(cfg)
    if not base:
        return False, "no API is configured"
    if not tok:
        _queue(cfg, p)
        return False, "no token yet — the recording is kept and will retry"

    head = {"Authorization": f"Bearer {tok}"}
    kind = "screen" if p.suffix.lower() in (".mp4", ".mov", ".mkv") else "audio"
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"

    # 1. ask for a place to put it
    try:
        r = requests.post(f"{base}/recordings/upload-url", headers=head,
                          json={"filename": p.name, "content_type": ctype, "kind": kind},
                          timeout=TIMEOUT_SIGN)
        r.raise_for_status()
        signed = r.json()
    except Exception as e:
        _queue(cfg, p)
        return False, f"Maestro would not sign the upload ({str(e)[:60]}) — recording kept"

    key, url = signed.get("key"), signed.get("url")
    if not key or not url:
        _queue(cfg, p)
        return False, "the signed upload was incomplete — recording kept"

    # 2. put the bytes there. Streamed, so a 300MB screen capture is not read
    #    into memory first.
    try:
        with p.open("rb") as fh:
            up = requests.put(url, data=fh,
                              headers={"Content-Type": signed.get("content_type") or ctype},
                              timeout=TIMEOUT_UPLOAD)
        up.raise_for_status()
    except Exception as e:
        _queue(cfg, p)
        return False, f"upload failed ({str(e)[:60]}) — recording kept"

    # 3. tell Maestro to read it
    try:
        r = requests.post(f"{base}/recordings/ingest", headers=head,
                          json={"key": key, "kind": kind, "client": client or "",
                                "page_url": page_url_for(p)},
                          timeout=TIMEOUT_INGEST)
        r.raise_for_status()
        out = r.json()
    except Exception as e:
        _queue(cfg, p)
        return False, f"Maestro could not read it ({str(e)[:60]}) — recording kept"

    # Read, so the note has served its purpose. Left behind it would attach the
    # wrong page to nothing in particular and clutter the folder.
    try:
        p.with_suffix(p.suffix + ".url").unlink(missing_ok=True)
    except OSError:
        pass

    routed = out.get("routed")
    cat = out.get("category") or "?"
    if routed == "triage":
        return True, f"{cat}: {(out.get('title') or '')[:90]} — card raised"
    if routed == "meeting":
        return True, f"Brainstorm — {out.get('tasks', 0)} task(s) proposed"
    if routed == "studio":
        # Work for Advertisable. The plan is in the bar's Storyboards chip.
        status = out.get("status")
        if status == "proposed":
            return True, f"Plan ready in the bar: {out.get('summary') or 'storyboards'} — press Go"
        if status == "needs_brand":
            return True, "Which brand? Open the bar and type it under the card"
        return True, f"Could not plan that: {out.get('reason') or status}"
    # Read but deliberately not carded. 'other' and a low-confidence verdict are
    # normal answers, not failures, so say which rather than implying a fault.
    return True, f"Read, no card: {out.get('reason') or cat}"


def flush(cfg: dict) -> tuple[int, int]:
    """Retry everything queued. Returns (sent, still_waiting).

    The queue is truncated before the walk so a failure re-queues cleanly
    through send() instead of being retried forever inside this loop."""
    q = _unsent_dir(cfg) / "queue"
    if not q.exists():
        return 0, 0
    try:
        pending = [ln.strip() for ln in q.read_text(encoding="utf-8").splitlines() if ln.strip()]
        q.write_text("", encoding="utf-8")
    except OSError:
        return 0, 0

    sent = failed = 0
    for line in dict.fromkeys(pending):          # de-duplicated, order kept
        f = Path(line)
        if not f.is_file():
            continue                              # gone: drop it rather than retry forever
        ok, _ = send(f, cfg)
        if ok:
            sent += 1
        else:
            failed += 1
    return sent, failed


if __name__ == "__main__":
    import config
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    ok, msg = send(sys.argv[1], config.load(), sys.argv[2] if len(sys.argv) > 2 else "")
    print(("OK: " if ok else "FAILED: ") + msg)
    raise SystemExit(0 if ok else 1)
