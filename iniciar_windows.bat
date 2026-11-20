@echo off
REM Abre la interfaz local en http://127.0.0.1:8765 (solo accesible desde este equipo).
setlocal
cd /d "%~dp0"
if not exist .venv (
  echo Primero ejecute instalar_windows.bat
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m cauce_form_connector ui
