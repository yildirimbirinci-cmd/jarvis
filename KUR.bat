@echo off
setlocal
chcp 65001 >nul
title Jarvis Plan Onay Oncelik Fix

set "ROOT=%USERPROFILE%\Desktop\artmach_assistant"

if not exist "%ROOT%\core\assistant.py" (
    echo HATA: Jarvis proje klasoru bulunamadi:
    echo %ROOT%
    pause
    exit /b 1
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3.11 "%~dp0apply_fix.py" --root "%ROOT%"
) else (
    python "%~dp0apply_fix.py" --root "%ROOT%"
)

if errorlevel 1 (
    echo.
    echo KURULUM BASARISIZ.
    pause
    exit /b 1
)

echo.
echo Kurulum tamamlandi.
pause
