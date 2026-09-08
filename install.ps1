# First-time setup for Maestro Bar on Windows. One line in PowerShell:
#
#   $env:MAESTRO_TOKEN='YOUR_TOKEN'; irm https://raw.githubusercontent.com/APX-Solutions/maestro-bar-win/main/install.ps1 | iex
#
# Replaces unzipping by hand and creating the token file in Notepad — which is
# where every Windows setup so far has gone wrong: Notepad saves UTF-8 with a
# byte order mark and silently appends .txt, and neither is visible afterwards.

$ErrorActionPreference = 'Stop'

function Fail($msg) { Write-Host ""; Write-Host "  STOP: $msg" -ForegroundColor Red; Write-Host ""; exit 1 }

Write-Host ""
Write-Host "  Maestro Bar - setup"
Write-Host "  ==================="
Write-Host ""

$token = $env:MAESTRO_TOKEN
if (-not $token) { Fail "no token. The line you were sent starts with `$env:MAESTRO_TOKEN='...' - paste the whole thing." }
$token = $token.Trim()
if ($token.Length -lt 20) { Fail "that token looks too short - it was probably cut off. Ask for it again." }
if ($token -notmatch '^[A-Za-z0-9_\-]+$') { Fail "that token has characters a token never contains - it was probably cut off." }

# Python first: without it the app cannot start, and finding that out after
# downloading everything wastes the one attempt someone is willing to make.
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Host "  Python is missing. Installing it (one time)..."
  try { winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements | Out-Null }
  catch { Fail "could not install Python. Run this once, then try again:  winget install Python.Python.3.12" }
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
  if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail "Python installed but this window cannot see it yet. Close PowerShell, open it again, and run the line again."
  }
}

# ASCII, no BOM, no trailing newline. utf8 in PowerShell 5.1 writes a BOM, and
# the app reading it back gets an invisible character glued to the token.
$dir = Join-Path $env:USERPROFILE '.maestro'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
[IO.File]::WriteAllText((Join-Path $dir 'token'), $token, [Text.Encoding]::ASCII)
Write-Host "  Token saved."

$dest = Join-Path $env:USERPROFILE 'MaestroBar'
$zip  = Join-Path $env:TEMP 'maestrobar-setup.zip'
$out  = Join-Path $env:TEMP 'maestrobar-setup'

Write-Host "  Downloading..."
try {
  Invoke-WebRequest -UseBasicParsing 'https://github.com/APX-Solutions/maestro-bar-win/archive/refs/heads/main.zip' -OutFile $zip
  if (Test-Path $out) { Remove-Item -Recurse -Force $out }
  Expand-Archive -Path $zip -DestinationPath $out -Force
} catch { Fail "download failed: $($_.Exception.Message)" }

$src = Get-ChildItem -Directory $out | Select-Object -First 1
if (-not $src) { Fail "the download was empty. Try the line again." }

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Path (Join-Path $src.FullName '*') -Destination $dest -Recurse -Force
Remove-Item -Force $zip -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue

Write-Host "  Installed to $dest"
Write-Host ""
Write-Host "  Starting it now. The FIRST run takes a few minutes: it builds its own"
Write-Host "  Python environment and downloads ffmpeg (~110 MB). That happens once."
Write-Host ""
Write-Host "  Leave the black window open - closing it stops the app."
Write-Host "  Press Ctrl+Alt+M to show and hide the bar."
Write-Host ""
Write-Host "  Then, from the icon near the clock: 'Check setup', and"
Write-Host "  'Choose microphone...' - Windows names every mic differently and the"
Write-Host "  app cannot guess yours. Skip it and recordings stop the second they start."
Write-Host ""
Write-Host "  Later, to update: double-click update.bat in $dest"
Write-Host ""

Start-Process -FilePath (Join-Path $dest 'START HERE.bat') -WorkingDirectory $dest
