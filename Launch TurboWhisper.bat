@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: TurboWhisper Launcher
:: Checks for Visual C++ Runtime and installs if missing.

set "EXE_DIR=%~dp0"
set "EXE_PATH=%EXE_DIR%TurboWhisper.exe"

:: Check if exe exists
if not exist "%EXE_PATH%" (
    echo [ERROR] TurboWhisper.exe not found in %EXE_DIR%
    echo Please ensure this script is in the same folder as TurboWhisper.exe.
    pause
    exit /b 1
)

:: Check for VC++ 2015-2022 Redistributable (x64)
set "VC_OK=0"

:: Check System32 for vcruntime140.dll (most reliable check)
if exist "%SystemRoot%\System32\vcruntime140.dll" set "VC_OK=1"

:: Also check registry
if "%VC_OK%"=="0" (
    reg query "HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" /v Major >nul 2>&1
    if !ERRORLEVEL! EQU 0 set "VC_OK=1"
)

if "%VC_OK%"=="1" (
    goto :run_app
)

:: VC++ Runtime not found — offer to install
echo.
echo =============================================
echo  Visual C++ Runtime Required
echo =============================================
echo.
echo  TurboWhisper requires the Microsoft Visual C++
echo  2015-2022 Redistributable (x64) to run.
echo.
echo  It was not detected on your system.
echo.

:: Try winget first
where winget >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo  Installing via winget...
    echo.
    winget install --id Microsoft.VCRedist.2015+.x64 --accept-source-agreements --accept-package-agreements --silent
    if %ERRORLEVEL% EQU 0 (
        echo.
        echo  VC++ Runtime installed successfully!
        echo.
        goto :run_app
    )
    echo  winget failed. Trying direct download...
    echo.
)

:: Download VC++ redistributable installer
echo  Downloading Visual C++ Redistributable...
echo.

set "VC_URL=https://aka.ms/vs/17/release/vc_redist.x64.exe"
set "VC_TEMP=%TEMP%\vc_redist.x64.exe"

powershell -ExecutionPolicy ByPass -NoProfile -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%VC_URL%' -OutFile '%VC_TEMP%' -UseBasicParsing" >nul 2>&1

if not exist "%VC_TEMP%" (
    echo [ERROR] Failed to download VC++ Redistributable.
    echo.
    echo  Please download and install manually:
    echo  https://aka.ms/vs/17/release/vc_redist.x64.exe
    echo.
    pause
    exit /b 1
)

echo  Installing Visual C++ Redistributable...
echo  (A UAC prompt may appear)
echo.

"%VC_TEMP%" /install /quiet /norestart
set "INSTALL_ERR=%ERRORLEVEL!"

del /f /q "%VC_TEMP%" >nul 2>&1

if %INSTALL_ERR% NEQ 0 (
    if %INSTALL_ERR% NEQ 1638 (
        echo [WARNING] Installer returned code %INSTALL_ERR%.
        echo  Attempting to launch TurboWhisper anyway...
        echo.
    )
)

if exist "%SystemRoot%\System32\vcruntime140.dll" (
    echo  VC++ Runtime installed successfully!
    echo.
) else (
    echo [WARNING] Could not verify VC++ Runtime installation.
    echo  If TurboWhisper fails, install manually:
    echo  https://aka.ms/vs/17/release/vc_redist.x64.exe
    echo.
)

:run_app
:: Launch TurboWhisper
echo  Starting TurboWhisper...
start "" "%EXE_PATH%"
exit /b 0
