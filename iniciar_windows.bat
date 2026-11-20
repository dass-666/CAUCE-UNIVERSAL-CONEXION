@echo off
REM Abre la interfaz local en http://127.0.0.1:8765 (solo accesible desde este equipo).
setlocal
cd /d "%~dp0"
REM Navegadores de Playwright dentro del proyecto: evita fallos de Chromium con ventana
REM cuando la carpeta del perfil de usuario tiene espacios (p. ej. "C:\Users\Nombre Apellido").
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.pw-browsers"
REM Consola en UTF-8 para que los acentos se vean bien
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not exist .venv (
  echo Primero ejecute instalar_windows.bat
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m cauce_form_connector ui
