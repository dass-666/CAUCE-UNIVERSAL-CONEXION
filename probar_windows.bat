@echo off
REM Ejecuta las pruebas automatizadas y la demostracion de laboratorio.
setlocal
cd /d "%~dp0"
REM Navegadores de Playwright dentro del proyecto: evita fallos de Chromium con ventana
REM cuando la carpeta del perfil de usuario tiene espacios (p. ej. "C:\Users\Nombre Apellido").
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.pw-browsers"
REM Consola en UTF-8 para que los acentos se vean bien
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
call .venv\Scripts\activate.bat
set CAUCE_EVIDENCE_DIR=evidence\out
python -m pytest -q || exit /b 1
python scripts\demo_lab.py
