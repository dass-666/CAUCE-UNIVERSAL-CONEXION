# Revisión de seguridad v0.1

Fecha: 2026-11-20. Alcance: aplicación local de una persona (127.0.0.1). No se añaden autenticación
ni infraestructura: no las necesita una herramienta que no se expone a la red.

## Defectos encontrados y corregidos

| # | Defecto (demostrado) | Gravedad | Corrección | Prueba que lo verifica |
|---|---|---|---|---|
| 1 | **El HTML importado podía comunicarse con servicios locales.** El bloqueo de red se aplicaba solo a la página (`page.route`): un `WebSocket` llegó a un servidor en 127.0.0.1, y `navigator.sendBeacon` no lo intercepta el enrutado de Playwright. Las ventanas emergentes quedaban fuera del bloqueo | Alta: un HTML malicioso pegado podía tocar servicios del equipo o de la red interna | Bloqueo a nivel de **contexto** (`context.route` + `route_web_socket`), contexto en modo **sin conexión** (`offline=True`, cubre beacons), service workers bloqueados, descargas desactivadas y ventanas emergentes cerradas | `tests/test_security.py::test_imported_html_cannot_reach_local_services`, `::test_imported_html_cannot_navigate_away` |
| 2 | **Una página autorizada podía redirigir a destinos no autorizados** (p. ej. una IP interna) y el conector la seguía | Media | Cada navegación del marco principal, incluidas redirecciones, pasa por la política de URLs (`UrlPolicy.guard`). Las no autorizadas se abortan antes de salir del equipo | `::test_authorized_page_cannot_redirect_to_unauthorized_host` |
| 3 | **La captura de pantalla de la interfaz se guardaba en disco** (`logs/captura_actual.png`) con los datos personales escritos | Media | La captura se genera en memoria y se envía con `Cache-Control: no-store`; no se escribe ningún archivo | `::test_screenshot_not_persisted_and_close_discards_data` |

Mejoras menores aplicadas en la misma revisión:

- La API rechaza peticiones con `Sec-Fetch-Site: cross-site` o `same-site` (además del filtro `Host`/`Origin` ya existente). Prueba: `::test_cross_site_fetch_metadata_rejected`.
- `file://` solo admite archivos `.html`/`.htm` (antes podía abrirse cualquier archivo local en el navegador del conector). Prueba: `::test_file_urls_limited_to_html`.
- Botón «Terminar» en la interfaz: cierra el navegador y descarta de la memoria las filas cargadas (`/api/close` ya existía; ahora también borra los datos).

## Componentes revisados sin defectos nuevos

| Componente | Resultado |
|---|---|
| Carga de HTML | Límite de 2 MB; se abre en un contexto aislado sin cookies ni almacenamiento previos; sin red (ver #1) |
| JavaScript del HTML importado | Se ejecuta dentro de Chromium (sandbox del navegador) con origen opaco `about:blank`: no puede leer `file://` ni otros orígenes. Ver límite L1 |
| Restricciones de URL | Solo localhost/127.0.0.1/::1, `file://*.html` y dominios de `config/allowed_hosts.json` con declaración de autorización. Subdominios engañosos (`dominio.com.atacante.net`) rechazados |
| Archivos locales | El parámetro `lab` se valida contra la lista real; los nombres de perfil se sanean (sin `../`) |
| API local | Escucha solo en 127.0.0.1; filtros `Host`, `Origin` y `Sec-Fetch-Site`; los cuerpos JSON exigen `Content-Type: application/json` (no se pueden forzar desde un formulario de otra web) |
| Interfaz (XSS) | Todo texto procedente del HTML analizado se escapa. Prueba: `test_web_app.py::test_ui_escapes_labels_from_imported_html` |
| Perfiles | Guardan estructura y correspondencias, nunca valores |
| Auditoría | Lista blanca de claves, sin valores, URLs sin query/fragmento, redacción de correos y números largos. Prueba: `test_audit_log_contains_no_personal_data` |
| Datos personales temporales | Las filas viven solo en memoria del proceso; los archivos subidos los gestiona Starlette en un temporal que se elimina al terminar la petición; los logs de acceso de uvicorn están desactivados |
| CAPTCHA / intervención humana | Se mantiene el bloqueo: no rellena y `submit` devuelve 403 |

## Analizador de formularios externos

- **Autorización antes de abrir:** usa la misma `UrlPolicy` (lista `config/allowed_hosts.json` + declaración `--authorized`);
  si falla, el navegador ni se inicia (prueba `test_url_externa_sin_autorizacion_no_se_abre`). Las redirecciones a dominios
  no autorizados se bloquean con el mismo `nav_guard`.
- **Solo lectura:** una carga, sin ventana, sin cookies previas; no escribe, no hace clic ni envía. Prueba: el servidor de
  laboratorio registra una sola petición GET al formulario y ningún POST.
- **Sin valores personales en las salidas:** el informe, el perfil y la vista previa dicen si hay dato y si encaja, nunca el
  valor (prueba `_sin_secretos` sobre los cuatro archivos y la salida del CLI).
- **Sin equivalencias inventadas:** CURP, RFC, documentos y consentimientos no se proponen; `id_externo` (personal) no
  se asume equivalente a ningún campo; los códigos de Monitor exigen una tabla aprobada. El expediente sintético deja
  `id_externo` vacío.
- **Vista previa local:** HTML autónomo con `Content-Security-Policy: default-src 'none'`; no carga nada de la red. Los textos de
  la página analizada se escapan antes de mostrarse.

## Límites conocidos (documentados, no corregidos en v0.1)

- **L1. Scripts que bloquean el navegador:** un HTML con un bucle infinito deja ocupado el hilo de Playwright; hay que cerrar la aplicación (Ctrl+C) y volver a abrirla.
- **L2. La página puede engañar al detector:** `detector.js` corre en el mismo contexto que los scripts de la página, que podrían alterar lo que ve. Afecta a la exactitud del análisis, no a la seguridad del equipo.
- **L3. Salidas que sí contienen datos:** el `FillReport` (interfaz, API y stdout del CLI) incluye valores esperados y registrados porque la persona debe revisarlos. Quien lo consuma (p. ej. Cauce Monitor) no debe guardarlo en logs. `--screenshot` del CLI y `scripts/demo_lab.py` escriben capturas porque se piden explícitamente (la demo usa datos ficticios).
- **L4. Excel muy comprimido:** un `.xlsx` de 10 MB podría descomprimirse a mucho más en memoria. Riesgo bajo en uso local con archivos propios.
- **L5. Sin autenticación:** cualquier programa del mismo equipo puede llamar a la API. Es aceptable para un uso personal local; no exponga el puerto ni lo use en equipos compartidos.
