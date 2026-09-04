"""Configuration and small helpers.

The Windows app reads the same bar.json the Mac app reads. One file describes
the strip on both platforms, so a change to the queue or a new button is made
once. Only two keys differ by necessity, and both are named for it:
audio_device (a device NAME here, an index on the Mac) and after_record.cmd.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Maestro"
CONFIG_PATH = APP_DIR / "bar.json"
STATE_PATH = APP_DIR / "state.json"

# Hide the console window every subprocess would otherwise flash on screen.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def bundled(name: str) -> Path:
    """A file shipped beside the script, or inside the PyInstaller bundle."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


def expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


DEFAULTS = {
    "api": "",
    "token_service": "maestro-token",
    "audio_device": "",           # empty = pick the first microphone found
    "out_dir": "~/Recordings",
    "after_record": {"cmd": "", "notify": True},
    "sidebar": {
        "enabled": True,
        "hotkey": "<ctrl>+<alt>+m",
        "edge": "right",
        "width": 44,
        "flyout_width": 330,
        "refresh_seconds": 90,
        "record": [],
        "sections": [],
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def install_default_if_missing() -> None:
    """First run: give the user a config of their own to edit."""
    if CONFIG_PATH.exists():
        return
    APP_DIR.mkdir(parents=True, exist_ok=True)
    src = bundled("bar.json")
    if src.exists():
        CONFIG_PATH.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def load() -> tuple[dict, str]:
    """Returns the config and where it came from, for 'Check setup'."""
    for path in (CONFIG_PATH, bundled("bar.json")):
        if not path.exists():
            continue
        try:
            cfg = _merge(DEFAULTS, json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            return _merge(DEFAULTS, {}), f"{path} is invalid ({e}), using defaults"
        # The Mac writes its hotkey as a list; accept both spellings.
        hk = cfg["sidebar"].get("hotkey")
        if isinstance(hk, list):
            cfg["sidebar"]["hotkey"] = "+".join(
                {"cmd": "<ctrl>", "ctrl": "<ctrl>", "alt": "<alt>",
                 "shift": "<shift>"}.get(str(k).lower(), str(k).lower())
                for k in hk
            )
        return cfg, str(path)
    return _merge(DEFAULTS, {}), "no config found"


def read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(d: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        STATE_PATH.write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


def ffmpeg_path() -> str | None:
    """The app's own copy first, then whatever is on the machine.

    Audio recording needs ffmpeg and so does screen capture here (gdigrab), so
    without one the app does nothing at all. Asking every person to install it
    first is how the Mac side ended up with recordings that silently produced
    an empty file — so the build vendors a static ffmpeg.exe beside the app and
    this looks there before anything else.

    A machine-wide ffmpeg still wins over the guesses below it: someone who
    deliberately installed a newer one should get theirs."""
    here = Path(__file__).resolve().parent
    for bundled in (here / "vendor" / "ffmpeg.exe",          # running from source
                    here / "ffmpeg.exe",                     # packaged beside the exe
                    Path(getattr(sys, "_MEIPASS", here)) / "ffmpeg.exe"):  # PyInstaller
        if bundled.is_file():
            return str(bundled)

    found = shutil.which("ffmpeg")
    if found:
        return found
    for guess in (r"C:\ffmpeg\bin\ffmpeg.exe",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin" / "ffmpeg.exe"):
        if Path(guess).is_file():
            return str(guess)
    return None


def audio_devices() -> list[str]:
    """DirectShow wants a device NAME, and every machine names them
    differently, so the name has to be discovered rather than assumed."""
    exe = ffmpeg_path()
    if not exe:
        return []
    proc = subprocess.run(
        [exe, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True, text=True, creationflags=NO_WINDOW, errors="replace")
    # Two output formats, because ffmpeg changed this and the bundled build is
    # new enough to use the second one.
    #
    #   older:  [dshow @ ..] DirectShow audio devices
    #           [dshow @ ..]  "Microphone Array"
    #   newer:  [dshow @ ..] "Microphone Array" (audio)
    #
    # The old parser only knew the section headers, which ffmpeg 9 no longer
    # prints — so every device was filtered out and the app reported "no
    # microphone" on a machine that had one. Read both: the inline tag when it
    # is there, the section otherwise.
    names, audio_section = [], False
    for line in proc.stderr.splitlines():
        if "DirectShow audio devices" in line:
            audio_section = True
            continue
        if "DirectShow video devices" in line:
            audio_section = False
            continue
        if '"' not in line or "Alternative name" in line:
            continue
        name = line.split('"')[1]
        if "(audio)" in line:
            names.append(name)
        elif "(video)" in line:
            continue
        elif audio_section:
            names.append(name)
    # De-duplicated, order kept: a device can be listed twice when both formats
    # appear in one output.
    return list(dict.fromkeys(names))


def device_opens(name: str, timeout: float = 8.0) -> bool:
    """Can ffmpeg actually open this microphone right now?

    Listed and usable are different things. Tamara's machine offers her AirPods
    first and the built-in mic second; the AirPods are listed even when Windows
    is holding them as "Find My" rather than as a live input, so opening them
    fails and ffmpeg exits instantly — taking the whole recording with it.

    Half a second of audio to nowhere answers the question that a device list
    cannot."""
    exe = ffmpeg_path()
    if not exe or not name:
        return False
    try:
        r = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error",
             "-f", "dshow", "-i", f"audio={name}", "-t", "0.5", "-f", "null", "-"],
            capture_output=True, timeout=timeout,
            creationflags=NO_WINDOW, errors="replace")
        return r.returncode == 0
    except Exception:  # noqa: BLE001 — a probe that hangs is a device to avoid
        return False


_working_device: str | None = None


def pick_audio_device(preferred: str = "") -> str:
    """The first microphone that actually works, remembered for the session.

    Taking devices[0] blindly is what broke this: the list is ordered by
    Windows, not by usefulness, and a disconnected headset can sit at the top.

    A device the user chose explicitly is honoured without probing — it is
    their machine and their call, and a probe that wrongly fails would silently
    override them."""
    global _working_device
    if preferred:
        return preferred
    if _working_device:
        return _working_device
    for name in audio_devices():
        if device_opens(name):
            _working_device = name
            return name
    return ""


# --- privacy, the Windows way ------------------------------------------------
#
# There is no TCC here, so nothing to request: the microphone is a per-machine
# and per-user toggle in Settings, and the screen needs no permission at all
# (gdigrab reads the desktop directly). That is a real difference from the Mac
# and it cuts both ways — nothing to grant, but also no prompt, so a blocked
# microphone shows up only as a recording with silence in it.
#
# So the app has to notice and say so, which is the same job the Mac's
# CGRequestScreenCaptureAccess does, done differently.

MIC_SETTINGS_URI = "ms-settings:privacy-microphone"


def microphone_allowed() -> bool | None:
    """True / False / None when it cannot be determined.

    The consent store is per-user in the registry. None is a real answer and
    must not be treated as False — reporting "your microphone is blocked" to
    someone whose microphone is fine is worse than saying nothing."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg
        key = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
               r"\CapabilityAccessManager\ConsentStore\microphone")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value, _ = winreg.QueryValueEx(k, "Value")
        return str(value).lower() == "allow"
    except Exception:  # noqa: BLE001 — absent key, policy, locked-down machine
        return None


def open_microphone_settings() -> None:
    """Take them to the toggle instead of describing where it lives."""
    try:
        os.startfile(MIC_SETTINGS_URI)  # noqa: S606 — a Settings URI, not a path
    except Exception:  # noqa: BLE001
        subprocess.Popen(["cmd", "/c", "start", "", MIC_SETTINGS_URI],
                         creationflags=NO_WINDOW)
