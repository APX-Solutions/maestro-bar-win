@echo off
REM Build a single MaestroBar.exe into dist\.
REM The bar is a web page (ui\) shown in QtWebEngine, so the exe carries
REM Chromium: expect ~150 MB more than before. PyInstaller's PySide6 hooks
REM pick up QtWebEngineProcess and its resources on their own.
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv || goto :nopython
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\pip install pyinstaller

REM ffmpeg goes IN the exe. Screen capture (gdigrab) and audio (dshow) both go
REM through it, so without one the app does nothing at all -- and asking every
REM person to install it first is how the Mac side ended up with people
REM recording into an empty file and no explanation.
.venv\Scripts\python vendor_ffmpeg.py
if not exist vendor\ffmpeg.exe (
  echo.
  echo WARNING: no vendor\ffmpeg.exe. The app will need ffmpeg already on the
  echo machine, and will say so rather than failing silently.
  echo.
)

.venv\Scripts\pyinstaller ^
  --noconfirm --clean --onefile --windowed ^
  --name MaestroBar ^
  --add-data "bar.json;." ^
  --add-data "ui;ui" ^
  --add-binary "vendor\ffmpeg.exe;." ^
  --hidden-import keyring.backends.Windows ^
  --hidden-import PySide6.QtWebEngineWidgets ^
  --hidden-import PySide6.QtWebChannel ^
  --collect-submodules pynput ^
  maestro_bar.py

echo.
echo Built dist\MaestroBar.exe  -- send this file
echo ffmpeg is inside it: nothing to install on the other machine.
echo.
echo Note: --onefile unpacks on every launch, so the first start takes a few
echo seconds. Drop --onefile for a folder that starts instantly.
pause
exit /b 0

:nopython
echo Python was not found. Install it with:  winget install Python.Python.3.12
pause
