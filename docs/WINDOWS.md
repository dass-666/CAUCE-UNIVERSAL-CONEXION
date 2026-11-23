# Ejecutar en Windows

> Estado de verificación (2026-11-20, commit `0c76f54`): en GitHub Actions con `windows-latest`
> (Python 3.12, Chromium sin ventana) pasan las 70 pruebas, la demo (`DEMO SUPERADA`), el ejemplo
> de Cauce Monitor (`EJEMPLO SUPERADO`) y el recorrido de la interfaz (`RECORRIDO SUPERADO`).
> Prueba física (2026-11-20, Windows 11 Pro 23H2, Python 3.14): instalación, 70 pruebas, demo,
> ejemplo de Cauce Monitor y recorrido visible de la interfaz superados. Se encontró y corrigió un fallo:
> Chromium con ventana no arrancaba desde la carpeta por defecto de Playwright en un perfil de usuario
> con espacios (ver «Problemas frecuentes»).
> Si algo falla, el mensaje de error de cada paso indica qué revisar.

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
   - descarga Chromium para Playwright (`python -m playwright install chromium`) dentro del proyecto,
     en `.pw-browsers` (los tres `.bat` usan esa carpeta mediante `PLAYWRIGHT_BROWSERS_PATH`).

Equivalente manual en PowerShell:

```powershell
cd C:\Proyectos\cauce-universal-conexion
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.pw-browsers"   # igual que los .bat
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

## Analizar un formulario externo

Doble clic en **`analizar_formulario_windows.bat`** (o `analizar_formulario_windows.bat https://sitio/reserva`).
Pide la URL, pregunta si tiene autorización y abre `vista_previa.html` en su navegador. Solo lectura: no rellena ni envía.
El dominio debe estar antes en `config\allowed_hosts.json`. Resultados en `informes\analisis_<fecha>\`.

`instalar_windows.bat` usa el lanzador `py` si existe y, si no, `python` (3.10 o superior). Esto se prueba en
GitHub Actions en un Windows sin `py`.

## Recorrido Monitor → Forms (datos sintéticos)

Doble clic en **`recorrido_monitor_windows.bat`**: sirve los formularios de laboratorio, aprueba el perfil del lab 06
(con tablas de equivalencias) y abre una ventana rellenada y verificada. No envía nada; ciérrela al terminar.

## Pruebas y demostración

Doble clic en **`probar_windows.bat`**: ejecuta `pytest` y `scripts\demo_lab.py`.
El informe queda en `evidence\out\informe_demo.md` con capturas PNG.

Para ver la demo escribiendo en pantalla:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\demo_lab.py --headed
```

Recorrido completo de la interfaz (abre, analiza, importa Excel, revisa, rellena, comprueba
confirmaciones y campos dinámicos, y verifica que no se envía), con capturas en `evidence\out\recorrido`:

```powershell
python scripts\recorrido_ui.py
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
| `Executable doesn't exist ... chromium` | No se descargó Chromium | Ejecute de nuevo `instalar_windows.bat` |
| `BrowserType.launch: spawn UNKNOWN` al abrir el navegador con ventana | Windows no puede cargar Chromium desde `%LOCALAPPDATA%\ms-playwright` (visto con un perfil de usuario con espacios; error de configuración en paralelo/SxS) | Use los `.bat` (instalan en `.pw-browsers` del proyecto) y coloque el proyecto en una ruta sin espacios, p. ej. `D:\cauce-ucf`. Alternativa: `set CAUCE_BROWSER_PATH=ruta\a\chrome.exe` |
| Acentos mal en la consola (`carÃ¡cter`) | Página de códigos de la consola | Los `.bat` ya ponen `chcp 65001`; en otra consola ejecute `chcp 65001` |
| `py no se reconoce` | Python no está en el PATH | Reinstale Python marcando «Add to PATH» |
| El puerto 8765 está ocupado | Otra aplicación lo usa | `python -m cauce_form_connector.web.app --port 8800` |
| Antivirus bloquea Chromium | Política corporativa | Pida a TI permitir `%USERPROFILE%\AppData\Local\ms-playwright` |
| Teclado en español y caracteres raros | No debería ocurrir: los acentos y la ñ se envían como eventos de tecla Unicode, independientes de la distribución del teclado de Windows | Si ocurre, indíquelo con la captura del informe |

## Programa con ventana (doble clic)

Abra `abrir_cauce_forms.bat` con doble clic. La primera vez instala lo necesario. Escriba la URL y use «Analizar formulario», «Revisar perfil» y «Preparar autorrellenado»; no pide referencias aut_ ni rev_.

- Formularios locales (localhost, 127.0.0.1, archivos .html): sin pasos previos.
- Sitios externos: la primera vez que use un dominio, la ventana pregunta una sola vez si añadirlo a su lista local de dominios permitidos. Se siguen deteniendo ante CAPTCHA o inicio de sesión.
- El perfil se revisa en la vista previa (campos y tablas de equivalencias) y se aprueba al pulsar «Preparar autorrellenado». Cada campo se verifica tras rellenarlo y el conector nunca envía el formulario.
- Resultados, perfiles y lista de dominios en `%LOCALAPPDATA%\CauceForms` (solo su usuario, fuera de esta carpeta).

### Campos que antes quedaban para la persona

- **Listas hechas con JavaScript** (no `<select>`): se detectan sus opciones, se aplican con la tabla de equivalencias aprobada y se comprueba que el propio control reconozca la opción (opción marcada, valor interno y texto visible).
- **Fechas en texto**: en la vista previa elija el formato (AAAA-MM-DD, DD/MM/AAAA, etc.). La conversión se aprueba con el perfil.
- **Datos que el expediente no tiene** (por ejemplo un identificador de cliente): en la vista previa elija «captura manual». Al pulsar «Preparar autorrellenado», la ventana le pide el dato. Se usa solo en esa ejecución y no se guarda ni se muestra en el registro.
- Ejemplo sintético: `lab_forms/07_listas_personalizadas.html` con `config/formularios/lab_listas_personalizadas.json`.
