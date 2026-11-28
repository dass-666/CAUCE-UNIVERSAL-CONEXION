@echo off
REM Instalación en Windows: crea un entorno virtual, instala dependencias y Chromium para Playwright.
setlocal
cd /d "%~dp0"
REM Navegadores de Playwright dentro del proyecto: evita fallos de Chromium con ventana
REM cuando la carpeta del perfil de usuario tiene espacios (p. ej. "C:\Users\Nombre Apellido").
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.pw-browsers"
REM Consola en UTF-8 para que los acentos se vean bien
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
REM Busca Python 3.10+: primero el lanzador "py"; si no existe (instalaciones sin lanzador o desde
REM Microsoft Store), "python". El acceso directo falso de la Store no ejecuta el codigo y se descarta.
set "PYEXE="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo No se encontro Python 3.10 o superior ^(ni "py" ni "python"^).
  echo Instale Python desde https://www.python.org/downloads/ y marque "Add python.exe to PATH".
  exit /b 1
)
echo Usando Python: %PYEXE%
if not exist .venv\Scripts\python.exe (
  %PYEXE% -m venv .venv || exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt || exit /b 1
python -m playwright install chromium || exit /b 1
echo.
echo Instalacion completada. Ejecute iniciar_windows.bat para abrir la aplicacion.
