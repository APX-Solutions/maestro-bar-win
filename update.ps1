# Update Maestro Bar. One line in PowerShell:
#
#   irm https://raw.githubusercontent.com/APX-Solutions/maestro-bar-win/main/update.ps1 | iex
#
# The same shape as the Mac's update.sh, and for the same reason: an update
# people have to go and find in a folder is an update that does not happen.
# No token — this never touches ~/.maestro/token.

$ErrorActionPreference = 'Stop'
function Fail($m) { Write-Host ""; Write-Host "  STOP: $m" -ForegroundColor Red; Write-Host ""; exit 1 }

Write-Host ""
Write-Host "  Maestro Bar - update"
Write-Host "  ===================="
Write-Host ""

$dest = Join-Path $env:USERPROFILE 'MaestroBar'
if (-not (Test-Path $dest)) { Fail "Maestro Bar is not installed here. Use the install line you were sent." }

# Stop it first: a running app holds its files open, and copying over a live
# process is how you get a half-updated folder that fails in a new way.
Get-Process pythonw, python -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -and $_.Path.StartsWith($dest) } |
  Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

$zip = Join-Path $env:TEMP 'maestrobar-update.zip'
$out = Join-Path $env:TEMP 'maestrobar-update'

Write-Host "  Downloading the latest version..."
try {
  Invoke-WebRequest -UseBasicParsing 'https://github.com/APX-Solutions/maestro-bar-win/archive/refs/heads/main.zip' -OutFile $zip
  if (Test-Path $out) { Remove-Item -Recurse -Force $out }
  Expand-Archive -Path $zip -DestinationPath $out -Force
} catch { Fail "download failed: $($_.Exception.Message)  (your current version still works)" }

$src = Get-ChildItem -Directory $out | Select-Object -First 1
if (-not $src) { Fail "the download was empty. Try again in a minute." }

# Code only. .venv, ffmpeg and Recordings are not in the archive, so they are
# untouched — which is what makes an update seconds rather than another 110MB.
Copy-Item -Path (Join-Path $src.FullName '*') -Destination $dest -Recurse -Force
Remove-Item -Force $zip -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue

# Refresh the shortcuts too: someone who installed before they existed should
# get them from an update rather than being told to reinstall.
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
} catch { }

$pyw = Join-Path $dest '.venv\Scripts\pythonw.exe'
if (Test-Path $pyw) {
  Start-Process -FilePath $pyw -ArgumentList 'maestro_bar.py' -WorkingDirectory $dest -WindowStyle Hidden
  Write-Host ""
  Write-Host "  Updated. Maestro Bar is running again - press Ctrl+Alt+M."
  Write-Host ""
} else {
  # No environment yet: the install never finished, so send them through the
  # path that builds one instead of pretending this worked.
  Write-Host ""
  Write-Host "  Updated, but the Python environment is missing - starting setup."
  Write-Host ""
  Start-Process -FilePath (Join-Path $dest 'START HERE.bat') -WorkingDirectory $dest
}
