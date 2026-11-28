@echo off
REM Cauce Forms: abre la ventana del programa. Doble clic en este archivo.
REM La primera vez instala lo necesario (entorno de Python y Chromium); tarda unos minutos.
setlocal
cd /d "%~dp0"
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.pw-browsers"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not exist .venv\Scripts\pythonw.exe (
  echo Primera ejecucion: instalando Cauce Forms...
  call instalar_windows.bat
  if errorlevel 1 (
    echo.
    echo La instalacion fallo. Revise los mensajes de arriba.
    pause
    exit /b 1
  )
)
if not exist "%PLAYWRIGHT_BROWSERS_PATH%" (
  .venv\Scripts\python.exe -m playwright install chromium
)
start "" ".venv\Scripts\pythonw.exe" -m cauce_form_connector.lanzador
exit /b 0
