@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.13 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" app\doctor.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" app\install_settings.py
if errorlevel 1 goto failed
echo Ready. Double-click start.cmd to open Bili Note.
pause
exit /b 0
:failed
echo Installation failed or cancelled. Check the message above and retry install.cmd.
echo Python 3.13 x64 with Python Launcher and a writable notes folder are required.
pause
exit /b 1
