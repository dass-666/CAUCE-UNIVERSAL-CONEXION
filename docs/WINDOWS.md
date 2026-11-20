# Ejecutar en Windows

> Estado de verificación: estos pasos **no se han ejecutado todavía en un equipo Windows**.
> El código se probó en Linux con Chromium. El flujo de GitHub Actions
> (`.github/workflows/tests.yml`) incluye `windows-latest` para verificarlo en cuanto el
> repositorio tenga el código. Si algo falla, el mensaje de error de cada paso indica qué revisar.

## Requisitos

- Windows 10 u 11.
- Python 3.10 o superior desde <https://www.python.org/downloads/>. En el instalador marque
  **«Add python.exe to PATH»**. Compruébelo en una terminal con `py --version`.
- Unos 500 MB libres (Chromium de Playwright ocupa unos 150–300 MB).
- Conexión a Internet solo durante la instalación.

## Instalación (una vez)

1. Descargue o clone el repositorio, por ejemplo en `C:\Proyectos\cauce-universal-conexion`.
2. Haga doble clic en **`instalar_windows.bat`** (o ejecútelo desde `cmd`). Esto:
   - crea el entorno virtual `.venv`,
   - instala las dependencias de `requirements-dev.txt`,
   - descarga Chromium para Playwright (`python -m playwright install chromium`).

Equivalente manual en PowerShell:

```powershell
cd C:\Proyectos\cauce-universal-conexion
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1      # si PowerShell lo bloquea: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
```

## Uso diario

Doble clic en **`iniciar_windows.bat`**. Se abre `http://127.0.0.1:8765` en su navegador
habitual. Al pulsar «Analizar» o «Rellenar», se abre una **ventana de Chromium aparte** donde
verá escribir al conector. No toque esa ventana mientras escribe. Al terminar, revise y envíe
usted mismo, o escriba `ENVIAR` en el paso 5 de la interfaz.

Para cerrar: `Ctrl + C` en la ventana negra de la terminal.

## Pruebas y demostración

Doble clic en **`probar_windows.bat`**: ejecuta `pytest` y `scripts\demo_lab.py`.
El informe queda en `evidence\out\informe_demo.md` con capturas PNG.

Para ver la demo escribiendo en pantalla:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\demo_lab.py --headed
```

## Variables de entorno opcionales

| Variable | Valor por defecto | Para qué |
|---|---|---|
| `CAUCE_HEADLESS` | `0` | `1` = sin ventana de navegador |
| `CAUCE_TYPING_DELAY_MS` | `35` | pausa fija entre teclas |
| `CAUCE_BROWSER_PATH` | (vacío) | usar un Chromium concreto |
| `CAUCE_ALLOWED_HOSTS_FILE` | `config\allowed_hosts.json` | dominios externos autorizados |
| `CAUCE_AUDIT_PATH` | `logs\audit.jsonl` | registro de auditoría |
| `CAUCE_PROFILES_DIR` | `profiles` | configuraciones guardadas |

Ejemplo en `cmd`: `set CAUCE_TYPING_DELAY_MS=80` antes de `iniciar_windows.bat`.

## Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `Executable doesn't exist ... chromium` | No se descargó Chromium | `python -m playwright install chromium` |
| `py no se reconoce` | Python no está en el PATH | Reinstale Python marcando «Add to PATH» |
| El puerto 8765 está ocupado | Otra aplicación lo usa | `python -m cauce_form_connector.web.app --port 8800` |
| Antivirus bloquea Chromium | Política corporativa | Pida a TI permitir `%USERPROFILE%\AppData\Local\ms-playwright` |
| Teclado en español y caracteres raros | No debería ocurrir: los acentos y la ñ se envían como eventos de tecla Unicode, independientes de la distribución del teclado de Windows | Si ocurre, indíquelo con la captura del informe |
