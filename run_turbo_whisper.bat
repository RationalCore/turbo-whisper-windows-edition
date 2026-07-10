@echo off
title Turbo Whisper
cd /d "%~dp0"

:: Add src directory to Python path (works even if folder is renamed)
set PYTHONPATH=%~dp0src;%PYTHONPATH%

:: Install webrtcvad if not present
python -c "import webrtcvad" 2>nul
if errorlevel 1 (
    echo Installing webrtcvad...
    pip install webrtcvad
)

:: Install keyboard module if not present (for hotkey capture)
python -c "import keyboard" 2>nul
if errorlevel 1 (
    echo Installing keyboard module...
    pip install keyboard
)

python -m turbo_whisper.main
pause
