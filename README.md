# Cauce Universal Form Connector

Conector local, modular y reutilizable que:

1. Analiza formularios HTML (código pegado, archivo `.html` o URL autorizada), incluidos los creados con JavaScript.
2. Detecta campos, etiquetas, tipos, validaciones, opciones, obligatorios y pares de confirmación (correo/teléfono).
3. Propone correspondencias entre campos y columnas de datos (JSON, CSV o Excel) para revisión humana.
4. Rellena los campos de texto **carácter por carácter con eventos de teclado reales** (sin pegar ni asignar `.value`).
5. Verifica lo que la página registró realmente, incluidas confirmaciones y validaciones de la propia página.
6. **Se detiene antes del envío.** Solo envía si una persona escribe `ENVIAR` o pulsa el botón ella misma.
7. Respeta los CAPTCHA y las páginas que piden intervención humana: las detecta y no rellena.

Proyecto independiente: no modifica Cauce Monitor, Jarvis ni Cauce Network Engine.

## Inicio rápido

**Windows:** ver [docs/WINDOWS.md](docs/WINDOWS.md). En resumen: `instalar_windows.bat` y luego `iniciar_windows.bat`.

**Linux/macOS:**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium
python -m cauce_form_connector ui          # abre http://127.0.0.1:8765
```

En la interfaz: elija un formulario de laboratorio, cargue `sample_data/personas_ficticias.xlsx`,
revise las correspondencias y pulse «Rellenar carácter por carácter».

## Pruebas y demostración

```bash
python -m pytest -q                 # 70 pruebas (unitarias, navegador, interfaz, seguridad)
python scripts/demo_lab.py          # genera evidence/out/informe_demo.md y capturas
python scripts/demo_lab.py --headed # lo mismo viendo el navegador escribir
python scripts/ejemplo_cauce_monitor.py --no-interactivo  # ejemplo de integración con Cauce Monitor (API local, v0.1.0)
python -m cauce_form_connector preparar < solicitud.json   # contrato cauce.conector.v1: docs/CONTRATO_CAUCE_CONECTOR_V1.md
python scripts/recorrido_ui.py      # recorrido de la interfaz paso a paso, con capturas
```

`evidence/` contiene un informe y capturas de una ejecución de referencia.

## Línea de comandos

```bash
python -m cauce_form_connector analyze --html lab_forms/01_tradicional.html
python -m cauce_form_connector propose --html lab_forms/03_validaciones.html --data sample_data/personas_ficticias.csv
python -m cauce_form_connector fill --html lab_forms/03_validaciones.html --data sample_data/personas_ficticias.json --row 0 --screenshot salida.png
```

Salida en JSON por stdout. Códigos de salida: `0` correcto, `2` terminado con incidencias, `3` URL no autorizada, `1` error.

## Autorrellenado desde un expediente (Cauce Monitor)

```bash
python scripts/recorrido_monitor.py          # demo visible con datos ficticios; no envía
python -m cauce_form_connector autorrellenar --formulario config/formularios/lab_validaciones.json \
    --expediente sample_data/expediente_ficticio.json --puerto 8765 --aprobar-perfil rev_demo
```

Detalle y límites en [docs/CONTRATO_CAUCE_CONECTOR_V1.md](docs/CONTRATO_CAUCE_CONECTOR_V1.md) §8.

## Analizar un formulario externo (solo lectura)

```bash
python -m cauce_form_connector analizar-formulario --url https://sitio-autorizado.example/reserva --authorized \
    --expediente sample_data/expediente_monitor_sintetico.json
```

En Windows: doble clic en `analizar_formulario_windows.bat` (pide la URL y abre la vista previa).
El dominio debe estar en `config/allowed_hosts.json` y hay que declarar la autorización. Abre la página una vez,
sin ventana, y **no escribe, no hace clic ni envía**. Deja en `informes/analisis_<formulario>_<fecha>/`:

| Archivo | Para qué |
|---|---|
| `informe.md` / `informe.json` | Campos, selectores, tipos, opciones, fechas, confirmaciones, validaciones, elementos dinámicos y decisión por campo (automático / revisar / humano / ignorado) |
| `perfil_borrador.json` | Perfil en el formato de `config/formularios/*.json`, solo con los campos automáticos; los dudosos van en `_borrador.pendientes_revision` |
| `vista_previa.html` | Página local para marcar qué campos incluir, corregir la ruta del expediente y descargar el perfil revisado |

Las correspondencias usan el **modelo real del Expediente de Monitor** (campos planos: `nombre`, `fecha_nacimiento`,
`telefono`, `correo`, `id_externo`, `id_registro_previo`, `folio_anterior`, `tipo_tramite`, `operacion`, `region`,
`sucursal`, `fecha_solicitada`, `fechas_desde`, `fechas_hasta`, `modo`; ver `cauce_form_connector/esquema_monitor.py`).
Solo `nombre` (si el formulario pide el nombre completo), `fecha_nacimiento`, `telefono` y `correo` pueden ser
automáticos. Nombre/apellidos separados, fechas de cita, folios e identificadores van a «revisar»; los códigos
(`tipo_tramite`, `operacion`, `region`, `sucursal`, `modo`) exigen una tabla de equivalencias aprobada
(`_borrador.equivalencias_pendientes`); CURP, RFC y consentimientos nunca se proponen.

El perfil sigue siendo un borrador: `autorrellenar --formulario <perfil> --expediente <exp> --aprobar-perfil rev_xxx`
es el paso humano que lo aprueba. Códigos de salida: `0` informe generado, `3` URL no autorizada (no se abre),
`4` la página exige intervención humana (CAPTCHA), `6` expediente ilegible.

## Estructura

| Componente | Archivo |
|---|---|
| Analizador de formularios externos (informe + perfil borrador) | `cauce_form_connector/analizador_formularios.py` |
| Analizador HTML (estático, sin navegador) | `cauce_form_connector/html_analyzer.py` |
| Inspector de páginas dinámicas (Playwright) | `cauce_form_connector/page_inspector.py` |
| Detector de formularios | `cauce_form_connector/detector.js` + `form_detector.py` |
| Motor de correspondencia | `cauce_form_connector/matcher.py` |
| Motor de escritura secuencial | `cauce_form_connector/sequential_typer.py` |
| Validador de resultados | `cauce_form_connector/validator.py` |
| Orquestador (relleno por pasadas, parada antes de enviar) | `cauce_form_connector/engine.py` |
| Interfaz de revisión | `cauce_form_connector/web/` |
| Adaptador de entrada de datos | `cauce_form_connector/data_adapter.py` |
| Registro de auditoría sin datos personales | `cauce_form_connector/audit.py` |
| Configuraciones reutilizables | `cauce_form_connector/config_store.py` |
| Política de URLs autorizadas | `cauce_form_connector/url_policy.py` + `config/allowed_hosts.json` |

Más detalle: [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) ·
Contrato de integración: [docs/CONTRATO_CAUCE_MONITOR.md](docs/CONTRATO_CAUCE_MONITOR.md) ·
Limitaciones y pendientes: [docs/LIMITACIONES.md](docs/LIMITACIONES.md) ·
Seguridad: [docs/SEGURIDAD.md](docs/SEGURIDAD.md)

## Uso responsable

- Solo formularios propios o con autorización expresa. Los dominios externos deben añadirse a
  `config/allowed_hosts.json` y, además, declararse la autorización en cada análisis.
- No se implementa ninguna evasión de CAPTCHA, bloqueo, control de acceso ni detección anti-bot.
  La pausa entre teclas es fija a propósito (no se "humaniza").
- Para pruebas use solo datos ficticios (`sample_data/`, dominios `example.com` / `example.org`).
