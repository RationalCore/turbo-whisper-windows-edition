@echo off
chcp 65001 >nul
title TurboWhisper Build + Compress
setlocal enabledelayedexpansion

set "DIST_DIR=%~dp0dist"
set "PROJECT_DIR=%~dp0"
set "BUILD_SPEC=%~dp0build_exe.spec"
set "PYTHON=C:\Program Files\Python313\python.exe"

echo ============================================
echo  TurboWhisper - Building Executable
echo ============================================
echo.

:: 1. Clean previous build artifacts
echo [1/4] Cleaning previous build artifacts...
if exist "%DIST_DIR%\TurboWhisper.exe" del /f /q "%DIST_DIR%\TurboWhisper.exe" >nul 2>&1
if exist "%DIST_DIR%\TurboWhisper_compressed.exe" del /f /q "%DIST_DIR%\TurboWhisper_compressed.exe" >nul 2>&1
if exist "%~dp0build" rmdir /s /q "%~dp0build" >nul 2>&1
echo    Done.
echo.

:: 2. Build with PyInstaller
echo [2/4] Building TurboWhisper.exe with PyInstaller...
"%PYTHON%" -m PyInstaller --clean "%BUILD_SPEC%" 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] PyInstaller build FAILED! Error code: %ERRORLEVEL%
    goto :error
)
echo    Done.
echo.

:: 3. Package into ZIP
echo [3/4] Creating distribution archive (TurboWhisper.zip)...
set "EXE_FILE=%DIST_DIR%\TurboWhisper.exe"
set "ZIP_FILE=%DIST_DIR%\TurboWhisper.zip"

if not exist "!EXE_FILE!" (
    echo [!] TurboWhisper.exe not found at !EXE_FILE!
    goto :error
)

:: Get size
for %%I in ("!EXE_FILE!") do set "FILE_SIZE=%%~zI"
set /a "FILE_MB=!FILE_SIZE! / 1048576"
echo    Executable size: !FILE_MB! MB

:: Create ZIP using PowerShell
powershell -NoProfile -Command ^
    "$zipPath = '%DIST_DIR:\=\\%\\TurboWhisper.zip';" ^
    "if (Test-Path $zipPath) { Remove-Item $zipPath -Force };" ^
    "Add-Type -Assembly 'System.IO.Compression.FileSystem';" ^
    "$archive = [System.IO.Compression.ZipFile]::Open($zipPath, 'Create');" ^
    "$exePath = '%DIST_DIR:\=\\%\\TurboWhisper.exe';" ^
    "$entryName = 'TurboWhisper.exe';" ^
    "[System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $exePath, $entryName, 'Optimal') | Out-Null;" ^
    "$archive.Dispose();" ^
    "$size = (Get-Item $zipPath).Length / 1MB;" ^
    "Write-Host ('    Archive created: ' + [math]::Round($size, 2) + ' MB')"
if %ERRORLEVEL% NEQ 0 (
    echo [!] ZIP packaging failed, but executable is ready.
    echo    Executable: !EXE_FILE!
) else (
    echo    Done.
)
echo.

:: Done
echo ============================================
echo  Build completed successfully!
echo ============================================
echo.
echo  Output files:
echo    - !EXE_FILE! (!FILE_MB! MB)
echo    - %ZIP_FILE%
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