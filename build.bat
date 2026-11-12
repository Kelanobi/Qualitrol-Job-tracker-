@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
python -m pytest
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean DMSOrderTracker.spec
if errorlevel 1 exit /b 1
echo Build complete: dist\DMS Order Tracker\DMS Order Tracker.exe

