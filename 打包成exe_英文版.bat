@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
REM Warehouse System - Windows one-click EXE builder (ASCII version)
REM Double-click to run. Output: dist\WarehouseSystem.exe

echo.
echo   ============================================
echo    Warehouse System  -  EXE Builder
echo   ============================================
echo.

set "PYCMD="
where py >nul 2>nul && set "PYCMD=py"
if not defined PYCMD (where python >nul 2>nul && set "PYCMD=python")
if not defined PYCMD (where python3 >nul 2>nul && set "PYCMD=python3")

if not defined PYCMD (
    echo   [x] Python not found
    echo.
    echo   Install from https://www.python.org/downloads/
    echo   Check "Add Python to PATH" during install
    echo.
    pause
    exit /b 1
)

echo   [1/5] Interpreter: %PYCMD%
%PYCMD% --version
if errorlevel 1 (
    echo   [x] Interpreter cannot run
    pause
    exit /b 1
)

cd /d "%~dp0"
echo   Current dir: %CD%
echo.

echo   [2/5] Installing dependencies...
%PYCMD% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo.
    echo   [x] Dependency install failed. Check network.
    pause
    exit /b 1
)

echo.
echo   [3/6] Installing window deps (Qt WebEngine, ~400MB, optional)...
%PYCMD% -m pip install -r requirements-desktop.txt
if errorlevel 1 (
    echo   [!] Qt not installed. Build continues, app falls back to browser.
    echo      For a standalone window run: %PYCMD% -m pip install PySide6
)

echo.
echo   [4/6] Cleaning old output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo   [5/6] Building, please wait 1-5 minutes...
%PYCMD% -m PyInstaller warehouse.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo   [x] Build failed, scroll up for errors
    pause
    exit /b 1
)

echo.
echo   [6/6] Checking output...
if not exist "dist\warehouse\warehouse.exe" (
    if not exist "dist\warehouse\WarehouseSystem.exe" (
        echo   [x] exe not found in dist
        dir /b dist 2>nul
        pause
        exit /b 1
    )
)

echo.
echo   ============================================
echo    Build finished
echo    Output: dist folder
echo    Data file warehouse.db sits next to the exe
echo   ============================================
echo.
if exist dist explorer dist
pause