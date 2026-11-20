@echo off
REM Instalación en Windows: crea un entorno virtual, instala dependencias y Chromium para Playwright.
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% neq 0 (
  echo No se encontro Python. Instale Python 3.10 o superior desde https://www.python.org/downloads/ y marque "Add python.exe to PATH".
  exit /b 1
)
if not exist .venv (
  py -3 -m venv .venv || exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt || exit /b 1
python -m playwright install chromium || exit /b 1
echo.
echo Instalacion completada. Ejecute iniciar_windows.bat para abrir la aplicacion.
