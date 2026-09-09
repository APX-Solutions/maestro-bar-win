@echo off
title Maestro Bar
cd /d "%~dp0"

echo.
echo   Maestro Bar
echo   ===========
echo.

REM "where python" also finds the Windows Store alias, which is not Python: it
REM prints "Python was not found..." and exits. Ask for a version instead.
set "PY="
for %%C in (python python3 py) do (
  if not defined PY (
    %%C --version 2>nul | findstr /b /c:"Python 3" >nul && set "PY=%%C"
  )
)
if not defined PY (
  echo   Python is not installed. Install it once with this command,
  echo   then run this file again:
  echo.
  echo       winget install Python.Python.3.12
  echo.
  echo   If Windows opened the Microsoft Store instead, turn off the python
  echo   shortcut under Settings ^> Apps ^> Advanced app settings ^>
  echo   App execution aliases, then try again.
  echo.
  pause
  exit /b 1
)

if not exist .venv (
  echo   First run: setting up. This takes a few minutes and only happens once.
  echo.
  %PY% -m venv .venv || goto :fail
  .venv\Scripts\python -m pip install --upgrade pip --quiet
  .venv\Scripts\pip install -r requirements.txt || goto :fail
  echo.
  echo   Getting ffmpeg. It records the screen and the microphone, and this is
  echo   a ~110 MB download that takes a few minutes. It only happens once.
  echo.
  .venv\Scripts\python vendor_ffmpeg.py
  echo.
  echo   Ready.
  echo.
)

echo   Starting. Look for the icon near the clock, and press Ctrl+Alt+M.
echo   Leave this window open. Closing it stops the app.
echo.

REM The console stays visible on purpose while this is new: if anything goes
REM wrong the error is here to copy, instead of vanishing silently.
.venv\Scripts\python maestro_bar.py

echo.
echo   Maestro Bar stopped. If there is an error above, send it back.
pause
exit /b 0

:fail
echo.
echo   Setup failed. Send the lines above to whoever gave you this.
pause
exit /b 1
