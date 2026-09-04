@echo off
REM Run from source. First time it makes a virtual environment and installs
REM everything into it, which takes a few minutes.
cd /d "%~dp0"
if not exist .venv (
  echo Creating a virtual environment...
  python -m venv .venv || goto :nopython
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw maestro_bar.py
exit /b 0

:nopython
echo Python was not found. Install it with:  winget install Python.Python.3.12
pause
