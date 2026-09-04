@echo off
chcp 65001 >nul
title TurboWhisper Build (Compatibility)
setlocal enabledelayedexpansion

set "PROJECT_DIR=%~dp0"
set "BUILD_SPEC=%PROJECT_DIR%build_exe_compat.spec"
set "DIST_DIR=%PROJECT_DIR%dist\TurboWhisper"

echo ============================================
echo  TurboWhisper - Folder Build
echo  (onedir mode, no runtime extraction)
echo ============================================
echo.

:: 1. Find or install Python
echo [1/6] Locating Python...
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

:: Auto-install Python via winget
if not defined PYTHON (
    echo    Python not found. Installing via winget...
    where winget >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [!] winget not found. Install Python 3.12 manually from https://python.org
        goto :error
    )
    winget install --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [!] Failed to install Python via winget.
        echo     Install Python 3.10+ manually from https://python.org and add to PATH.
        goto :error
    )

    :: Refresh PATH and re-detect
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
    set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    if not exist "!PYTHON!" (
        for %%V in (312 311 310) do (
            if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
                set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
                goto :python_found
            )
        )
        echo [!] Python installed but not found. Restart your terminal and retry.
        goto :error
    )
    echo    Python installed successfully.
)

:python_found
if not defined PYTHON (
    echo [!] Python not found. Install Python 3.10+ and add to PATH.
    goto :error
)
echo    Python: %PYTHON%
"%PYTHON%" --version
echo.

:: 2. Ensure uv
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

echo [!] Failed to install uv.
goto :error

:uv_ok
echo    uv OK
echo.

:: 3. PortAudio (bundled in PyAudio wheel, no build needed)
echo [3/6] Checking PortAudio...
echo    PortAudio is bundled in PyAudio wheel. Skipping build.
echo    PortAudio OK
echo.

:: 4. Sync dependencies
echo [4/6] Syncing project dependencies...
cd /d "%PROJECT_DIR%"
%UV_CMD% sync --quiet 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] uv sync failed.
    goto :error
)

echo    Installing PyInstaller...
%UV_CMD% pip install --python .venv\Scripts\python.exe pyinstaller >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Failed to install PyInstaller.
    goto :error
)
for /f "delims=" %%V in ('".venv\Scripts\python.exe" -m PyInstaller --version') do set "PI_VER=%%V"
echo    PyInstaller v%PI_VER%
echo.

:: 5. Clean
echo [5/6] Cleaning previous build...
if exist "%PROJECT_DIR%build" rmdir /s /q "%PROJECT_DIR%build" >nul 2>&1
if exist "%PROJECT_DIR%dist" rmdir /s /q "%PROJECT_DIR%dist" >nul 2>&1
echo    Done.
echo.

:: 6. Build (onedir mode — folder with exe + DLLs)
echo [6/6] Building TurboWhisper (folder mode)...
".venv\Scripts\python.exe" -m PyInstaller --clean "%BUILD_SPEC%"
if %ERRORLEVEL% NEQ 0 (
    echo [!] PyInstaller build FAILED!
    goto :error
)

if not exist "%DIST_DIR%\TurboWhisper.exe" (
    echo [!] TurboWhisper.exe not found after build!
    goto :error
)

:: Copy launcher into the output folder
if exist "%PROJECT_DIR%Launch TurboWhisper.bat" (
    copy /y "%PROJECT_DIR%Launch TurboWhisper.bat" "%DIST_DIR%\" >nul 2>&1
    echo    Launcher copied to output folder.
)

:: Calculate total folder size
set "TOTAL_SIZE=0"
for /r "%DIST_DIR%" %%F in (*) do set /a "TOTAL_SIZE+=%%~zF"
set /a "TOTAL_MB=!TOTAL_SIZE! / 1048576"

echo.
echo ============================================
echo  Build SUCCESSFUL!
echo ============================================
echo.
echo  Output:    %DIST_DIR%\TurboWhisper.exe
echo  Size:      ~!TOTAL_MB! MB (entire folder)
echo  Mode:      Folder (onedir, no extraction at runtime)
echo.
echo  Distribute the entire TurboWhisper folder.
echo  Users run "Launch TurboWhisper.bat" on first use
echo  to auto-install VC++ Runtime if needed.
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
