@echo off
cd /d "%~dp0"
python -c "import hid, customtkinter, PIL, pystray" 2>nul
if errorlevel 1 (
  echo Installing dependencies...
  python -m pip install -r requirements.txt
)
python app.py
pause
