# Informe · Integración nativa de Cauce Forms con el ecosistema RC3

Fecha: 2026-11-28 · Rama: `claude/integracion-nativa-rc3` (desde `main` @ `aa1bb02`) · PR en **borrador**, sin fusionar.
Network Engine RC3 `f93dd58` y Monitor `7b92446`: **solo lectura** (no se modificaron).

**Cómo leer este informe.** Cada afirmación lleva su tipo de evidencia:

- **[Prueba]**: prueba automatizada que pasa en este entorno (nombre del archivo y la prueba).
- **[Medición]**: dato medido por un guion reproducible (archivo de resultados indicado).
- **[Inferencia]**: razonamiento sobre el código, sin prueba que lo ejecute.
- **[No verificado]**: falta comprobarlo; se dice qué haría falta.

Entorno de todas las pruebas: contenedor Linux en la nube (kernel 6.18, Python 3.13), Chromium de Playwright 1.65 en
modo headless, Network Engine en **modo simulado** (`python -m cauce sim`), portal ficticio **propio** en 127.0.0.1 y
datos ficticios. La confirmación humana «ENVIAR» de las jornadas la **simula el guion**. Nada de esto es una
validación física ni en Windows 11.

---

## 1. Auditoría inicial de Forms (`main` @ `aa1bb02`)

Línea base **[Prueba]**: 128 pruebas superadas, 1 omitida.

`submit_with_human_confirmation()` (`cauce_form_connector/engine.py`) ya tenía buenas defensas: frase exacta `ENVIAR`,
aprobación de un solo uso (`submit_armed` se consume antes del clic), sin envío en modo integración, y distinguía
«clic», «envío observado» y «recepción confirmada» (esta última siempre `False`). Lo que **no** tenía:

| Carencia | Gravedad | Impacto |
|---|---|---|
| Ningún registro durable antes del clic | Alta | Si Forms se cae durante el envío, al volver no queda constancia: nadie sabe que hay algo que revisar. |
| Nada impide reenviar el mismo expediente | Alta | Tras un resultado dudoso, la persona puede rellenar otra vez y pulsar ENVIAR: posible **duplicado**. |
| Ningún estado «incierto» | Alta | «Clic hecho, sin constancia» y «no se envió» se veían igual. |
| `session.settle()` después del clic | Media | Con un portal lento la petición quedaba retenida (medido: hasta 14 s bloqueada en `page.evaluate`). |
| Sin relación con Network Engine | — | No había diagnóstico de red ni forma de avisar al centro de revisiones. |

## 2. Estado de las ramas anteriores

**[Medición]** `git merge --no-commit` de cada rama sobre esta candidata (sin publicar nada):

| Rama (Forms) | PR | Commits sobre `main` | Conflictos con esta candidata | Recomendación |
|---|---|---|---|---|
| `claude/persona-termina-en-la-ventana` | #5 (abierto) | 20 | `.github/workflows/tests.yml`, `page_inspector.py` | Decidir primero #5. Los conflictos son de ganchos añadidos en ambos lados; se resuelven a mano en menos de una hora. **[Inferencia]** |
| `claude/analizador-formularios-externos` | #4 (borrador) | 19 | los mismos dos archivos | Comparte base con #5; resolver después de #5. |
| `claude/combos-editables` | sin PR | 2 | `web/static/app.js` | Conflicto pequeño en la interfaz. |
| `claude/integracion-monitor-autorrelleno` | #3 (borrador) | 1 | ninguno | Compatible tal cual. |
| `claude/contrato-cauce-conector-v1` | #2 (fusionado) | 0 | — | Ya está en `main`. |

Lo posterior a `aa1bb02` que esta integración necesitaba se reescribió aquí (ganchos de cierre del navegador en
`page_inspector.py`); no se trajo ningún commit de otras ramas. **No** se tocó ninguna de esas ramas.

### Auditoría del PR #8 de Network Engine (bloque A)

**[Medición]** El PR #8 (`feat/sesiones-navegador`, cabeza `e9e1366`) cambia 20 archivos; se comparó cada uno con RC3 `f93dd58`:

- **Iguales en RC3 (6):** `cauce/session_scenario.py`, `tests/test_sessions.py`, `tests/test_scenario.py` y las dos
  evidencias de `docs/evidencia/`. Esas pruebas forman parte de las 346 que pasan en RC3 **[Prueba]**.
- **Distintos (14):** todos con **más** líneas en RC3. Ninguna función (`def`) ni ruta de la API del PR #8 falta en RC3
  **[Medición]**: lo que hizo el PR se conserva.
- **Mejoras posteriores:** puerto 8780, persistencia de sesiones (por eso cambió el significado del 404), campo
  `operation`, `requires_human` con resolución solo de operador, marcas pendientes y credencial `review` para Monitor,
  tiempo de espera del cliente de 5 s y documentación del contrato para Forms.
- **Diferencias a revisar todavía:** que el 404 `unknown_session` ahora significa «registrarse de nuevo» (antes no había
  persistencia); los consumidores que aún sigan el PR #8 deben adaptarse. Forms ya lo hace **[Prueba]** (`test_04`).

**Recomendación:** cerrar el PR #8 **como sustituido** por la cadena #10 → #15, **solo con la aprobación de Ronaldo**, y
nunca fusionarlo junto con esa cadena (reintroduciría el puerto y la semántica anteriores). No se cerró ni se fusionó.

## 3. Arquitectura de integración

```
 Persona ──► Interfaz Forms (127.0.0.1) ──► envio.enviar_protegido ──► Chromium ──► Portal ficticio propio
                    │                         │   1) registro en disco «en_curso»            (token de un solo uso,
                    │                         │   2) aviso «in_progress» al motor (≤3 s)      acuse por API)
                    │                         │   3) clic · 4) acuse verificado o «incierta»
                    │                         ▼
                    │                  operaciones.json (+.bak, SHA-256, candado entre procesos)
                    ▼
       red/diagnostico.DiagnosticoRed ── hilo de latidos ──► Network Engine RC3 (credencial «session»)
                                                                   ▲
                                         Monitor (credencial «read» / «review») ── centro de revisiones
```

Decisión **componente copiado frente a dependencia**: se copió y fijó un cliente mínimo
(`red/cliente_ne.py`, origen y huellas SHA-256 en `red/PIN.txt`). Ventajas: Forms no instala el motor ni su CLI, la copia
no puede pedir rutas de administración, y una prueba avisa si el original cambia. Desventaja: hay que actualizar la copia
a mano cuando cambie el contrato (la prueba lo detecta). Network Engine no publica un paquete solo-cliente; si algún día
lo hace, convendría pasar a dependencia versionada. No se usa el consumidor antiguo de RC2.

## 4. Funcionalidades implementadas

1. **Diagnóstico nativo** (`red/diagnostico.py`): fases del motor (iniciada, activa, pérdida de conectividad, recuperada,
   requiere intervención, incierta, interrumpida, cerrada), estado del motor (conectado, no configurado, no disponible,
   401, 403, 429, 409) y conectividad (verificada, degradada, perdida, desactualizada, desconocida, sin diagnóstico).
   Network Engine es opcional; sin él la interfaz dice «sin diagnóstico de red», nunca «sin internet» **[Prueba]** `test_02`.
2. **Identidad estable del puesto** (`red/identidad.py`): `forms-` + 8 hexadecimales al azar en `puesto.json`; rechaza
   correos y números largos como etiqueta. Mismo id de sesión tras reiniciar Forms **[Prueba]** `test_04`.
3. **Registro durable de operaciones** (`operaciones.py`): estados `en_curso`, `confirmada`, `incierta`, `no_enviada`,
   `rechazada`, `revisada`; escritura atómica con `fsync`, copia `.bak` y resumen SHA-256; huella del expediente con
   HMAC y clave local; candado entre procesos; `operaciones.vivo` para que la orden de operador no toque una operación viva.
4. **Envío protegido** (`envio.py`): como máximo una vez por acción confirmada; confirmación solo con acuse verificable;
   lo desconocido queda `incierta` y bloquea repetir.
5. **Revisión del operador** (`python -m cauce_form_connector operaciones listar|revisar`): nota obligatoria,
   confirmación exacta del id, auditada, sin datos personales.
6. **Interfaz**: sección «Estado del puesto» con cuatro indicadores y una sola alerta de revisión; tabla de operaciones
   con «Comprobar acuse»; botón de envío desactivado mientras se envía.
7. **Portal ficticio de laboratorio** (`lab_portal/servidor.py`) con fallos deliberados (lento, cortes TCP antes y
   después de registrar, 500, acuse lento, capacidad y ritmo 429).
8. **Guiones de laboratorio**: jornadas y prueba prolongada (`scripts/jornada_laboratorio.py`), capturas
   (`scripts/capturas_rc3.py`) y sustituto nativo del paso 09b (`scripts/paso_09b_nativo.py`).

## 5. Cambios por archivo

| Archivo | Cambio |
|---|---|
| `cauce_form_connector/red/cliente_ne.py`, `red/PIN.txt` | Cliente mínimo fijado a RC3 (session: registrar, latir, consultar, cerrar). |
| `cauce_form_connector/red/diagnostico.py` | Latidos, estados, marcas por entregar, 401/403/404/409/429. Nunca llama a `/resolve`. |
| `cauce_form_connector/red/identidad.py`, `red/montaje.py` | Identidad del puesto; montaje que nunca impide arrancar Forms. |
| `cauce_form_connector/operaciones.py` | Registro durable (nuevo). |
| `cauce_form_connector/envio.py` | Envío protegido (nuevo); `engine.submit_with_human_confirmation` se conserva para la CLI. |
| `cauce_form_connector/page_inspector.py` | Avisos de cierre inesperado del navegador o de la página. |
| `cauce_form_connector/settings.py` | `CAUCE_NE_*`, `CAUCE_FORMS_DATA_DIR`, `CAUCE_FORMS_PUESTO`. |
| `cauce_form_connector/web/app.py`, `web/static/*` | API `/api/diagnostico`, `/api/operaciones`, verificación de acuse; envío con candado de 0,5 s (409 si ocupado); interfaz. |
| `cauce_form_connector/cli.py` | Orden `operaciones`. |
| `lab_portal/` | Portal ficticio (nuevo). |
| `scripts/jornada_laboratorio.py`, `capturas_rc3.py`, `paso_09b_nativo.py` | Laboratorio (nuevos). |
| `tests/test_red_ne_rc3.py`, `test_envio_protegido.py`, `test_operaciones.py`, `test_compat_monitor.py`, `test_red_cliente_pin.py`, `ne_proceso.py`, `monitor_lectura.py`, `servidor_app.py` | Pruebas (nuevas). |
| `.github/workflows/tests.yml` | Descarga NE `f93dd58` y Monitor `7b92446` de solo lectura; jornada corta en Linux. |
| `pyproject.toml`, `__init__.py` | Versión `0.2.0rc1`. |
| `docs/INTEGRACION_NETWORK_ENGINE.md`, este informe, `docs/MANIFIESTO_FORMS_RC3.txt` | Documentación. |

## 6. Commits y PR

Commits en la rama (todos sobre `aa1bb02`): `f53c1c6` (diagnóstico, registro y envío protegido), `423e34a`
(compatibilidad con Monitor, paso 09b nativo, pin, jornadas), `367ed54` (dos defectos hallados en las capturas, guía,
0.2.0rc1) y el commit de documentación final que añade este informe. El PR se abre en **borrador** contra `main` y no se
fusiona.

## 7. Resultados completos de pruebas

**[Prueba]** Suite completa de Forms con Network Engine RC3 real y Monitor real: **189 superadas, 2 omitidas** (6 min 55 s).

- Omitida 1: contrato antiguo de Monitor `9e7f88d` (no había copia local; en CI sí se descarga).
- Omitida 2: prueba de permisos POSIX (el contenedor corre como root); la variante simulada sí pasa.

Network Engine RC3 en su propio repositorio: **346 superadas, 5 omitidas** (el brief citaba 323 en E8; `f93dd58` tiene más).

Las 18 situaciones adversas del bloque F:

| # | Situación | Prueba | Resultado |
|---|---|---|---|
| 1 | Funcionamiento normal | `test_01_funcionamiento_normal_y_lista_blanca`, `test_normal_confirma_solo_con_acuse_verificado` | Pasa |
| 2 | Network Engine apagado | `test_02_motor_apagado_no_bloquea_y_dice_sin_diagnostico` | Pasa |
| 3 | Network Engine reiniciado | `test_03_motor_reiniciado_con_operacion_en_curso_pide_revision` | Pasa |
| 4 | Forms reiniciado | `test_04_…`, `test_forms_muere_durante_el_envio_y_al_volver_queda_incierta` (SIGKILL real) | Pasa |
| 5 | Monitor reiniciado | `test_05_monitor_ve_la_revision_de_forms_y_reiniciarlo_no_cambia_nada` (código real de Monitor) | Pasa |
| 6 | Reinicio simultáneo de los tres | `test_06_reinicio_simultaneo_de_los_tres`, `test_06_reinicio_simultaneo_de_motor_y_forms_hereda_la_marca` | Pasa |
| 7 | Credencial revocada durante la operación | `test_07_credencial_revocada_durante_la_operacion_no_borra_alertas` | Pasa |
| 8 | Pérdida de conectividad | `test_08_09_perdida_y_recuperacion_no_desbloquean_lo_incierto` | Pasa |
| 9 | Recuperación | ídem | Pasa |
| 10 | Navegador cerrado | `test_10_navegador_cerrado_se_informa`, `test_navegador_cerrado_durante_el_envio_queda_incierta` | Pasa |
| 11 | Acción interrumpida antes del resultado | `test_corte_antes_de_registrar_queda_incierta_y_no_se_puede_repetir` | Pasa |
| 12 | Reintento de operación incierta | misma prueba + comprobación de 409 en cada jornada | Pasa |
| 13 | Doble clic | `test_doble_clic_en_la_interfaz_envia_una_sola_vez` (servidor real) | Pasa |
| 14 | Cierre de sesión con revisión pendiente | `test_14_…`, `test_centro_de_revisiones_de_monitor_y_marca_de_forms_sin_sesion` | Pasa |
| 15 | Registros dañados | `test_15_estado_del_motor_danado_pide_revision`, `test_archivo_danado_usa_respaldo…`, `test_archivo_y_respaldo_danados…`, `test_editar_el_archivo_a_mano_no_autoriza_nada` | Pasa |
| 16 | Motor lento | `test_16_motor_lento_o_congelado_no_retiene_la_accion` (SIGSTOP; omitida en Windows) | Pasa |
| 17 | Varias sesiones simultáneas | `test_17_varias_sesiones_simultaneas_aisladas` | Pasa |
| 18 | 403 y 429 | `test_18_403_…`, `test_18_429_…`, `test_limite_de_capacidad_429_se_respeta` | Pasa |

Ninguna exigencia se rebajó para obtener verde. Dos pruebas se **cambiaron** porque su expectativa era incorrecta, no
para que pasaran: (a) un corte después de que el portal registre ahora termina `confirmada` **por el acuse de la API**
(el diseño correcto) y se añadió la variante con la API caída, que queda `incierta`; (b) la prueba de credencial revocada
comprueba que la marca queda por entregar y que el motor pide revisión por sí solo al faltar latidos.

## 8. Resultados de jornadas simuladas

**[Medición]** `scripts/jornada_laboratorio.py --expedientes 6 10 20 50` (Forms, portal y Network Engine como procesos
reales; fallos deliberados; confirmación humana simulada). Archivo: `evidence/rc3/jornadas/jornadas.json`.

JORNADAS_TABLA

Lectura de la tabla:

- **Duplicados = 0** en todas: el portal nunca recibió dos veces el mismo expediente.
- «Recibidos» es menor que los expedientes por diseño: el corte *antes* de registrar no llega al portal, y ese
  expediente queda `incierta` y bloqueado (no se reenvía solo).
- Algunas `incierta` (portal lento, Forms reiniciado, navegador cerrado) pasaron a `confirmada` al final con
  «Comprobar acuse»: el portal sí las había recibido. Es lo esperado: Forms no supone, comprueba.
- «Bloqueos correctos»: intentos deliberados de repetir un expediente incierto o confirmado; todos respondieron 409.

**Qué es de Forms y qué es del simulador:** el registro, el bloqueo, la verificación por acuse y el diagnóstico son de
Forms. La red (caída y recuperación), el token de un solo uso, el acuse y los cortes TCP son del simulador y del portal
de laboratorio. Un portal real puede no tener acuse por API: entonces Forms **nunca** confirma (queda `incierta` y la
revisa una persona) **[Prueba]** `test_portal_ajeno_sin_acuse_nunca_se_da_por_recibido`.

## 9. Métricas de rendimiento

PROLONGADA_TEXTO

## 10. Capturas de pantalla

**[Medición]** `scripts/capturas_rc3.py`, Chromium real (headless) sobre la interfaz real de Forms, escala 2×.
Carpeta: `evidence/rc3/capturas/` (índice en `capturas.json`).

CAPTURAS_TABLA

Las capturas descubrieron un defecto real (ver §12, D6): antes de corregirlo, la captura «confirmada» mostraba
«Sesión: revisión humana necesaria» tras un envío lento confirmado. Repetidas con el código final, ya no.

## 11. Auditoría de seguridad

| Punto | Estado | Evidencia |
|---|---|---|
| Solo credencial `session` | Cumple: con `admin`, `read` o `review` el motor responde 403 y Forms lo muestra | [Prueba] `test_18_403_…` |
| Forms no resuelve marcas | Cumple: el cliente copiado no tiene `/resolve` ni rutas administrativas; con `session` el motor da 403 | [Prueba] `test_forms_no_puede_resolver_marcas`, `test_la_copia_no_tiene_rutas_administrativas` |
| Credencial protegida | DPAPI en Windows / archivo 0600 en Linux, creada por la orden oficial | [Prueba] `test_credencial_protegida_con_el_mecanismo_del_sistema` (en Linux: 0600; DPAPI solo en el runner Windows de CI) |
| Ningún secreto en archivos de Forms | Cumple | [Prueba] `test_ningun_secreto_en_los_archivos_de_forms` |
| Sin datos personales en el registro | Cumple: huella HMAC, notas saneadas | [Prueba] `test_el_archivo_no_guarda_datos_personales`, `test_huella_no_contiene_datos…` |
| Errores del motor no llegan a registros | Cumple: solo estado HTTP y código | [Inferencia] `CauceAPIError` descarta el mensaje |
| Solo 127.0.0.1, sin proxy | Cumple | [Inferencia] `ProxyHandler({})`; la API de acuse solo se consulta en loopback |
| 401 / 403 / 429 / 409 | Se muestran y no se insiste a ciegas (429: pausa de 2 s) | [Prueba] `test_07`, `test_18_*`, `test_sesion_ocupada…` |
| Sesiones ajenas | Una sesión no puede latir en la de otra (`not_session_owner`) | [Prueba] `test_17_…` |
| Monitor no muestra ids ni credenciales de Forms | Cumple | [Prueba] `test_05_…` |
| Cookies, formularios, credenciales del portal | No se guardan | [Inferencia] el registro solo admite los campos de una lista blanca (`CAMPOS`) |

Límites (no resueltos aquí): DPAPI no aísla procesos del mismo usuario de Windows; en Linux la credencial es un archivo
0600 en claro; la interfaz local sigue sin usuarios (solo 127.0.0.1 y filtro de `Host`/`Origin`); `huella.key` en
Windows queda con los permisos heredados del perfil **[No verificado]**.

## 12. Defectos encontrados y corregidos

| # | Defecto | Gravedad | Corrección | Comprobación |
|---|---|---|---|---|
| D1 | **Chromium reintenta por su cuenta un POST cuya conexión se corta** (hallazgo del navegador, no de Forms). Sin token de un solo uso en el portal, eso sería un duplicado que Forms no puede impedir. | Alta para portales reales | El portal de laboratorio usa token de un solo uso; Forms no da nada por recibido sin acuse. Riesgo documentado para portales externos. | `test_chromium_reintenta_por_su_cuenta_un_post_cortado_y_el_token_evita_el_duplicado` |
| D2 | `page.evaluate` quedaba bloqueado hasta 14 s con una navegación pendiente | Media | Escucha de respuestas + `wait_for_timeout`; sin `settle` tras el clic | `test_portal_lento_queda_incierta_y_luego_se_confirma` |
| D3 | El clic esperaba la navegación y vencía con portales lentos | Media | `click(no_wait_after=True)` | ídem |
| D4 | Candado de archivo: autobloqueo en llamadas anidadas | Alta (cuelgue) | Candado reentrante | suite de `test_operaciones.py` |
| D5 | Forms y la orden de operador podían pisarse el archivo | Alta | Candado entre procesos + relectura | `test_revision_del_operador_desde_otro_proceso_no_se_pierde` |
| D6 | **Falsa alarma**: un envío lento (varios latidos) hacía que Network Engine pidiera revisión humana aunque el envío terminara confirmado | Media (alarma innecesaria, contradicción en pantalla) | `pide_revision()` no cuenta la operación viva del propio proceso | `test_operacion_larga_en_curso_no_pide_revision_por_si_sola` (falla sin la corrección) |
| D7 | `operaciones listar` durante un envío convertía la operación viva en incierta | Media (dirección segura, pero un envío bueno quedaba dudoso) | `operaciones.vivo`: candado sostenido por el Forms que envía | `test_la_orden_de_operador_no_convierte…` (falla sin la corrección) |
| D8 | El medidor daba CPU 0 % siempre | Baja (métrica) | Mismo objeto de proceso entre muestras | prueba prolongada (§9) |
| D9 | Un fallo del portal pasaba al expediente siguiente en las jornadas | Baja (laboratorio) | `/_lab/limpiar_fallos` tras cada expediente | jornadas (§8) |
| D10 | La huella fijada del cliente fallaría en Windows (CRLF al descargar) | Baja (CI) | Comparación normalizada a LF | `test_los_archivos_de_origen_coinciden_con_el_pin` |

## 13. Solicitudes para el otro agente (Network Engine y Monitor)

No se modificó ninguno de los dos. Cada solicitud trae reproducción y una prueba de regresión propuesta.

**S1 · Network Engine · Media · `cauce/sessions.py`, `SessionRegistry.register`.** Si Forms pierde su id de sesión con
el motor en marcha (p. ej. se borra o daña `puesto.json`), el nuevo registro con la **misma** credencial recibe
`409 credential_has_session` mientras la sesión anterior siga registrada. Forms lo muestra («sesión ocupada») pero no
puede recuperarse solo. *Reproducción:* `tests/test_red_ne_rc3.py::test_sesion_ocupada_si_forms_pierde_el_id_con_el_motor_vivo`.
*Propuesta:* permitir que la misma credencial sustituya su propia sesión (motivo `replaced_by_owner`) heredando la marca,
como ya ocurre con las restauradas. *Prueba propuesta:* registrar, olvidar el id, registrar otra vez → 201 y
`requires_human` heredado.

**S2 · Network Engine · Baja · `SessionRegistry.resolve` y `_compute`.** Si el operador resuelve mientras el último
latido fue `error` con `operation: in_progress`, la marca reaparece al instante sin ningún latido nuevo (verificado al
depurar `test_centro_de_revisiones…`). Es la dirección segura, pero el operador ve «resuelto» y luego otra vez pendiente.
*Propuesta:* que `resolve` devuelva el estado recalculado, o 409 `condition_persists`, o que la resolución valga hasta
el siguiente informe de error. *Prueba propuesta:* latido error+in_progress → resolve → GET sin latidos → `requires_human` según lo acordado.

**S3 · Network Engine · Baja · contrato `session`.** La credencial `session` no puede saber que un operador resolvió
(desde Monitor) la marca pendiente de su etiqueta. Al volver, Forms sigue teniendo su operación local `incierta` y vuelve
a pedir revisión: **doble revisión** (segura, pero molesta). *Reproducción:* `tests/test_compat_monitor.py::test_centro_de_revisiones_de_monitor_y_marca_de_forms_sin_sesion`.
*Propuesta:* que la respuesta de `POST /sessions` incluya `resolved_pending: [{id, session_id, reason, resolved_at}]` de
su credencial o etiqueta; Forms marcaría como revisadas solo las operaciones cuyo `marca_ne` coincida.

**S4 · Network Engine · Propuesta · paso 09b (`scripts/laboratorio_integrado/sesion_laboratorio.py`).** Sustituir el
consumidor de referencia por Forms nativo: `scripts/paso_09b_nativo.py` de Forms acepta las **mismas** variables y escribe
el **mismo** JSON que el hijo actual. Cambio sugerido en `prueba()`: si existe `lb.forms / "scripts/paso_09b_nativo.py"`,
lanzarlo con `lb.py("forms")` en lugar de `__file__ --hijo`, y conservar el hijo como respaldo. *Evidencia:*
`tests/test_red_ne_rc3.py::test_paso_09b_con_forms_nativo_mismo_contrato` recorre las 8 fases con Chromium real, sin
ningún POST al portal. *Prueba propuesta en NE:* con una carpeta de Forms falsa que contenga el guion, `prueba()` lo usa.

**S5 · Monitor · Baja · documentación.** El centro de revisiones resuelve marcas sin sesión; las operaciones locales de
Forms se revisan aparte (`operaciones revisar`). Sugerencia: que Monitor, junto a una marca de una etiqueta `forms-*`,
recuerde que el puesto también puede tener una revisión local. Solo texto; no exige cambio de contrato.

## 14. Matriz de compatibilidad

| Componente | Versión | Resultado | Tipo de evidencia |
|---|---|---|---|
| Network Engine RC3 | `f93dd58` | Compatible | [Prueba] 24 pruebas contra el motor real en modo simulado |
| Contrato de sesiones (`docs/CONTRATO_SESIONES_FORMS.md`) | `f93dd58` | Cumplido; huellas fijadas | [Prueba] `test_red_cliente_pin.py` |
| Monitor | `7b92446` | Lectura de red y centro de revisiones compatibles | [Prueba] `test_compat_monitor.py` (servicio `monitoreo/red_lectura`; **no** la aplicación web completa con usuarios) |
| Centro de revisiones pendientes | `7b92446` | Ve y resuelve la marca que deja Forms; confirmación exacta | [Prueba] ídem |
| Chromium real | Playwright 1.65 (headless) | Compatible | [Prueba] |
| Windows 11 | — | **No verificado aquí**. CI incluye `windows-latest` (Windows Server, no Windows 11): DPAPI real y rutas | [No verificado] hasta ver el resultado del CI y probar en el equipo de Ronaldo |
| Instalaciones independientes | — | Forms no importa paquetes de NE ni de Monitor | [Inferencia] + [Prueba] del pin |
| Paso 09b | `f93dd58` | Mismo contrato con Forms nativo | [Prueba] `test_paso_09b_con_forms_nativo_mismo_contrato` |
| Network Engine RC2 / consumidor antiguo | — | No usado | — |

## 15. Manifiesto exacto de la candidata Forms

Ver `docs/MANIFIESTO_FORMS_RC3.txt`: versión, rama, commit base, commits, dependencias externas fijadas y huella SHA-256
de cada archivo de código.

## 16. Procedimiento de instalación aislada

Paso a paso en [`INTEGRACION_NETWORK_ENGINE.md`](INTEGRACION_NETWORK_ENGINE.md#instalación-aislada-windows-11). Resumen:
carpeta y entorno propios para Forms; el administrador del motor crea la credencial `session` con el **mismo usuario de
Windows** que ejecutará Forms; se configura `CAUCE_NE_SESSION_TOKEN_FILE`; sin esa variable Forms funciona como antes.
No modifica rutas globales, puertos públicos ni la instalación de Network Engine o Monitor.

## 17. Riesgos y validaciones físicas pendientes

| Pendiente | Riesgo | Cómo verificarlo |
|---|---|---|
| Windows 11 físico (instalación, DPAPI, cierre de la ventana de Chromium visible) | Medio | En el MSI de Ronaldo: instalar, crear la credencial con el mismo usuario, recorrer los estados con `iniciar_windows.bat`. |
| Red física (dos enlaces reales) | Medio | Toda la conectividad fue la simulada del motor. |
| Portales reales | **Alto** | Sin acuse verificable Forms nunca confirma; y sin token de un solo uso, el reintento propio de Chromium (D1) puede duplicar. Solo con autorización y acuerdo con el portal. |
| Aplicación web completa de Monitor (usuarios, roles, interfaz) | Bajo | Se probó su servicio de red y su centro de revisiones, no su interfaz con inicio de sesión. |
| PR #5 de Forms y esta candidata | Medio | Conflictos en dos archivos (§2); decidir el orden de fusión. |
| Solicitudes S1–S3 | Bajo-medio | Dependen del otro agente; Forms ya se comporta de forma segura sin ellas. |
| Confirmación humana | — | En las jornadas la «persona» es el guion. La interfaz real exige escribir ENVIAR. |
