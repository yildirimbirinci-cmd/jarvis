@echo off
setlocal
chcp 65001 >nul
title Jarvis Oz-Gelistirme Runtime Final Fix

set "ROOT=%USERPROFILE%\Desktop\artmach_assistant"
if not exist "%ROOT%\core\own_code_intent.py" (
    echo HATA: Proje bulunamadi:
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
    echo KURULUM BASARISIZ. Dosyalar otomatik geri alindi.
    pause
    exit /b 1
)

echo.
echo Kurulum tamamlandi. Jarvis'i yeniden baslat.
pause
