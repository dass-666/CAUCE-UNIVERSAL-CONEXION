@echo off
REM Ejecuta las pruebas automatizadas y la demostracion de laboratorio.
setlocal
cd /d "%~dp0"
call .venv\Scripts\activate.bat
set CAUCE_EVIDENCE_DIR=evidence\out
python -m pytest -q || exit /b 1
python scripts\demo_lab.py
