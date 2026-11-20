# Contrato de integración futura con Cauce Monitor

Versión del contrato: **1** (borrador). Estado: **propuesta**, revisada contra el código de v0.1 (2026-11-20). Cauce Monitor no se ha modificado y esta
integración no está implementada en su lado. Lo que sí existe hoy en el conector está marcado como
«disponible»; lo que falta, como «pendiente».

## 1. Reparto de responsabilidades

| Responsabilidad | Cauce Monitor | Conector |
|---|---|---|
| Origen de los datos (filas) y su calidad | ✔ | |
| Autorización legal/contractual para automatizar un sitio | ✔ (registra quién autoriza) | Aplica la lista de dominios permitidos |
| Analizar el formulario y proponer correspondencias | | ✔ |
| Aprobación humana de correspondencias | ✔ (interfaz o flujo de aprobación) | Guarda el perfil aprobado |
| Escribir, verificar y detenerse antes de enviar | | ✔ |
| Decisión final de envío | Persona | Solo ejecuta con confirmación explícita |
| Custodia de datos personales | ✔ | No persiste valores; auditoría sin datos personales |

Regla invariable: **ningún flujo automático de Cauce Monitor puede enviar un formulario sin un paso humano.**

## 2. Modos de integración

### 2.1 Línea de comandos (disponible) — recomendado para empezar

Cauce Monitor lanza un subproceso y lee JSON de stdout. Es el acoplamiento más bajo: no comparte memoria
ni dependencias.

```
python -m cauce_form_connector analyze (--url <URL> [--authorized] | --html <archivo.html> [--static])  → AnalysisResult
python -m cauce_form_connector propose --url <URL> --data <archivo>         → Mapping[]
python -m cauce_form_connector fill    --url <URL> --data <archivo> --row N
        [--profile <nombre>] [--mapping mapping.json] [--screenshot out.png] [--headed]  → FillReport
```

Códigos de salida: `0` todo verificado · `2` terminó con incidencias (revisar `FillReport`) ·
`3` URL no autorizada · `1` error inesperado (mensaje en stderr).

Limitación: en `fill` por CLI el navegador se cierra al terminar, así que la persona no puede enviar
desde esa ventana. Para el paso de envío humano use el modo 2.2 (o `--headed` y un modo de espera, pendiente).

### 2.2 API HTTP local (disponible, sin versión en la ruta)

Servidor en `127.0.0.1:8765` (`python -m cauce_form_connector.web.app --no-open --port N`), una sola sesión de navegador.
Rechaza peticiones con `Host` u `Origin` no locales y con `Sec-Fetch-Site: cross-site|same-site`.

| Método y ruta | Entrada | Salida |
|---|---|---|
| `POST /api/analyze` | `{"mode": "url"\|"html"\|"lab", "url"?, "html"?, "authorized": bool, "engine": "browser"\|"static"}` | `{"analysis": AnalysisResult, "proposals": Mapping[]}` |
| `POST /api/data` | multipart `file` (.json/.csv/.xlsx) | `{"rows", "columns", "preview", "proposals"}` |
| `POST /api/mapping/propose` | — | `{"proposals": Mapping[]}` |
| `POST /api/fill` | `{"mapping": {field_key: columna\|null}, "row_index", "delay_ms"?, "reload_first"?}` | `{"report": FillReport}` |
| `POST /api/submit` | `{"phrase": "ENVIAR"}` | `{"submitted": true}` · 403 sin frase o con CAPTCHA |
| `GET /api/screenshot` | — | PNG de la página actual (en memoria, no se guarda en disco) |
| `GET /api/status` | — | `{"loaded", "rows", "headless", "typing_delay_ms", "allowed_hosts"}` |
| `GET /api/lab` | — | formularios de laboratorio disponibles |
| `POST /api/close` | — | cierra el navegador y descarta de memoria filas y propuestas |
| `GET/POST /api/profiles`, `GET /api/profiles/{nombre}` | perfil | ver §3.4 |
| `GET /api/audit?limit=N` | — | eventos de auditoría |

Pendiente para producción: prefijo `/api/v1`, token local compartido, varias sesiones simultáneas,
cola de trabajos (`POST /api/v1/jobs` → `job_id`, `GET /api/v1/jobs/{id}`).

### 2.3 Biblioteca Python (disponible)

```python
from cauce_form_connector.page_inspector import BrowserSession
from cauce_form_connector.matcher import match_fields
from cauce_form_connector.engine import fill_form

with BrowserSession(headless=False) as s:
    s.open_url(url_autorizada)
    analysis = s.inspect()
    mapping = {m.field_key: m.column for m in match_fields(analysis.all_fields, columnas)}
    report = fill_form(s, mapping, fila)           # se detiene antes de enviar
```

Requisito: todas las llamadas a una `BrowserSession` desde el mismo hilo (o vía `BrowserWorker`).

## 3. Esquemas de datos (JSON)

### 3.1 `FieldDescriptor`

```json
{
  "key": "alta::email_confirmacion", "selector": "#email_confirmacion", "tag": "input", "type": "email",
  "name": "email_confirmacion", "id": "email_confirmacion", "label": "Confirme su correo electrónico",
  "placeholder": "", "aria_label": "", "autocomplete": "off", "required": true, "disabled": false,
  "readonly": false, "visible": true, "pattern": "", "minlength": null, "maxlength": null,
  "min": "", "max": "", "step": "", "inputmode": "", "options": [],
  "form_key": "alta", "confirms": "alta::email", "blocks_paste": true
}
```

`key` = `<form_key>::<name|id>`. Es estable mientras el formulario mantenga `name`/`id`.
`options` = `[{"value", "label", "selected"}]` en select y radio.

### 3.2 `AnalysisResult`

`{"source": "url"|"html", "source_label": "http://host/ruta" | "html:<sha256-16>", "engine": "browser"|"static",
"forms": [{"key", "selector", "name", "id", "action", "method", "fields": FieldDescriptor[], "submit_buttons": [str]}],
"safety": {"blocked": bool, "reasons": [str]}, "warnings": [str]}`

Si `safety.blocked` es `true`, Cauce Monitor debe derivar el caso a una persona y no reintentar.

### 3.3 `Mapping`

`{"field_key", "column": str|null, "confidence": 0..1, "reason": str, "status": "auto"|"revisar"|"manual"|"sin_datos"|"ignorado"}`

Política recomendada: Cauce Monitor puede preaprobar `auto`; `revisar` exige aprobación humana.

### 3.4 Perfil guardado (`profiles/<nombre>.json`)

`{"version": 1, "name", "saved_at", "source", "source_label", "structure_signature", "fields": FieldDescriptor[],
"mapping": {field_key: columna|null}}`

`structure_signature` cambia si cambian claves o tipos de campos. Al cargar, `reconcile` devuelve
`same_structure`, `missing_fields` y `new_fields`: si `same_structure` es `false`, pedir nueva revisión.

### 3.5 `FillReport`

```json
{
  "ok": true, "stopped_before_submit": true, "blocked": false, "blocked_reasons": [],
  "results": [{"field_key", "label", "type", "method": "teclado_secuencial|select_option|check|uncheck|fill_nativo|omitido",
               "status": "ok|ok_normalizado|diferente|invalido|error|omitido", "expected", "actual",
               "keystrokes", "valid_html5", "validation_message", "page_error", "detail", "discovered_late"}],
  "confirmations": [{"original", "confirmacion", "label", "match": true, "detail"}],
  "submit_buttons": ["Dar de alta"], "duration_ms": 1365, "screenshot": "ruta.png"
}
```

**Atención:** `expected` y `actual` contienen datos de la fila. Cauce Monitor no debe registrarlos en logs
generales; si necesita trazabilidad, use el evento de auditoría (§4).

`ok` = todos los campos intentados en `ok`/`ok_normalizado`, todas las confirmaciones coinciden y no
quedan campos aparecidos sin correspondencia.

## 4. Auditoría

JSON Lines en `CAUCE_AUDIT_PATH`. Eventos: `analyze`, `url_rejected`, `data_loaded`, `mapping_proposed`,
`mapping_override`, `field_filled`, `fill_blocked`, `fill_done`, `human_submit`, `profile_saved`, `profile_loaded`.
Claves permitidas por lista blanca (`audit.ALLOWED_KEYS`); nunca valores. Cauce Monitor puede ingerir el
archivo y correlacionar por `run_id`.

## 5. Versionado y compatibilidad

- Cambios que añaden campos opcionales: mismo número de versión.
- Cambios que quitan o renombran campos: nueva versión de contrato y de `profile.version`.
- El conector se distribuirá como paquete (`pip install` desde el repositorio) con versión semántica.

## 6. Ejemplo reproducible (disponible)

`scripts/ejemplo_cauce_monitor.py` simula a Cauce Monitor con una fila ficticia y solo endpoints existentes:

1. arranca el conector como proceso aparte;
2. entrega la fila por `POST /api/data` (en memoria);
3. `POST /api/analyze` sobre `lab_forms/03_validaciones.html` y preaprueba solo correspondencias `auto`
   (si hay alguna `revisar`, se detiene con código 5);
4. `POST /api/fill` y recibe el `FillReport`;
5. guarda su registro **sin valores** (campo, método, estado, nº de caracteres, confirmaciones) con estado
   `pendiente_confirmacion_humana`;
6. solo si una persona escribe `ENVIAR` en la consola llama a `POST /api/submit`;
7. `POST /api/close` para descartar los datos.

```bash
python scripts/ejemplo_cauce_monitor.py                  # interactivo, con ventana
python scripts/ejemplo_cauce_monitor.py --no-interactivo # automático: verifica relleno y que NO hubo envío
```

Registro resultante: `evidence/ejemplo_monitor_registro.json`. Prueba: `tests/test_ejemplo_cauce_monitor.py`.

## 7. Pendiente para activar la integración

1. Decidir modo (CLI o HTTP) según cómo esté construido Cauce Monitor (no verificado: no se ha revisado su código).
2. Definir dónde vive la aprobación humana (interfaz del conector o de Cauce Monitor).
3. Añadir `/api/v1`, token local y cola de trabajos si se requieren varias ejecuciones.
4. Prueba de contrato conjunta con un formulario de laboratorio.
