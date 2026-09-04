"""Transcribe a recording and push it into Maestro.

Called by the app when a recording finishes, and runnable by hand:

    python push.py "C:\\Users\\me\\Recordings\\2026-08-31_1420.m4a"

Nothing is ever lost. If the endpoint is missing or the token is wrong the
transcript is copied to Recordings\\unsent and flush() sends it later.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

import api
import config


def transcribe(media: Path, lang: str = "mk") -> str:
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(media), language=lang, vad_filter=True)
    return "\n".join(
        f"[{int(s.start // 60):02d}:{int(s.start % 60):02d}] {s.text.strip()}"
        for s in segments)


def push(txt: Path, cfg: dict, client: str = "") -> tuple[bool, str]:
    text = txt.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return False, "the transcript is empty"

    base = (cfg.get("api") or "").rstrip("/")
    token = api.read_token(cfg.get("token_service", "maestro-token"))
    if not base:
        return False, "no API is configured"
    if not token:
        return False, "no token"

    stem = txt.stem
    body = {
        # Stable id: the same recording pushed twice updates one meeting
        # instead of filing a duplicate set of tasks.
        "external_id": "local:" + hashlib.sha1(
            f"{stem}:{txt.stat().st_size}".encode()).hexdigest()[:16],
        "title": (client + " — " if client else "") + stem,
        "transcript": text,
        "attendees": [],
        "started_at": datetime.fromtimestamp(
            txt.stat().st_mtime, timezone.utc).isoformat(),
        "source": "local-recording",
    }
    try:
        r = requests.post(base + "/meetings/ingest",
                          headers={"Authorization": "Bearer " + token,
                                   "Content-Type": "application/json"},
                          data=json.dumps(body), timeout=60)
    except requests.RequestException as e:
        return False, str(e)[:80]
    if 200 <= r.status_code < 300:
        try:
            return True, f"{r.json().get('tasks', 0)} tasks"
        except ValueError:
            return True, ""
    return False, f"HTTP {r.status_code}"


def hold(txt: Path, out_dir: Path) -> None:
    unsent = out_dir / "unsent"
    unsent.mkdir(parents=True, exist_ok=True)
    (unsent / txt.name).write_text(txt.read_text(encoding="utf-8", errors="replace"),
                                   encoding="utf-8")


def flush() -> None:
    """Retry everything that never made it."""
    cfg, _ = config.load()
    out_dir = config.expand(cfg.get("out_dir", "~/Recordings"))
    for t in sorted((out_dir / "unsent").glob("*.txt")):
        ok, note = push(t, cfg)
        print(f"{t.name}: {'sent' if ok else 'still stuck'} {note}")
        if ok:
            t.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "--flush":
        flush()
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2

    media = Path(argv[1])
    client = argv[2] if len(argv) > 2 else ""
    cfg, _ = config.load()
    out_dir = config.expand(cfg.get("out_dir", "~/Recordings"))
    txt = media.with_suffix(".txt")

    if not txt.exists() or not txt.stat().st_size:
        try:
            txt.write_text(transcribe(media), encoding="utf-8")
        except ImportError:
            print("faster-whisper is not installed; keeping the audio only")
            return 1
        except Exception as e:                    # noqa: BLE001
            print(f"transcription failed: {e}")
            return 1

    ok, note = push(txt, cfg, client)
    if ok:
        print(f"in Maestro: {media.name} {note}")
        return 0
    hold(txt, out_dir)
    print(f"not sent ({note}); kept in {out_dir / 'unsent'}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
