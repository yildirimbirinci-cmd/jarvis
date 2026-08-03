@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" apply_lifecycle_stabilization.py
) else (
  python apply_lifecycle_stabilization.py
)
if errorlevel 1 (
  echo.
  echo Duzeltme uygulanamadi.
  pause
  exit /b 1
)
echo.
echo Duzeltme tamamlandi.
pause
