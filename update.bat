@echo off
REM Pull the latest Maestro Bar and restart it.
REM
REM Everything that is downloaded rather than written -- the virtual
REM environment and ffmpeg -- is ignored by git, so an update touches only the
REM code. Nothing is re-downloaded and the app restarts immediately.
cd /d "%~dp0"

where git >nul 2>&1
if errorlevel 1 (
  echo   Git is not installed. Install it once with:
  echo       winget install Git.Git
  echo   then run this file again.
  pause
  exit /b 1
)

echo   Updating...
git pull --ff-only || goto :fail

echo.
echo   Updated. Starting Maestro Bar.
echo.
start "" "%~dp0START HERE.bat"
exit /b 0

:fail
echo.
echo   Update failed. If it mentions local changes, send the lines above back.
pause
exit /b 1
