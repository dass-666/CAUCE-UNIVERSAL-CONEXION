# Contrato `cauce.conector.v1`: implementación en el Conector Universal

Este documento **sustituye** al borrador `CONTRATO_CAUCE_MONITOR.md` para la integración con Cauce Monitor. Si ambos difieren, manda este y, sobre él, el contrato de Monitor.

- **Contrato autoritativo:** Cauce Monitor, rama `claude/lab-puerto-estable`, commit `9e7f88d` (`docs/CONECTOR_CONTRATO_v1.md`, `monitoreo/conector/esquemas/*.json`, `monitoreo/conector/contrato.py`). No se modificó ese repositorio.
- **Cómo se evita una segunda versión incompatible:** los dos esquemas JSON y las reglas de validación se copiaron **tal cual** a `cauce_form_connector/contrato_v1/` (con `PIN.txt` y las huellas SHA-256 de origen; una prueba detecta ediciones a mano). El conector valida cada respuesta con el mismo código que usará Monitor antes de imprimirla.
- **Marcas:** **[V]** verificado con una prueba o comando de esta rama · **[I]** inferido · **[P]** pendiente o no verificado.

## 1. Uso

```bash
# 1) Una persona revisa el formulario real y aprueba qué campo lógico va a qué campo (archivo JSON {"nombre": "registro::nombre", ...})
python -m cauce_form_connector aprobar-perfil --url http://127.0.0.1:8765/01_tradicional.html \
    --perfil perfil_lab --formulario-id frm_lab --aprobado-por rev_lab --campos campos_aprobados.json
#    imprime perfil_sha256 y firma_estructura_esperada (v2:...) -> son los valores que Monitor fija en la solicitud

# 2) Monitor lanza UN proceso por solicitud: JSON por stdin, JSON por stdout
python -m cauce_form_connector preparar < solicitud.json > respuesta.json ; echo $?
```

Variables: `CAUCE_PROFILES_DIR` (perfiles), `CAUCE_STATE_DIR` (por omisión `state/`: ids de solicitud ya usados), `CAUCE_ALLOWED_HOSTS_FILE`, `CAUCE_HEADLESS` (por omisión **ventana visible**, necesaria para la revisión humana).

## 2. Qué hace `preparar` (orden fijo; cada paso falla de forma segura)

1. Lee **una** solicitud de stdin (máx. 1 MB). Si no es JSON, no cumple el esquema o trae campos extra → **solo código de salida 6, sin JSON** (igual que el simulador de Monitor).
2. Reglas semánticas del contrato (`validar_solicitud`, vendido de Monitor): `formulario_id` de la autorización = el del formulario, ventana `caduca_en − emitida_en ≤ 900 s`, campos 1–64 y valores de texto ≤ 200.
3. **Caducidad:** `ahora ≥ caduca_en` → rechazo (`SOLICITUD_CADUCADA`); autorización vencida → `AUTORIZACION_CADUCADA`. Se comprueba al inicio **y otra vez justo antes de teclear**.
4. **Política de URL** (`allowed_hosts.json`; local siempre permitido): otro dominio → `FORMULARIO_NO_AUTORIZADO`.
5. **Un solo uso:** se crea `state/solicitudes_usadas/<solicitud_id>` con apertura exclusiva (contiene solo la hora). Existe → `SOLICITUD_REPETIDA`. El id se consume **antes** de abrir el navegador y aunque después se rechace el perfil: Monitor debe emitir un id nuevo.
6. **Perfil aprobado:** se leen los bytes del archivo **una sola vez**, se compara su SHA-256 con `perfil_sha256` (comparación en tiempo constante) y esos mismos bytes se interpretan. Debe ser versión 2, `aprobado: true`, mismo `formulario_id` y misma `url`, con la firma v2 igual a `firma_estructura_esperada`. Si no → `PERFIL_NO_COINCIDE`. Un perfil v1 (de la interfaz) se **lee** pero **nunca** se acepta aquí. Cada campo pedido debe estar en el perfil (`CAMPO_NO_DECLARADO` si no).
7. **Navegador** (visible): fallo al arrancar → `NAVEGADOR_NO_DISPONIBLE`; fallo de red, navegación bloqueada o **redirección** a otra página (aunque sea local) → `PAGINA_NO_DISPONIBLE`.
8. **CAPTCHA / «solo humanos»** → `detenido_intervencion_humana`, 0 teclas; no se intenta superar. **Firma v2 distinta** → `detenido_estructura_cambiada` con `cambios` (`faltan`, `nuevos`, `modificados`: solo claves y tipo de cambio, nunca etiquetas ni valores), 0 teclas.
9. **Relleno** solo de los campos aprobados y pedidos, carácter a carácter (motor existente), sin completar con sugerencias, sin campos tardíos. Verificación posterior de cada campo.
10. **Revisión humana** en la ventana visible (§4). El conector **nunca hace clic** ni envía.
11. **Última barrera:** la respuesta se valida con `validar_respuesta` de Monitor (esquema, filtro de datos personales, huella, coherencia estado/código). Si algo falla se emite un `error_interno` genérico.

### Estados que emite y decisiones propias [la tabla de Monitor no las fija]

| Situación | `estado` | `errores[].codigo` | Salida |
|---|---|---|---|
| Revisada y cerrada sin enviar | `preparado_revisado_sin_envio` | — | 0 |
| Persona pulsó enviar (observado) | `enviado_por_persona_observado` | — | 0 |
| Campo `diferente/invalido` o confirmación que no coincide | `preparado_con_incidencias` | `CAMPO_NO_VERIFICADO` | 2 (la ventana queda abierta hasta que la persona la cierra o vence la espera) |
| Autorización/formulario/perfil no válido o alterado | `rechazado_no_autorizado` | `FORMULARIO_NO_AUTORIZADO`, `AUTORIZACION_CADUCADA`, `PERFIL_NO_COINCIDE` | 3 |
| CAPTCHA | `detenido_intervencion_humana` | `INTERVENCION_HUMANA_REQUERIDA` | 4 |
| Estructura distinta | `detenido_estructura_cambiada` | `ESTRUCTURA_CAMBIADA` | 5 |
| Solicitud caducada, repetida, campo no declarado | `rechazado_solicitud_invalida` | `SOLICITUD_CADUCADA`, `SOLICITUD_REPETIDA`, `CAMPO_NO_DECLARADO`, `SOLICITUD_MAL_FORMADA` | 6 |
| Nadie revisó a tiempo (o `espera_revision_max_s = 0`) | `revision_caducada` | `REVISION_CADUCADA` | 7 |
| Navegador/página no disponible, caída, fallo interno | `error_interno` | `NAVEGADOR_NO_DISPONIBLE`, `PAGINA_NO_DISPONIBLE`, `ERROR_INTERNO` | 1 |

La asignación de **qué estado** corresponde a perfil alterado, solicitud repetida o caducada, y de `firma_estructura = ""` + `estructura_coincide = false` en rechazos previos al navegador, es **decisión del conector** (el contrato solo enumera códigos). Se pide a Monitor que la confirme **[P]**. Todas esas respuestas pasan `validar_respuesta` de Monitor **[V]**.

La respuesta **no incluye `detalle`** en ningún caso (el contrato lo permite opcional): los códigos bastan y así no hay texto libre que pueda filtrar valores.

## 3. Idempotencia

Dos niveles, como en el contrato: `solicitud_id` (viaja, **un solo uso en el conector**, comprobado con archivo de creación exclusiva: dos procesos simultáneos con el mismo id no pasan los dos) y `clave_idempotencia` (**solo de Monitor**, no viaja). El conector no la conoce ni la necesita. Con Monitor, un reintento tras `INCIERTA` exige **id nuevo** y conciliación humana.
Límite: el registro de ids crece sin límite (un archivo de ~25 bytes por solicitud) y no se purga **[P]**; si se borra la carpeta `state/`, se pierde la protección contra repeticiones.

## 4. Revisión humana, espera, cancelación y caducidad (Fase 3)

**¿Contradice mantener el proceso mientras la persona revisa la caducidad acordada? No.** `caduca_en` es «el último instante para **empezar**» (Monitor, `docs/CONECTOR_CONTRATO_v1.md` §1) y el proceso puede durar hasta `espera_revision_max_s` (≤ 900 s) más 180 s de margen, tras lo cual Monitor lo termina y deja la solicitud `INCIERTA`. Por eso el conector comprueba la caducidad al inicio y antes de teclear, y **no** la vuelve a aplicar durante la revisión. Consecuencia a tener presente: una solicitud emitida a las 10:00 con ventana de 15 min puede estar en pantalla hasta ~10:30. Eso es coherente con el contrato, pero Monitor no debe interpretar «caducada» como «ya no hay datos en la ventana».

| Evento durante la espera | Resultado | Fiabilidad |
|---|---|---|
| La persona cierra la ventana sin enviar | `preparado_revisado_sin_envio` (`cerrada_sin_envio`) | [V] por prueba; **[I]** no distingue «cerró» de «el sistema operativo mató el navegador» si Chromium emite el cierre de página antes de desconectarse. El peor caso es un PREPARADO sin revisión real: nada se envió |
| Pulsa el botón de envío (gesto real) | `enviado_por_persona_observado` | **Indicio, no confirmación** (§5/H1) |
| Pasa `espera_revision_max_s` | `revision_caducada` (7) | [V] |
| Caída de la pestaña | `error_interno` `ERROR_INTERNO` | [V] (`chrome://crash`) |
| Cancelación por `SIGINT`/`SIGTERM` | Cierra el navegador, sale con 1 **sin JSON**; Monitor lo trata como sin respuesta → `INCIERTA` | [I] en Linux; en Windows `terminate()` no es ordenado: el navegador puede quedar abierto [P] |
| Monitor mata el proceso | Sin respuesta → `INCIERTA` (lo deriva Monitor) | [I] |

**Qué cuenta como «envío humano observado».** El conector registra los eventos `submit` de la página con un oyente de captura y solo cuenta uno si ocurre con **activación de usuario vigente** (`navigator.userActivation.isActive`) y **al menos 6 s después** de terminar el relleno (la activación transitoria dura ~5 s y el propio tecleo del conector la produce). Una prueba demuestra que un script de la página que llama a `requestSubmit()` dentro de esa ventana y fuera de ella **no** se cuenta [V]. Sub-reporta a propósito: un envío humano en los primeros 6 s se informará como cierre sin envío o caducidad. **Aun así sigue siendo un indicio:** que el navegador dispare `submit` no prueba que el sitio recibiera el formulario. Monitor debe seguir exigiendo su propio comprobante (folio).

## 5. Correcciones H1–H4 (cambios de comportamiento documentados)

| H | Defecto en v0.1.0 | Corrección | Compatibilidad |
|---|---|---|---|
| **H1** | `submit_with_human_confirmation` aceptaba `" ENVIAR\n"`, funcionaba sin rellenar, se repetía y devolvía `submitted: true` aunque el navegador bloqueara el envío | Frase **exacta**; exige un relleno completado en **esa** página; **un solo uso** (se consume antes del clic); inexistente si `session.integration_mode`; devuelve `clic_solicitado`, `envio_observado` y `confirmado_recepcion` (siempre `false`) | **Cambia:** `submitted` ahora significa «envío observado» (antes «se hizo clic»). La interfaz sigue funcionando; un segundo ENVIAR exige volver a rellenar. `fill_form` no cambia su firma |
| **H2** | `fill` por CLI aplicaba también propuestas «revisar» | `fill` usa solo propuestas `auto` + aprobaciones expresas (`--mapping`/`--profile`). Si queda alguna propuesta `revisar` sin aprobar, **no escribe nada**, imprime `{"codigo": "CORRESPONDENCIA_REQUIERE_REVISION", "campos": [...], "escrito": 0}` y sale con **4**. `preparar` solo usa el perfil aprobado | **Cambia** la salida de `fill` en datos ambiguos (antes los rellenaba) |
| **H3** | `--profile` ignoraba cambios de estructura y de sitio, y completaba con sugerencias | `fill --profile` rechaza (código **5**, `PERFIL_NO_COINCIDE`, sin escribir) si la estructura (firma v2) o el sitio cambiaron, y no añade campos tardíos. `preparar` añade huella SHA-256, vínculo con `formulario_id` y URL | **Cambia** `fill --profile` (antes seguía) |
| **H4** | Firma = `clave:tipo` | Firma **v2**: clave, etiqueta/aria/placeholder normalizados, tipo, etiqueta HTML, `required`, `pattern`, longitudes, límites, `autocomplete`, opciones (valor + etiqueta) y campo confirmado; sin visibilidad, selector ni valores. Formato `v2:<16 hex>` | Perfiles v1 **se siguen leyendo**; `reconcile` los compara con v1 y marca `legacy_signature: true`; los perfiles nuevos de la interfaz guardan ambas firmas. **En `preparar` un perfil v1 se rechaza** hasta que una persona lo apruebe con `aprobar-perfil` |

**La firma v2 la define el conector; Monitor solo la compara.** Cambiar su definición invalida todas las firmas aprobadas (hay que volver a aprobar). Se recomienda que Monitor guarde la firma y la huella que imprime `aprobar-perfil`, no las calcule.

## 6. Seguridad y límites (Fase 4)

Verificado con pruebas en `tests/test_integracion_contrato.py`, `tests/test_correcciones_h1_h4.py` y `tests/test_cli_h2_h3.py`: solicitud repetida, caducada, perfil alterado un byte, perfil v1, perfil de otro formulario o URL, firma que no es la del perfil, campo no declarado, cambio de etiqueta (0 teclas, 0 valores), campo nuevo/faltante, CAPTCHA (0 teclas), navegador inexistente, servidor caído, redirección local y a dominio no autorizado, cierre del navegador, caída de la pestaña, excepción con un valor dentro (no sale por stdout ni stderr), proceso real: un solo JSON por stdout, ningún valor en stdout/stderr, ningún archivo nuevo salvo el id (solo hora), nada en el directorio de trabajo.

No verificado o con límites **[P]**:
- **Archivos temporales del navegador:** Chromium crea un perfil temporal (en `/tmp` o `%TEMP%`) que Playwright borra al cerrar. No se inspeccionó su contenido **durante** la ejecución, así que no se puede afirmar que nunca contenga datos tecleados. El conector no escribe capturas, auditoría de valores ni temporales propios [V].
- **Huella de preparación:** el contrato la define sobre los valores **solicitados**, no sobre los leídos de la página. Lo que respalda que se escribieron es el estado `ok` de cada campo. Se sugiere a Monitor no tratarla como prueba de lo escrito.
- **Windows:** solo hay CI (`windows-latest`); **no hay validación física** de este modo. Puntos a mirar allí: cierre ordenado ante `terminate()`, perfil temporal del navegador y ruta de `state/`.
- **Aislamiento:** el proceso hereda los permisos del usuario que lo lanza; no hay protección frente a un compromiso del sistema (el propio contrato lo reconoce en Monitor §4).
- **Formularios con iframes, Shadow DOM o controles personalizados** siguen sin analizarse (L existentes); en esos casos la firma no los ve.
- La firma se calcula con el análisis inicial: campos que aparecen **después** de interactuar no entran en ella (y en este modo no se rellenan).
- Los campos de confirmación (correo repetido) solo se rellenan y se informan si **también** están en el perfil aprobado.

## 7. Prueba conjunta con el simulador de Monitor

1. Clone Monitor en el commit `9e7f88d` (solo lectura) y este conector en esta rama.
2. **Prueba automática** (ya incluida): `MONITOR_REPO=<ruta a Cauce-monitor-engine> python -m pytest tests/test_integracion_contrato.py -k monitor`. Usa el `Registro` real de Monitor: crea → autoriza → lanza → `recibir(respuesta, código de salida)` y comprueba que el estado final es `CADUCADA`, `CON_INCIDENCIAS` y `RECHAZADA` según el caso. La CI del conector lo ejecuta con el commit fijado.
3. **Prueba manual con ventana visible** (laboratorio, datos ficticios): sirva `lab_forms/` en `127.0.0.1`, ejecute `aprobar-perfil`, construya una solicitud con `perfil_sha256` y firma impresos, lance `preparar` con `espera_revision_max_s: 120` **sin** `CAUCE_HEADLESS`, revise la ventana, ciérrela y compruebe `preparado_revisado_sin_envio`. Con el lanzador de Monitor (`puede_lanzar()` sigue en `False` por diseño) esto aún no es posible: hoy se lanza a mano o con un script.
4. Para comparar con el simulador de Monitor: `python -m monitoreo.conector.simulador < solicitud.json` produce el mismo esquema de respuesta; las diferencias esperables son `duracion_ms` y la asignación de estados de §2.

## 8. Autorrellenado desde un expediente (puente Monitor → conector)

Capa fina sobre `preparar` (no duplica el motor): `cauce_form_connector/puente_monitor.py`, subcomando `autorrellenar` y `scripts/recorrido_monitor.py`.

- **Perfil de formulario configurable** (`config/formularios/<id>.json`): `url`, `formulario_id`, `perfil`, `campos_formulario` (nombre lógico → clave de campo del formulario) y `campos_expediente` (nombre lógico → campo PLANO del Expediente de Monitor: `nombre`, `correo`, `telefono`…; ver `esquema_monitor.py`). Añadir un formulario nuevo = un JSON nuevo, sin tocar código.
- **Minimización:** solo se leen del expediente las rutas declaradas; lo demás nunca se toca [V: `test_solo_se_extraen_campos_declarados`]. Un dato vacío o ausente no se inventa: el campo queda para la persona y se informa como «sin dato» [V].
- **Aprobación humana del perfil:** `autorrellenar` no corre si no hay perfil aprobado (salida 6). Aprobar es explícito: `--aprobar-perfil rev_xxx` tras revisar `campos_formulario` frente al formulario real [V].
- **Informe sin valores:** etiqueta del campo, estado (`ok`, `ok_normalizado`, `diferente`, `invalido`), nº de caracteres y confirmaciones (correo/teléfono repetido). Los errores de validación del propio sitio se reflejan como `invalido` solo cuando la página los expone al conector.
- **Uso:** `python -m cauce_form_connector autorrellenar --formulario config/formularios/lab_validaciones.json --expediente sample_data/expediente_monitor_sintetico.json --puerto <p> [--aprobar-perfil rev_xxx] [--espera-s 300] [--json]`. Ventana visible; no envía; la persona revisa y cierra.
- **Resultado inequívoco de `autorrellenar`:** `informe.resultado` = `{total, verificados, pendientes[{campo, etiqueta, motivo}], completo}` y línea `RESULTADO:` en el texto. Salida **0** solo con todos los campos del perfil verificados; **8** si el contrato terminó en 0 pero quedó algún campo pendiente (dato, equivalencia o identificador ausente); `salida_contrato` conserva el código del contrato. `preparar` y el contrato fijado no cambian [V: `tests/test_resultado_inequivoco.py`].
- **Tablas de equivalencias** (`equivalencias` en el perfil de formulario): `{nombre_lógico: {"campo_monitor": "tipo_tramite", "valores": {"<código de Monitor>": "<valor de la opción>"}}}`. Todo campo que lea un código de Monitor (`tipo_tramite`, `operacion`, `region`, `sucursal`, `modo`) DEBE tener tabla; los códigos deben existir en Monitor cuando su catálogo se conoce [V: `test_tablas_invalidas_se_rechazan`].
  - Se aprueban junto con el perfil (`--aprobar-perfil rev_xxx`): la aprobación comprueba que cada valor sea una opción del formulario real y guarda la tabla dentro del perfil aprobado, protegida por su SHA-256 [V: `test_tabla_con_opcion_inexistente_no_se_aprueba`].
  - Si la tabla del JSON cambia después, `autorrellenar` se niega (`equivalencias_no_aprobadas`, salida 6) [V: `test_tabla_alterada_tras_aprobar_se_rechaza`].
  - Un código sin fila no se adivina: el campo queda para la persona (`sin_equivalencia`) [V].
  - `preparar` rechaza (`rechazado_solicitud_invalida` / `SOLICITUD_MAL_FORMADA`) un valor que no esté entre los aprobados para ese campo, por si Monitor enviara el código sin traducir [V: `test_preparar_rechaza_codigo_sin_traducir`]. El contrato fijado no cambia.
- **Pendiente [P]:** el lanzador de Monitor (`puede_lanzar()` sigue en `False`) y validación física en el PC con Windows 11.
