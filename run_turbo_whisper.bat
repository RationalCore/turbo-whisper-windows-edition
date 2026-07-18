@echo off
title Turbo Whisper
cd /d "%~dp0"

:: ============================================================
:: 1. Ensure uv is available
:: ============================================================
where uv >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :uv_ok

python -m uv --version >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :uv_ok

echo [setup] uv not found. Installing...
powershell -ExecutionPolicy ByPass -NoProfile -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; iex (irm https://astral.sh/uv/install.ps1)"
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\uv;%PATH%"

uv --version >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :uv_ok
python -m uv --version >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :uv_ok

echo [!] Failed to install uv. Install manually: pip install uv
pause
exit /b 1

:uv_ok
:: ============================================================
:: 2. Ensure PortAudio is available for pyaudio build
:: ============================================================
set "PORTAUDIO_DIR=C:\portaudio"
if exist "C:\portaudio\include\portaudio.h" goto :portaudio_ready

echo [setup] PortAudio not found. Building from source...
echo [setup] Requires: git, Visual Studio Build Tools, internet.

python -m pip install --quiet cmake 2>nul
set "PATH=%APPDATA%\Python\Python314\Scripts;%PATH%"

set "PA_SRC=%TEMP%\portaudio_build"
if exist "%PA_SRC%" rmdir /s /q "%PA_SRC%" >nul 2>&1
git clone --depth 1 https://github.com/PortAudio/portaudio.git "%PA_SRC%" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Failed to download PortAudio. Install git and retry.
    pause
    exit /b 1
)

mkdir "%PA_SRC%\build" >nul 2>&1
cmake -S "%PA_SRC%" -B "%PA_SRC%\build" -DPA_BUILD_SHARED_LIBS=ON >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] cmake configure failed. Install Visual Studio Build Tools.
    pause
    exit /b 1
)
cmake --build "%PA_SRC%\build" --config Release >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] PortAudio build failed.
    pause
    exit /b 1
)

mkdir "C:\portaudio\include" >nul 2>&1
mkdir "C:\portaudio\lib" >nul 2>&1
copy /y "%PA_SRC%\include\portaudio.h" "C:\portaudio\include\" >nul 2>&1
copy /y "%PA_SRC%\build\Release\portaudio.lib" "C:\portaudio\lib\" >nul 2>&1
copy /y "%PA_SRC%\build\Release\portaudio.dll" "C:\portaudio\lib\" >nul 2>&1
copy /y "%PA_SRC%\build\Release\portaudio.exp" "C:\portaudio\lib\" >nul 2>&1
echo [setup] PortAudio installed to C:\portaudio

:portaudio_ready
set "VCPKG_PATH=%PORTAUDIO_DIR%"
set "INCLUDE=%PORTAUDIO_DIR%\include;%INCLUDE%"
set "LIB=%PORTAUDIO_DIR%\lib;%LIB%"

:: ============================================================
:: 3. Install project dependencies
:: ============================================================
echo [setup] Syncing dependencies...
python -m uv sync --quiet 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] uv sync failed.
    pause
    exit /b 1
)

:: Copy portaudio.dll next to pyaudio module so it can be found at runtime
for %%F in (".\.venv\Lib\site-packages\pyaudio\_*portaudio*.pyd") do (
    copy /y "C:\portaudio\lib\portaudio.dll" "%%~dpF" >nul 2>&1
)

echo [setup] Dependencies ready.

:: ============================================================
:: 4. Run the application
:: ============================================================
python -m uv run python -m turbo_whisper.main
pause
