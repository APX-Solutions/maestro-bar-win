# Windows toolbar — parity with the Mac app

What each side does, and where they genuinely differ. Written after bringing
the Windows twin up to the Mac's current behaviour, so the differences below
are deliberate rather than missing work.

## The same

| | macOS | Windows |
| --- | --- | --- |
| record audio | ffmpeg · avfoundation | ffmpeg · dshow |
| record screen + audio | `screencapture -v -g` | ffmpeg · gdigrab + dshow |
| ffmpeg | **bundled** in the .app | **bundled** in the .exe |
| what gets sent | the media itself | the media itself |
| endpoint | `/recordings/upload-url` → S3 → `/recordings/ingest` | same |
| token | `~/.maestro/token`, then Keychain | `~/.maestro/token`, then Credential Manager |
| nothing lost on failure | queued in `unsent-media` | queued in `unsent-media` |
| retry | Retry unsent recordings | Retry unsent recordings |
| config | `~/.config/maestro/bar.json` | `bar.json` beside the app |

## Genuinely different, and why

**Permissions.** macOS gates screen capture behind TCC, so the app asks with
`CGRequestScreenCaptureAccess` and offers a button to the exact settings pane.
Windows has no equivalent: `gdigrab` reads the desktop with no permission at
all, and the microphone is a Settings toggle with **no prompt**.

That cuts both ways. Nothing to grant, but also nothing to notice — a blocked
microphone shows up only as a recording full of silence. So the Windows app
reads the consent store, and when a recording comes back empty it says
*"Windows is blocking the microphone"* and opens `ms-settings:privacy-microphone`,
rather than the Mac's ask-first flow. Same job, opposite mechanism.

`microphone_allowed()` returns `True`, `False` or **`None`**. None is a real
answer — locked-down machines and group policy hide that key — and is never
treated as blocked. Telling someone their microphone is off when it is fine is
worse than saying nothing.

**Screen capture quality.** `screencapture` is a first-party recorder; gdigrab
is a screen scraper at a fixed framerate (15fps here). Windows captures will be
larger and less smooth. That is a real difference in the output, not a bug.

**No code signature.** The Mac app is ad-hoc signed, which is what makes its
permission grants stick to an identity. Windows has no equivalent requirement
here, so a rebuild does not invalidate anything — the one place Windows is
simpler.

## Left behind on purpose

`push.py` still transcribes locally and posts text to `/meetings/ingest`. It is
no longer on the recording path — `send.py` replaced it — but it is kept for
the meetings flow and for anyone running it by hand.

`faster-whisper` is therefore no longer a requirement. Recordings are read
server-side, which is the point: a speech model on every laptop is what made
the Mac side fail silently when it was missing.
