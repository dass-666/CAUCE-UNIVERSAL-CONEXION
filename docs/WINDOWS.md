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

- **Listas hechas con JavaScript** (no `<select>`, con o sin atributos ARIA; si las opciones solo aparecen al abrir la lista, se abren con un clic normal para leerlas): se detectan sus opciones, se aplican con la tabla de equivalencias aprobada y se comprueba que el propio control reconozca la opción (opción marcada, valor interno y texto visible).
- **Fechas en texto**: el formato se detecta del campo (p. ej. «dd/mm/aaaa») y la fecha se convierte sola. Si es ambiguo (p. ej. solo `\d{2}/\d{2}/\d{4}`), la vista previa pide elegirlo; no se adivina. La conversión se aprueba con el perfil.
- **Datos que el expediente no tiene** (por ejemplo un identificador de cliente): en la vista previa elija «captura manual». Al pulsar «Preparar autorrellenado», la ventana le pide el dato. Se usa solo en esa ejecución y no se guarda ni se muestra en el registro.
- Ejemplo sintético: `lab_forms/07_listas_personalizadas.html` con `config/formularios/lab_listas_personalizadas.json`.
- **Identificadores desde una fuente local**: en la vista previa elija «fuente de datos local» e indique su nombre (por defecto `identificadores`). La fuente es un archivo que usted configura en `%LOCALAPPDATA%\CauceForms\fuentes\<nombre>.json` (formato en `sample_data/fuente_identificadores_sintetica.json`). Si no hay registro, el informe dice exactamente qué falta; nada se inventa.
- **Reutilización**: tras la primera aprobación, el perfil se guarda en `%LOCALAPPDATA%\CauceForms\formularios`. Las siguientes veces, «Preparar autorrellenado» con la misma URL lo usa sin preguntar nada. Si en Descargas hay un perfil revisado más reciente para esa URL, se usa y se aprueba ese. Si el formulario cambió, se detiene (ESTRUCTURA_CAMBIADA) y pide volver a analizar.

### Desplegables hechos con JavaScript (cualquier librería)

El conector no depende de cómo se llame la librería: actúa como una persona y observa. Abre la lista con un clic normal,
toma como opciones lo que aparece en pantalla en ese momento, elige con un clic (si la lista tiene buscador, escribe el
texto de la opción) y comprueba que el formulario registró el valor: en el `<select>` real escondido, en el campo oculto,
en la opción marcada o en el texto que muestra la lista. Reconoce cuatro formas: `<select>` real sustituido por un
componente, controles con ARIA (`combobox`), campo oculto dentro de un contenedor de lista y elementos con clase de lista.
Los identificadores que cambian en cada carga no se usan como clave del campo.

Comprobado en local con las librerías reales Select2, Chosen, Tom Select, Choices, SlimSelect, MUI (Select y
Autocomplete), Ant Design y Element Plus. **No cubierto todavía:** controles dentro de un `iframe` o de Shadow DOM
(el diagnóstico los cuenta para saber si es el caso).

### Diagnóstico de controles

Cada «Analizar formulario» deja en la carpeta del análisis `diagnostico_controles.json` y `diagnostico_controles.md`.
Contienen solo la estructura de la página (etiquetas HTML, clases, roles y los rótulos de las opciones), qué controles se
reconocieron, cuáles parecen controles y no se reconocieron y qué apareció al hacer clic en cada lista. No incluyen
valores escritos, cookies ni parámetros de la URL. Si un desplegable de un sitio real no se reconoce, ese archivo es la
evidencia necesaria para corregirlo sin adivinar.

### Fechas

El formato se toma de lo que declara el propio campo: texto de ayuda, atributos de las librerías de calendario
(`data-date-format`, `data-format`, máscaras), `title`, el texto de ayuda asociado o un ejemplo inequívoco (`31/12/2024`).
Un ejemplo como `05/06/2024` o una máscara `99/99/9999` no deciden nada: el campo queda en «revisar» y el formato se elige
en la vista previa. Las fechas de solo lectura que se eligen en un calendario quedan para la persona.

### Volver a analizar sin repetir la configuración

Si ya existe un perfil revisado para la misma URL, «Analizar formulario» lo usa como punto de partida: la vista previa
aparece con las mismas rutas, formatos de fecha y tablas de equivalencias. Una equivalencia cuya opción ya no existe en el
formulario queda sin fila (no se sustituye por otra). En consola: `analizar-formulario --perfil-previo <perfil.json>`.

### Cuadros de texto que abren una lista (autocompletar de jQuery y similares)

Para la página son simples `input type="text"`, así que el detector no puede distinguirlos mirando el HTML. Durante
«Analizar formulario» (y al aprobar el perfil) el conector los comprueba uno a uno con interacción ordinaria: un clic y,
si no aparece nada, la tecla flecha abajo; después Escape. No escribe nada. Según lo que ocurra:

- **Aparecen opciones** → el campo pasa a ser una lista con esas opciones (misma clave de campo).
- **No aparece nada pero hay indicios de autocompletar** (componente de jQuery asociado al campo, `aria-autocomplete`,
  clase de lista) → lista que «solo responde al escribir». Si el componente tiene sus opciones en la propia página se
  leen de ahí; si las pide al servidor, en la tabla de equivalencias se anota el **texto exacto** de cada opción.
- **Abre un calendario** o parece una fecha → se trata como fecha.
- **Sugiere valores pero es un dato libre** (nombre, correo, teléfono…) sin valor interno asociado → sigue siendo texto.
- **Nada de lo anterior** → campo de texto normal.

Al rellenar, el conector abre la lista o va tecleando el texto y pulsa la opción en cuanto la lista la ofrece. Solo vale
una opción idéntica (sin distinguir mayúsculas ni acentos); nunca la más parecida. Después comprueba lo que registró el
formulario: el texto del cuadro y, si existe, el campo oculto que guarda el código (si queda vacío, es una incidencia
aunque el texto se vea bien). El perfil aprobado recuerda qué cuadros son listas, así que al rellenar no se vuelve a
sondear y los perfiles anteriores siguen funcionando igual.

Comprobado en local con componentes reales: jQuery UI Autocomplete (al enfocar, al escribir, con campo oculto y el
«combobox» de su demo), EasyAutocomplete, typeahead.js, jquery-editable-select, devbridge Autocomplete y Materialize
(select y autocomplete).

### Diagnóstico: por qué cada control se reconoció o se descartó

`diagnostico_controles.md` incluye la sección «Por qué cada control se reconoció o se descartó»: para cada campo, el tipo
detectado, el tipo final, el motivo, qué pasó en cada paso del sondeo, los indicios encontrados y los eventos de jQuery
asociados. `diagnostico_controles_anonimo.json` es la misma información sin URL, dominios, título, rótulos ni textos de
opciones: es el archivo pensado para compartir. Conserva nombres de clases e identificadores HTML, que son técnicos;
conviene repasarlos antes de enviarlo.

### Fechas con calendario

Además de las pistas del propio campo, se lee el formato configurado en el calendario (jQuery UI Datepicker,
bootstrap-datepicker, flatpickr, Materialize). Si al pulsar el campo se abre un selector que se queda con el foco y no
deja escribir, el conector lo cierra con Escape, marca el campo como incidencia y sigue con los demás.
