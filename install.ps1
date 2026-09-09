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

# A python that actually runs.
#
# "Does the command exist" is not the question. Windows ships an App Execution
# Alias at WindowsApps\python.exe that exists whether or not Python is
# installed; running it prints "Python was not found; run without arguments to
# install from the Microsoft Store" and exits. Get-Command finds it, so the
# check passed and setup died several steps later with that message — which is
# exactly what happened on the first real Windows install.
#
# So: run each candidate and believe only the ones that answer with a version.
function Find-RealPython {
  foreach ($c in @('python', 'python3', 'py')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    # Skip the Store alias by path as well as by behaviour — belt and braces.
    if ($cmd.Source -and $cmd.Source -like '*WindowsApps*') { continue }
    try {
      $v = & $cmd.Source '--version' 2>&1
      if ($v -match 'Python 3\.(\d+)' -and [int]$Matches[1] -ge 9) { return $cmd.Source }
    } catch { }
  }
  return $null
}

$py = Find-RealPython
if (-not $py) {
  Write-Host "  Python is missing. Installing it (one time, a few minutes)..."
  try {
    winget install --id Python.Python.3.12 --silent --scope user `
      --accept-package-agreements --accept-source-agreements | Out-Null
  } catch {
    Fail "could not install Python automatically. Run this once, then paste the install line again:  winget install Python.Python.3.12"
  }
  # A fresh install is not on PATH in THIS window, so look where winget puts it
  # rather than telling someone to open a new terminal and start over.
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
              [Environment]::GetEnvironmentVariable('Path','User')
  $py = Find-RealPython
  if (-not $py) {
    $guess = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
             Sort-Object FullName -Descending | Select-Object -First 1
    if ($guess) { $py = $guess.FullName }
  }
  if (-not $py) { Fail "Python was installed but cannot be found. Close PowerShell, open it again, and paste the install line once more." }
}
Write-Host "  Python: $py"

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

# Build the environment HERE, with the python we just verified.
#
# START HERE.bat would otherwise do it by calling bare "python", which is the
# Store alias again — the same failure, one step later. Doing it here also puts
# the slow part (a ~110MB ffmpeg download) in front of someone who is watching,
# instead of behind a window they were told they could close.
$venv = Join-Path $dest '.venv'
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
  Write-Host "  Setting up (a few minutes, once)..."
  & $py -m venv $venv
  if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) { Fail "could not create the Python environment." }
}
$vpy = Join-Path $venv 'Scripts\python.exe'
& $vpy -m pip install --upgrade pip --quiet 2>&1 | Out-Null
& $vpy -m pip install -r (Join-Path $dest 'requirements.txt') --quiet
if ($LASTEXITCODE -ne 0) { Fail "could not install what the app needs. Send the lines above back." }

Write-Host "  Getting ffmpeg (~110 MB, once)..."
Push-Location $dest
& $vpy (Join-Path $dest 'vendor_ffmpeg.py')
Pop-Location


# Start at login, with no console window.
#
# The first run below keeps its window on purpose — that is where setup errors
# are readable. Every run after it is silent: pythonw has no console, and a
# shortcut in Startup means nobody has to remember to launch anything or keep a
# black window open. The venv it points at does not exist yet; the first run
# creates it, and the shortcut is only used from the next login onwards.
# Two shortcuts, both pointing at pythonw (no console window):
#   Startup    - so it is simply running, every day, without anyone deciding to
#   Start Menu - so "Maestro" in the Start box finds it, the way an app should
#                be found. Telling someone to open a .bat inside a folder they
#                have to navigate to is not an app, it is a chore.
try {
  $w = New-Object -ComObject WScript.Shell
  $target = Join-Path $dest '.venv\Scripts\pythonw.exe'
  foreach ($dir in @([Environment]::GetFolderPath('Startup'),
                     [Environment]::GetFolderPath('Programs'))) {
    $sc = $w.CreateShortcut((Join-Path $dir 'Maestro Bar.lnk'))
    $sc.TargetPath       = $target
    $sc.Arguments        = 'maestro_bar.py'
    $sc.WorkingDirectory = $dest
    $sc.Description      = 'Maestro Bar'
    $sc.Save()
  }
  Write-Host "  Added to Start, and starts automatically when you log in."
} catch {
  Write-Host "  (could not add shortcuts - not fatal, you can open it from $dest)"
}
Write-Host ""
Write-Host "  Starting it now. You can close the window it opens."
Write-Host ""
Write-Host "  Press Ctrl+Alt+M to show and hide the bar. That is all you need to do."
Write-Host "  If you ever quit it: press Start, type Maestro, press Enter."
Write-Host ""
Write-Host "  Later, to update: double-click update.bat in $dest"
Write-Host ""

Start-Process -FilePath (Join-Path $dest 'START HERE.bat') -WorkingDirectory $dest
