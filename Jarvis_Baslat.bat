@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Artmach Assistant - Jarvis

rem Bu dosyayi artmach_assistant klasorune koyup cift tiklayin.
set "JARVIS_DIR=%~dp0"
set "ENTRY=%JARVIS_DIR%start_jarvis.py"
set "LOG=%JARVIS_DIR%jarvis_start_error.txt"
set "PYTHON_EXE="
set "PYTHON_ARGS="

if not exist "%ENTRY%" (
    color 4F
    echo.
    echo HATA: start_jarvis.py bulunamadi.
    echo.
    echo Jarvis_Baslat.bat dosyasini start_jarvis.py ile ayni
    echo artmach_assistant klasorune koyup tekrar cift tiklayin.
    echo.
    echo Aranan konum: "%ENTRY%"
    echo.
    pause
    exit /b 1
)

rem Once proje sanal ortamlarini dene.
if exist "%JARVIS_DIR%.venv\Scripts\python.exe" set "PYTHON_EXE=%JARVIS_DIR%.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%JARVIS_DIR%venv\Scripts\python.exe" set "PYTHON_EXE=%JARVIS_DIR%venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%JARVIS_DIR%..\.venv\Scripts\python.exe" set "PYTHON_EXE=%JARVIS_DIR%..\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%JARVIS_DIR%..\venv\Scripts\python.exe" set "PYTHON_EXE=%JARVIS_DIR%..\venv\Scripts\python.exe"

rem Ardindan bilinen Python 3.11 kurulumunu ve Windows Python Launcher'i dene.
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
    echo HATA: Python 3.11 bulunamadi.
    echo Python 3.11'i kurduktan sonra bu dosyayi yeniden cift tiklayin.
    echo.
    pause
    exit /b 1
)

rem Ollama kuruluysa ve calismiyorsa arka planda baslatmayi dene.
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /I "ollama.exe" >nul
if errorlevel 1 (
    where ollama >nul 2>nul
    if not errorlevel 1 (
        echo Ollama baslatiliyor...
        start "Ollama" /min ollama serve
        timeout /t 3 /nobreak >nul
    )
)

del /q "%LOG%" >nul 2>nul
echo Jarvis baslatiliyor...
echo Python: %PYTHON_EXE% %PYTHON_ARGS%
echo.

pushd "%JARVIS_DIR%"
call "%PYTHON_EXE%" %PYTHON_ARGS% "%ENTRY%" >"%LOG%" 2>&1
set "JARVIS_EXIT=%ERRORLEVEL%"
popd

if not "%JARVIS_EXIT%"=="0" (
    color 4F
    echo.
    echo Jarvis acilamadi. Hata kaydi:
    echo "%LOG%"
    echo.
    type "%LOG%"
    echo.
    pause
    exit /b %JARVIS_EXIT%
)

exit /b 0
