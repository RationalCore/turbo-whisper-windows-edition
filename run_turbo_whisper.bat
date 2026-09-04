@echo off
title Turbo Whisper
cd /d "%~dp0"

:: ============================================================
:: 1. Find or install Python
:: ============================================================
set "PYTHON="

where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    for /f "delims=" %%P in ('where python') do (
        if not defined PYTHON set "PYTHON=%%P"
    )
)

if not defined PYTHON (
    where py >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)"') do (
            set "PYTHON=%%P"
        )
    )
)

if not defined PYTHON (
    for %%V in (314 313 312 311 310) do (
        if exist "C:\Python%%V\python.exe" (
            set "PYTHON=C:\Python%%V\python.exe"
            goto :python_found
        )
        if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
            set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
            goto :python_found
        )
    )
)

if not defined PYTHON (
    echo [setup] Python not found. Installing via winget...
    where winget >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [!] winget not found. Install Python 3.12 manually from https://python.org
        pause
        exit /b 1
    )
    winget install --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [!] Failed to install Python via winget. Install manually from https://python.org
        pause
        exit /b 1
    )
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
    set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    if not exist "!PYTHON!" (
        echo [!] Python installed but not found. Restart your terminal and retry.
        pause
        exit /b 1
    )
    echo [setup] Python installed successfully.
)

:python_found
echo [setup] Python: %PYTHON%

:: ============================================================
:: 2. Ensure uv is available
:: ============================================================
where uv >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :uv_ok

echo [setup] uv not found. Installing...
powershell -ExecutionPolicy ByPass -NoProfile -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; iex (irm https://astral.sh/uv/install.ps1)"
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\uv;%PATH%"

uv --version >nul 2>&1
if %ERRORLEVEL% EQU 0 goto :uv_ok

echo [!] Failed to install uv. Install manually from https://docs.astral.sh/uv/
pause
exit /b 1

:uv_ok
echo [setup] uv OK

:: ============================================================
:: 3. Sync dependencies
:: ============================================================
echo [setup] Syncing dependencies...
uv sync --quiet 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] uv sync failed.
    pause
    exit /b 1
)
echo [setup] Dependencies ready.

:: ============================================================
:: 4. Run the application
:: ============================================================
uv run python -m turbo_whisper.main
pause
