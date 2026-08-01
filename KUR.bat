@echo off
setlocal
chcp 65001 >nul
set "ROOT=C:\Users\yildi\Desktop\artmach_assistant"
if not exist "%ROOT%\core\own_code_intent.py" (
  echo HATA: Jarvis proje klasoru bulunamadi:
  echo %ROOT%
  pause
  exit /b 1
)
if not exist "%ROOT%\.jarvis_manual_backup" mkdir "%ROOT%\.jarvis_manual_backup"
copy /Y "%ROOT%\core\own_code_intent.py" "%ROOT%\.jarvis_manual_backup\own_code_intent.py.before_plan_routing_fix" >nul
copy /Y "%~dp0core\own_code_intent.py" "%ROOT%\core\own_code_intent.py" >nul
copy /Y "%~dp0tests\test_own_code_intent_plan_routing.py" "%ROOT%\tests\test_own_code_intent_plan_routing.py" >nul
cd /d "%ROOT%"
python -m py_compile core\own_code_intent.py
if errorlevel 1 goto rollback
python -m pytest -q tests\test_own_code_intent_plan_routing.py
if errorlevel 1 goto rollback
echo.
echo JARVIS OZ-GELISTIRME YONLENDIRME DUZELTMESI BASARILI.
echo Jarvis'i yeniden baslat.
pause
exit /b 0
:rollback
copy /Y "%ROOT%\.jarvis_manual_backup\own_code_intent.py.before_plan_routing_fix" "%ROOT%\core\own_code_intent.py" >nul
echo.
echo HATA: Test basarisiz. Eski dosya geri yuklendi.
pause
exit /b 1
