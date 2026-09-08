@echo off
REM Update Maestro Bar to the latest code and restart it.
REM
REM Downloads the current main branch straight from GitHub, so this works for a
REM zip install as well as a clone and needs no git. Everything that is
REM downloaded rather than written -- the virtual environment and ffmpeg -- is
REM left alone, so an update takes seconds and re-downloads nothing.
setlocal
cd /d "%~dp0"

echo.
echo   Maestro Bar - update
echo   ====================
echo.

taskkill /f /im pythonw.exe >nul 2>&1
taskkill /f /im python.exe >nul 2>&1

set "ZIP=%TEMP%\maestrobar-update.zip"
set "OUT=%TEMP%\maestrobar-update"

echo   Downloading the latest version...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "try {" ^
  "  Invoke-WebRequest -UseBasicParsing 'https://github.com/APX-Solutions/maestro-bar-win/archive/refs/heads/main.zip' -OutFile $env:ZIP;" ^
  "  if (Test-Path $env:OUT) { Remove-Item -Recurse -Force $env:OUT }" ^
  "  Expand-Archive -Path $env:ZIP -DestinationPath $env:OUT -Force;" ^
  "  exit 0" ^
  "} catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 goto :fail

REM Copy the code over the top. .venv, ffmpeg and Recordings are not in the zip,
REM so they survive untouched -- that is what makes this fast.
echo   Installing...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "try {" ^
  "  $src = Get-ChildItem -Directory $env:OUT | Select-Object -First 1;" ^
  "  Copy-Item -Path ($src.FullName + '\*') -Destination '%~dp0' -Recurse -Force;" ^
  "  exit 0" ^
  "} catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 goto :fail

del "%ZIP%" >nul 2>&1
rmdir /s /q "%OUT%" >nul 2>&1

echo.
echo   Updated. Starting Maestro Bar.
echo.
start "" "%~dp0START HERE.bat"
exit /b 0

:fail
echo.
echo   Update failed - the lines above say why. Your current version still works.
echo   Nothing was changed.
pause
exit /b 1
