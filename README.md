# Maestro Bar for Windows

The same strip as the Mac version, rebuilt natively for Windows. A thin bar of
icons parks against the edge of the screen; clicking one opens a small flyout
beside it. Drag it anywhere and it snaps back to the nearer edge.

`Ctrl+Alt+M` shows and hides it. There is also a tray icon.

```
   ┌────┐
   │ ▤ ●│   Review, with a dot when something is waiting
   │ ✎  │   Capture
   │ ⏺  │   Record audio
   │ ▣  │   Record screen and audio
   └────┘
```

It reads **the same `bar.json`** the Mac app reads, so the queue, the buttons,
the endpoints and the field mappings are defined once for both platforms.

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
