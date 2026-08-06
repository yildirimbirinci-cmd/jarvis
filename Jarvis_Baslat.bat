@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Artmach Assistant - Jarvis

set "JARVIS_DIR=%~dp0"
set "ENTRY=%JARVIS_DIR%start_jarvis.py"
set "LOG=%JARVIS_DIR%jarvis_start_error.txt"
set "PYTHON_EXE="
set "PYTHON_ARGS="

if not exist "%ENTRY%" (
    color 4F
    echo.
    echo ERROR: start_jarvis.py was not found.
    echo Expected path: "%ENTRY%"
    echo.
    pause
    exit /b 1
)

if exist "%JARVIS_DIR%.venv\Scripts\python.exe" set "PYTHON_EXE=%JARVIS_DIR%.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%JARVIS_DIR%venv\Scripts\python.exe" set "PYTHON_EXE=%JARVIS_DIR%venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%JARVIS_DIR%..\.venv\Scripts\python.exe" set "PYTHON_EXE=%JARVIS_DIR%..\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%JARVIS_DIR%..\venv\Scripts\python.exe" set "PYTHON_EXE=%JARVIS_DIR%..\venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"

if not defined PYTHON_EXE (
    py -3.11 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%P in ('where py') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
        set "PYTHON_ARGS=-3.11"
    )
)

if not defined PYTHON_EXE (
    python -c "import sys; assert sys.version_info[:2] == (3, 11)" >nul 2>nul
    if not errorlevel 1 for /f "delims=" %%P in ('where python') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)

if not defined PYTHON_EXE (
    color 4F
    echo.
    echo ERROR: Python 3.11 was not found.
    echo.
    pause
    exit /b 1
)

pushd "%JARVIS_DIR%" || exit /b 1
"%PYTHON_EXE%" %PYTHON_ARGS% "%ENTRY%"
set "JARVIS_EXIT=%ERRORLEVEL%"
popd

if not "%JARVIS_EXIT%"=="0" (
    color 4F
    echo.
    echo Jarvis could not start. Error log:
    echo "%LOG%"
    echo.
    if exist "%LOG%" type "%LOG%"
    echo.
    pause
)

exit /b %JARVIS_EXIT%
