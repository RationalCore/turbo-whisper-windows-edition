@echo off
title Turbo Whisper
cd /d "%~dp0"

:: Install webrtcvad if not present
python -c "import webrtcvad" 2>nul
if errorlevel 1 (
    echo Installing webrtcvad...
    pip install webrtcvad
)

python -m turbo_whisper.main
pause
