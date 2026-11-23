@echo off
REM Recorrido Monitor -> Forms con datos SINTETICOS (no envia nada). Sirve lab_forms en 127.0.0.1, aprueba el
REM perfil (paso humano simulado con rev_demo) y abre una ventana rellenada y verificada para revisarla.
REM   recorrido_monitor_windows.bat                     (perfil del laboratorio 06, con tablas de equivalencias)
REM   recorrido_monitor_windows.bat config\formularios\lab_validaciones.json
setlocal
cd /d "%~dp0"
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.pw-browsers"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not exist .venv\Scripts\python.exe (
  echo Primero ejecute instalar_windows.bat
  exit /b 1
)
call .venv\Scripts\activate.bat
set "FORMULARIO=%~1"
if "%FORMULARIO%"=="" set "FORMULARIO=config\formularios\lab_cita_monitor.json"
set "ESPERA=%CAUCE_ESPERA_S%"
if "%ESPERA%"=="" set "ESPERA=120"
python scripts\recorrido_monitor.py --formulario "%FORMULARIO%" --espera-s %ESPERA%
exit /b %ERRORLEVEL%
