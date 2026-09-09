"""Fetch a static ffmpeg.exe into vendor\\, for shipping beside the app.

Audio AND screen capture both go through ffmpeg here (gdigrab for the desktop,
dshow for the microphone), so without one the app does nothing at all.

Asking every person to install it first is exactly how the Mac side ended up
with people recording into an empty file and no explanation. The build vendors
one instead.

It must be a STATIC build. A copy that depends on DLLs sitting next to someone
else's installation would fail on precisely the machines that have no ffmpeg —
the ones this exists for. The checks at the bottom are the point of the script,
not a formality: they stop a broken build being packaged and handed out.
"""

from __future__ import annotations

import subprocess
import time
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = HERE / "vendor" / "ffmpeg.exe"

# Static Windows builds carrying gdigrab and dshow — both required, gdigrab is
# the screen and dshow is the microphone.
#
# More than one, in this order, because a single source is a single point of
# failure and this one failed on a colleague's first install:
#
#   HTTP Error 503: Service Unavailable
#
# gyan.dev is one person's server and rate-limits under load; BtbN's builds are
# GitHub releases behind a CDN. GitHub goes first for that reason alone, with
# gyan.dev kept as the fallback rather than dropped — two independent hosts fail
# together far less often than one fails alone.
URLS = [
    # releases/download/LATEST — the rolling tag literally named "latest",
    # whose asset names are stable. NOT releases/latest/download, which means
    # "the newest release": BtbN publishes autobuilds hourly under names like
    # ffmpeg-N-126482-g903325e279-win64-gpl.zip, so the moment one lands the
    # stable filename is not in the newest release and every download 404s.
    # It worked when tested and broke ninety minutes later, on someone's install.
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
]
ATTEMPTS_PER_URL = 3


def fetch(force: bool = False) -> Path:
    if DEST.is_file() and not force:
        print(f"==> {DEST} already present (pass --force to refetch)")
        return DEST

    # Streamed to a temp FILE, not into memory. The archive is ~110 MB and the
    # first version read it all with .read() and then copied it again into a
    # BytesIO — a quarter of a gigabyte of RAM to extract one executable.
    #
    # It also printed nothing for the whole download, so on a slow link the
    # build looked hung. It is genuinely slow: this took over five minutes on a
    # normal connection, which is a thing to SAY rather than let someone guess.
    tmp = DEST.parent / "ffmpeg-download.zip.part"
    tmp.parent.mkdir(parents=True, exist_ok=True)

    # Try each host, a few times each, with a growing pause. A 503 is usually
    # "not right now" rather than "never", and the whole setup used to die on
    # the first one.
    got = 0
    last = ""
    for url in URLS:
        for attempt in range(1, ATTEMPTS_PER_URL + 1):
            print(f"==> downloading {url}")
            if attempt > 1:
                print(f"    attempt {attempt} of {ATTEMPTS_PER_URL}")
            print("    ~110-170 MB, and this is not fast — several minutes is normal.")
            got = 0
            try:
                # timeout here is per-read, not total: a slow but steady
                # download is fine, a stalled one is not.
                with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as out:
                    total = int(r.headers.get("Content-Length") or 0)
                    while chunk := r.read(1 << 20):
                        out.write(chunk)
                        got += len(chunk)
                        if total:
                            print(f"\r    {got / 1e6:6.0f} / {total / 1e6:.0f} MB", end="", flush=True)
                print()
                break
            except Exception as e:
                last = f"{url}: {e}"
                print(f"\n    failed after {got / 1e6:.0f} MB: {e}")
                tmp.unlink(missing_ok=True)
                if attempt < ATTEMPTS_PER_URL:
                    time.sleep(5 * attempt)
        if tmp.is_file() and tmp.stat().st_size > 0:
            break
    else:
        raise SystemExit(f"could not download ffmpeg from any source. Last error — {last}\n"
                         f"Install it by hand instead:  winget install Gyan.FFmpeg")
    if not (tmp.is_file() and tmp.stat().st_size > 0):
        raise SystemExit(f"could not download ffmpeg from any source. Last error — {last}\n"
                         f"Install it by hand instead:  winget install Gyan.FFmpeg")

    try:
        with zipfile.ZipFile(tmp) as z:
            names = [n for n in z.namelist() if n.lower().endswith("bin/ffmpeg.exe")]
            if not names:
                raise SystemExit("no bin/ffmpeg.exe inside the archive")
            DEST.write_bytes(z.read(names[0]))
    except zipfile.BadZipFile:
        # A truncated download reads as a corrupt zip. Say which it was, or the
        # next person debugs the archive instead of their connection.
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"the download was incomplete ({got / 1e6:.0f} MB) — run it again")
    finally:
        tmp.unlink(missing_ok=True)

    size_mb = DEST.stat().st_size / 1e6
    print(f"==> wrote {DEST} ({size_mb:.0f} MB)")

    # --- the checks that matter -------------------------------------------
    if sys.platform.startswith("win"):
        try:
            out = subprocess.run([str(DEST), "-hide_banner", "-devices"],
                                 capture_output=True, text=True, timeout=60).stdout
        except Exception as e:
            DEST.unlink(missing_ok=True)
            raise SystemExit(f"REFUSING: the downloaded ffmpeg does not run ({e})")
        for dev in ("gdigrab", "dshow"):
            if dev not in out:
                DEST.unlink(missing_ok=True)
                raise SystemExit(f"REFUSING: no {dev} — it could not "
                                 f"{'capture the screen' if dev == 'gdigrab' else 'record audio'}")
        print("==> verified: runs, gdigrab and dshow both present")
    else:
        # Fetched on a Mac for packaging elsewhere: the binary cannot be run
        # here, so say so rather than implying it was checked.
        print("==> NOT verified (not running on Windows) — run this on the "
              "build machine, or verify before shipping")
    return DEST


if __name__ == "__main__":
    fetch(force="--force" in sys.argv)
