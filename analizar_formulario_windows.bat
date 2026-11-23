@echo off
REM Analiza (SOLO LECTURA) el formulario de una URL autorizada y abre la vista previa del perfil borrador.
REM No rellena, no envia, no inicia sesion ni resuelve CAPTCHA. Resultados en informes\analisis_...
setlocal
cd /d "%~dp0"
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.pw-browsers"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not exist .venv (
  echo Primero ejecute instalar_windows.bat
  exit /b 1
)
call .venv\Scripts\activate.bat
set "URL=%~1"
if "%URL%"=="" set /p "URL=URL autorizada del formulario: "
if "%URL%"=="" exit /b 1
set "EXPEDIENTE=%~2"
if "%EXPEDIENTE%"=="" set "EXPEDIENTE=sample_data\expediente_ficticio.json"
set "AUT="
echo.
echo Los dominios externos deben estar en config\allowed_hosts.json.
set /p "RESP=Tiene autorizacion por escrito para analizar esta pagina? (S/N): "
if /i "%RESP%"=="S" set "AUT=--authorized"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
set "SALIDA=informes\analisis_%TS%"
python -m cauce_form_connector analizar-formulario --url "%URL%" %AUT% --expediente "%EXPEDIENTE%" --salida "%SALIDA%"
set "RC=%ERRORLEVEL%"
if "%RC%"=="3" (
  echo URL no autorizada: no se abrio la pagina.
  exit /b 3
)
if exist "%SALIDA%\vista_previa.html" start "" "%SALIDA%\vista_previa.html"
exit /b %RC%
