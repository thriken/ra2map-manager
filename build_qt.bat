@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title RA2 Map Manager - Qt build

echo ============================================================
echo   RA2 Map Manager - Qt5/Qt6 build  (Windows 7 and later)
echo   Output: dist\RA2MapManager.exe
echo ============================================================
echo.

set "PY="

REM 1) honor PYQTPY env var (path to a Python install dir)
if defined PYQTPY (
    if exist "%PYQTPY%\python.exe" set "PY=%PYQTPY%\python.exe"
)

REM 2) try py launcher (newest 3.x)
if not defined PY (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=py -3"
    )
)

REM 3) try plain python
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=python"
    )
)

if not defined PY (
    echo [ERROR] Python 3.8+ not found.
    echo   Install 64-bit Python from https://www.python.org/downloads/
    echo   or set the PYQTPY environment variable to the Python install
    echo   directory and rerun.
    pause
    exit /b 1
)

echo Using interpreter: %PY%
echo.

echo [1/4] Upgrading pip ...
%PY% -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo [2/4] Installing PyInstaller ...
%PY% -m pip install pyinstaller
if errorlevel 1 goto :fail

echo [3/4] Checking Qt binding (PyQt5 preferred, PyQt6 fallback) ...
set "QT_EXCLUDE="
%PY% -c "import PyQt5.QtCore" >nul 2>nul
if errorlevel 1 (
    echo   PyQt5 not usable, trying to install it ...
    %PY% -m pip install "PyQt5>=5.15" >nul 2>nul
    %PY% -c "import PyQt5.QtCore" >nul 2>nul
    if errorlevel 1 (
        echo   PyQt5 unavailable on this Python, installing PyQt6 ...
        %PY% -m pip install "PyQt6>=6.4"
        if errorlevel 1 goto :fail
        set "QT_EXCLUDE=--exclude PyQt5"
    ) else (
        set "QT_EXCLUDE=--exclude PyQt6"
    )
) else (
    set "QT_EXCLUDE=--exclude PyQt6"
)

echo [4/4] Building EXE ...
REM --noupx: 避免 UPX 压缩运行库 DLL 引发的随机崩溃
REM 排除未使用的 Qt 绑定：PyInstaller 不允许同时收集 PyQt5+PyQt6
REM 若杀毒软件对单文件版误报，可改用目录版：
REM   %PY% -m PyInstaller --onedir --windowed --clean --noupx --name RA2MapManager MapManagerGUI.py
%PY% -m PyInstaller --onefile --windowed --clean --noupx --name RA2MapManager %QT_EXCLUDE% MapManagerGUI.py
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo   SUCCESS: dist\RA2MapManager.exe
echo   Copy it to any Win7+ machine and run.
echo ============================================================
pause
exit /b 0

:fail
echo.
echo [ERROR] build failed.
pause
exit /b 1
