# Maestro Bar for Windows

The same bar as the Mac version. A strip one button wide parks against the
right edge of the screen, level with the middle; the panel opens beside it,
holding the review queue, a box for capturing a thought, and a box for asking
the company brain.

`Ctrl+Alt+M` shows and hides it. There is also a tray icon.

```
                                          +---+
   +------------------------------------+ | : |  drag it; it returns to an edge
   | Review                  <  1 of 3 > | +---+
   | Follow up on the Ohrid shoot       | | M |  open Maestro
   | Kupola Media                       | | o |  record audio
   | Hi Marko, thanks for the call ...  | | # |  record the screen
   | + Done   Dismiss   Send the email  | | < |  open and close the panel
   |                                    | +---+
   | Review 3 . Capture . Ask           | | X |  put it away
   | +--------------------------------+ | +---+
   | | Tell the agent what to change  | |  46px
   | +--------------------------------+ |
   +------------------------------------+
```

Idle, the strip says nothing at all. While a recording runs it shows a timer
and the button that started it turns red. Dropped anywhere, it returns to the
nearer edge.

It reads **the same `bar.json`** the Mac app reads, and shows **the same page**:
`ui/` here is a copy of `MaestroBar/ui` in the Mac repo, made by
`scripts/sync-ui.sh` there. Edit it in one place, run that script, commit both.
Do not edit `ui/` here by hand.

The page runs in QtWebEngine, so the .exe now carries Chromium and is about
150 MB larger than the old one. That is the price of the two platforms looking
identical rather than approximately alike.

The window is excluded from screen capture with `SetWindowDisplayAffinity`, so
the bar is not in a shared screen or a recording. That needs Windows 10 2004 or
newer; older builds ignore it and the bar is simply visible. `"invisible": false`
in `bar.json` turns it off.

## Running it (no .exe yet)

Unzip the folder anywhere and double-click **START HERE.bat**.

The first run makes a private Python environment inside the folder and
installs what it needs, which takes a few minutes and happens once. After that
it starts in a couple of seconds. A console window stays open while the app
runs; leave it there, closing it stops the app.

If Windows says Python is missing, install it once:

```
winget install Python.Python.3.12
```

Windows may also mark the files as coming from the internet and refuse to run
the .bat. Right-click it, choose **Properties**, tick **Unblock**, then run it
again.

## Later: a real .exe

Once it works, `build.bat` on the same machine produces
`dist\MaestroBar.exe` — a single file that needs no Python and no console
window. It has to be built on Windows; it cannot be cross-compiled from a Mac.

Double-click **MaestroBar.exe**. That is the whole install — it needs no
administrator rights and writes nothing outside your own user folder.

Windows SmartScreen will stop it the first time, because the file is not
signed by a paid certificate: click **More info**, then **Run anyway**. Once.

Then from the tray icon:

- **Set API token…** — paste the token you were given. It goes into Windows
  Credential Manager, never into a file.
- **Choose microphone…** — DirectShow addresses microphones by name and every
  machine names them differently, so pick yours once.
- **Check setup** — says what is ready and what is missing.

Recording needs ffmpeg, once:

```
winget install Gyan.FFmpeg
```

Transcription is optional. Without `faster-whisper` installed, recordings are
saved as audio and nothing reads them.

## Build the .exe

On a Windows machine with Python 3.10 or newer:

```
build.bat
```

That makes a virtual environment, installs the dependencies, and writes
`dist\MaestroBar.exe`. Send that single file.

To run from source instead, without building:

```
run.bat
```

`--onefile` unpacks the app on every launch, so the first start takes a few
seconds. Drop that flag in `build.bat` for a folder that starts instantly.

## Where things live

| What | Where |
| --- | --- |
| Config | `%APPDATA%\Maestro\bar.json` |
| Position of the strip | `%APPDATA%\Maestro\state.json` |
| Recordings and transcripts | `%USERPROFILE%\Recordings` |
| Captures | `%USERPROFILE%\Recordings\captures.md` |
| Anything that failed to send | `%USERPROFILE%\Recordings\unsent` |
| Token | Windows Credential Manager, under `maestro-token` |

## What each icon does

**Review** reads `/sales/actions?status=pending`. ✓ marks the card done, the
`…` menu holds Dismiss and Send the email, and ‹ › move through the queue. An
action fires optimistically: the card leaves at once, and if the request fails
the list reloads and a notification says so.

The line at the bottom of that flyout does not email anyone. It posts to
`/sales/actions/{id}/instruct`, which tells the agent what to change about the
card in front of you.

**Capture** appends to `Recordings\captures.md`. When there is an endpoint that
turns a note into a ticket, put its path in `compose.path` and the same box
starts posting instead.

**Record audio** and **Record screen** save to `Recordings` and then transcribe
and push to `/meetings/ingest`. Only the icon whose recording is running turns
red. If the push fails the transcript waits in `unsent`, and

```
python push.py --flush
```

sends the backlog.

## Two things that differ from the Mac, by necessity

**Stopping ffmpeg.** Windows has no usable SIGINT for this, so the recorder
writes `q` to ffmpeg's own stdin. Killing the process instead leaves an
unplayable file with no moov atom, which is how recordings get silently lost.

**Naming the microphone.** DirectShow wants a device name, not an index, and
the names differ on every machine. The app lists what Windows reports and uses
the first one until you choose.

## If something is wrong

- **Nothing happens on Ctrl+Alt+M** — another app owns that combination.
  Change `sidebar.hotkey` in `bar.json` (for example `<ctrl>+<shift>+m`) and
  use Reload config in the tray menu.
- **No strip and no tray icon** — it is running but the tray is collapsed.
  Check the `^` overflow arrow next to the clock and drag the icon out.
- **Review says "Nothing waiting" forever** — no token, or an expired one.
  Tray → Check setup.
- **Recordings are silent** — wrong input. Tray → Choose microphone.
