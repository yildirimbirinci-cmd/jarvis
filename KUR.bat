@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" install_two_regression_fix.py
) else (
  python install_two_regression_fix.py
)
if errorlevel 1 (
  echo.
  echo Kurulum basarisiz. Yedekler otomatik geri yuklendi.
  pause
  exit /b 1
)
echo.
echo Kurulum tamamlandi.
pause
