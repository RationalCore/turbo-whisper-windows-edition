@echo off
chcp 65001 >nul
title TurboWhisper Build
setlocal enabledelayedexpansion

set "PROJECT_DIR=%~dp0"
set "BUILD_SPEC=%PROJECT_DIR%build_exe.spec"
set "DIST_DIR=%PROJECT_DIR%dist"

echo ============================================
echo  TurboWhisper - Building Executable
echo ============================================
echo.

:: 1. Find Python
echo [1/6] Locating Python...
set "PYTHON="

:: Try PATH first
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    for /f "delims=" %%P in ('where python') do (
        if not defined PYTHON set "PYTHON=%%P"
    )
)

:: Try py launcher
if not defined PYTHON (
    where py >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)"') do (
            set "PYTHON=%%P"
        )
    )
)

:: Try common install paths
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

:python_found
if not defined PYTHON (
    echo [!] Python not found. Install Python 3.10+ and add to PATH.
    goto :error
)
echo    Python: %PYTHON%
"%PYTHON%" --version
echo.

:: ============================================================
:: 2. Ensure uv is available
:: ============================================================
echo [2/6] Checking uv...
set "UV_CMD="

where uv >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "UV_CMD=uv"
    goto :uv_ok
)

"%PYTHON%" -m uv --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "UV_CMD=%PYTHON% -m uv"
    goto :uv_ok
)

echo    uv not found. Installing...
powershell -ExecutionPolicy ByPass -NoProfile -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; iex (irm https://astral.sh/uv/install.ps1)"
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\uv;%PATH%"

where uv >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "UV_CMD=uv"
    goto :uv_ok
)
"%PYTHON%" -m uv --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "UV_CMD=%PYTHON% -m uv"
    goto :uv_ok
)

echo [!] Failed to install uv. Install manually: pip install uv
goto :error

:uv_ok
echo    uv OK
echo.

:: ============================================================
:: 3. Ensure PortAudio is available for pyaudio build
:: ============================================================
echo [3/6] Checking PortAudio...
set "PORTAUDIO_DIR=C:\portaudio"
if exist "C:\portaudio\include\portaudio.h" goto :portaudio_ready

echo    PortAudio not found. Building from source...
echo    Requires: git, Visual Studio Build Tools, internet.

"%PYTHON%" -m pip install --quiet cmake 2>nul
set "PATH=%APPDATA%\Python\Python314\Scripts;%PATH%"

set "PA_SRC=%TEMP%\portaudio_build"
if exist "%PA_SRC%" rmdir /s /q "%PA_SRC%" >nul 2>&1
git clone --depth 1 https://github.com/PortAudio/portaudio.git "%PA_SRC%" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Failed to download PortAudio. Install git and retry.
    goto :error
)

mkdir "%PA_SRC%\build" >nul 2>&1
cmake -S "%PA_SRC%" -B "%PA_SRC%\build" -DPA_BUILD_SHARED_LIBS=ON >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] cmake configure failed. Install Visual Studio Build Tools.
    goto :error
)
cmake --build "%PA_SRC%\build" --config Release >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] PortAudio build failed.
    goto :error
)

mkdir "C:\portaudio\include" >nul 2>&1
mkdir "C:\portaudio\lib" >nul 2>&1
copy /y "%PA_SRC%\include\portaudio.h" "C:\portaudio\include\" >nul 2>&1
copy /y "%PA_SRC%\build\Release\portaudio.lib" "C:\portaudio\lib\" >nul 2>&1
copy /y "%PA_SRC%\build\Release\portaudio.dll" "C:\portaudio\lib\" >nul 2>&1
copy /y "%PA_SRC%\build\Release\portaudio.exp" "C:\portaudio\lib\" >nul 2>&1
echo    PortAudio installed to C:\portaudio

:portaudio_ready
set "VCPKG_PATH=%PORTAUDIO_DIR%"
set "INCLUDE=%PORTAUDIO_DIR%\include;%INCLUDE%"
set "LIB=%PORTAUDIO_DIR%\lib;%LIB%"
echo    PortAudio OK
echo.

:: ============================================================
:: 4. Sync dependencies via uv
:: ============================================================
echo [4/6] Syncing project dependencies...
cd /d "%PROJECT_DIR%"
%UV_CMD% sync --quiet 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] uv sync failed.
    goto :error
)

:: Copy portaudio.dll next to pyaudio module
for %%F in (".\.venv\Lib\site-packages\pyaudio\_*portaudio*.pyd") do (
    copy /y "C:\portaudio\lib\portaudio.dll" "%%~dpF" >nul 2>&1
)

:: Install PyInstaller into venv (uv pip, since venv has no pip.exe)
echo    Installing PyInstaller...
%UV_CMD% pip install --python .venv\Scripts\python.exe pyinstaller >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Failed to install PyInstaller.
    goto :error
)
for /f "delims=" %%V in ('".venv\Scripts\python.exe" -m PyInstaller --version') do set "PI_VER=%%V"
echo    PyInstaller v%PI_VER%
echo.

:: ============================================================
:: 5. Clean previous build
:: ============================================================
echo [5/6] Cleaning previous build...
if exist "%DIST_DIR%\TurboWhisper.exe" del /f /q "%DIST_DIR%\TurboWhisper.exe" >nul 2>&1
if exist "%PROJECT_DIR%build" rmdir /s /q "%PROJECT_DIR%build" >nul 2>&1
if exist "%PROJECT_DIR%dist" rmdir /s /q "%PROJECT_DIR%dist" >nul 2>&1
echo    Done.
echo.

:: ============================================================
:: 6. Build with PyInstaller
:: ============================================================
echo [6/6] Building TurboWhisper.exe...
".venv\Scripts\python.exe" -m PyInstaller --clean "%BUILD_SPEC%"
if %ERRORLEVEL% NEQ 0 (
    echo [!] PyInstaller build FAILED!
    goto :error
)

:: Verify output
if not exist "%DIST_DIR%\TurboWhisper.exe" (
    echo [!] TurboWhisper.exe not found after build!
    goto :error
)

for %%I in ("%DIST_DIR%\TurboWhisper.exe") do set "FILE_SIZE=%%~zI"
set /a "FILE_MB=!FILE_SIZE! / 1048576"

echo.
echo ============================================
echo  Build SUCCESSFUL!
echo ============================================
echo.
echo  Output: %DIST_DIR%\TurboWhisper.exe
echo  Size:   !FILE_MB! MB
echo.
goto :end

:error
echo.
echo ============================================
echo  Build FAILED!
echo ============================================
echo.
pause
exit /b 1

:end
endlocal
exit /b 0
