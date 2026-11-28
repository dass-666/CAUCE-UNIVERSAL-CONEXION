# Candidata consolidada · Cauce Forms 0.2.0rc2

Fecha: 2026-11-28 · Rama `claude/candidata-consolidada` · **commit estable de código `08eeaa3`** · PR #7 en **borrador**, sin fusionar. Nada se fusionó en `main`.
Todo se probó con el portal propio de laboratorio (`lab_portal/`, 127.0.0.1) y datos ficticios. Network Engine y
Monitor no se modificaron (solo se leen, en commits fijados). Esto **no** es una validación en Windows 11 físico.

## 1. Qué contiene y en qué orden

| Paso | Origen | Commit | Qué trae |
|---|---|---|---|
| Base | `main` | `aa1bb02` | Contrato `cauce.conector.v1`, correcciones H1–H4 |
| 1 | PR #5 `persona-termina-en-la-ventana` | `d3b230a` → merge `030142a` | La persona termina en la misma ventana. **Incluye #4 y #3**: las ramas están apiladas (#3 ⊂ #4 ⊂ #5) |
| 2 | PR #6 `integracion-nativa-rc3` | `cd34070` (código `fdd099a`) → merge `080c2c5` | Forms nativo con Network Engine RC3, registro durable, envío protegido |
| 3 | Consolidación | `bd5c066` y siguientes | Bloques B, C y D de esta misión (abajo) |

**Sobre el PR #3:** no hace falta incorporarlo aparte. Su único commit (`16d3e87`) ya está dentro de #4 y #5
(`git merge-base --is-ancestor` lo confirma). Se evaluó su ruta (`autorrellenar`): usa `integracion.ejecutar`, así que
ahora también pasa por el registro de operaciones (§3).

## 2. Conflictos resueltos

| Archivo | Conflicto | Resolución | Riesgo comprobado |
|---|---|---|---|
| `page_inspector.py` · `open_url` | #4 guarda `last_settle_changes = settle()`; #6 añade `_avisar_apertura()` | Se conservan los dos, en ese orden | El aviso a Network Engine sigue saliendo tras abrir; el analizador sigue viendo los cambios del DOM |
| `.github/workflows/tests.yml` (bloque 1) | #4 añade la descarga de Ext JS; #6 cambia `pytest -q` por `pytest -q -rs` | Descarga de Ext JS + `pytest -q -rs` | — |
| `.github/workflows/tests.yml` (bloque 2) | #4 añade el analizador sobre `lab06`; #6 la jornada corta | Se conservan los dos pasos | — |

Archivos tocados por los dos lados y fusionados sin conflicto de texto: `cli.py` (órdenes `autorrellenar` y
`analizar-formulario` de #3/#4 junto a `operaciones` de #6) y `README.md`. Comprobado a mano que ninguna protección
nueva quedó sustituida por código antiguo:

- `page_inspector.on_route`: el bloqueo de escrituras del analizador (#4) va **antes** de la guarda de navegación y del
  registro de envíos de la persona (§3): durante un análisis nada sale, y fuera de él todo envío se anota.
- `integracion._esperar_revision` (#5) conserva su lógica; el registro se añade por el aviso de la sesión, sin cambiarla.
- La guarda de AST `test_ninguna_ruta_de_produccion_envia_sin_el_registro_durable` (#6) sigue en verde con #3/#4/#5:
  `autorrellenar` y `analizar-formulario` no envían.

**[Prueba]** Suite completa sobre el merge (antes de los cambios de esta misión): **289 superadas, 2 omitidas**
(permisos POSIX como root; ACL de Windows en Linux).

## 3. Registro de operaciones (bloque B)

| Inconsistencia de la revisión final | Solución (conservadora) | Prueba |
|---|---|---|
| Envío de la persona por **JavaScript sin evento `submit`** | Toda petición `POST/PUT/PATCH/DELETE` al **mismo sitio** del formulario que Forms no originó cuenta como envío de la persona. Se anota `incierta` **antes** de dejarla salir; si no se puede anotar, **se corta** | `test_envio_por_javascript_sin_evento_submit_queda_registrado_antes_de_salir`, `test_si_no_se_puede_registrar_el_envio_de_la_persona_no_sale` |
| **Hallazgo nuevo:** con la interfaz en reposo, la petición de la persona se quedaba **parada** en el interceptor de Forms (medido: 0 recibidas en 4 s) hasta la siguiente acción; si esa acción era ENVIAR, **salían las dos** | El hilo del navegador atiende los eventos cada 0,1 s en reposo y `enviar_protegido` vuelve a comprobar justo antes del clic | `test_envio_de_la_persona_con_la_interfaz_en_reposo_llega_y_queda_registrado`, `test_envio_de_la_persona_pendiente_y_enviar_a_la_vez_no_duplica` |
| Operaciones en modo **`preparar`** | El envío de la persona (botón o JavaScript) queda `incierta` en el registro de Forms, con `ref_solicitud` = `solicitud_id`. Un expediente bloqueado **no se vuelve a preparar** y no se abre el navegador | `test_preparar_envio_de_la_persona_queda_en_el_registro_y_no_se_prepara_otra_vez` |
| **Diferencias Monitor / Forms** | Las dos partes guardan el `solicitud_id`; ver tabla de conciliación abajo | idem |
| Operaciones observadas **sin verificación posible** | Siempre `incierta` (`verificable: false`); nunca `confirmada`. Solo un operador la cierra, y **solo `no_recibido` desbloquea** | `test_revisar_sin_resultado_quita_la_alerta_pero_sigue_bloqueando`, `test_resolver_la_marca_en_network_engine_no_desbloquea_el_expediente` |

Conciliación Monitor ↔ Forms (`preparar`):

| Qué vio Forms | Respuesta a Monitor (contrato sin cambios) | Registro de Forms |
|---|---|---|
| `submit` con gesto de la persona | `enviado_por_persona_observado` | `incierta` · `envio_humano_observado_preparar` |
| Escritura por JavaScript, sin `submit` | **sin cambio** (`preparado_revisado_sin_envio` o `revision_caducada`) | `incierta` · `envio_humano_por_javascript_preparar` |
| `submit` forzado por la propia página | sin cambio (no se atribuye a la persona) | `incierta` (salió una escritura) |
| Monitor vuelve a pedir ese expediente | `detenido_intervencion_humana` · `INTERVENCION_HUMANA_REQUERIDA` | sin cambio |

La segunda y la tercera fila son **diferencias que quedan**: el contrato `9e7f88d` no tiene un estado para «salió una
escritura que no se puede atribuir». Forms es el lado conservador y se niega a repetir. Va como solicitud al
responsable de E10 (§8).

Revisiones del operador (cambio de comportamiento): `operaciones revisar` exige `--resultado recibido | no_recibido |
sin_determinar`. Antes, cualquier revisión desbloqueaba el expediente, aunque el operador hubiera visto que **sí** llegó.
Las revisiones antiguas sin resultado se tratan como `sin_determinar` (siguen bloqueando).

## 4. Protección contra duplicados (bloque C)

| Caso | Resultado | Evidencia |
|---|---|---|
| Confirmadas | Bloquean siempre | `test_confirmada_tambien_bloquea…` |
| Archivadas (> 500 cerradas) | Siguen bloqueando; ahora también las revisadas que bloquean | `test_confirmadas_que_salen_de_la_lista_siguen_bloqueando`, `test_revisada_que_bloquea_tambien_se_archiva` |
| Inciertas | Bloquean hasta revisión **en el puesto** con resultado; una revisión de red solo se anota (§13) | `test_reinicio_convierte_en_curso_en_incierta…`, §3 |
| Pérdida de `huella.key` | Revisión `GENERAL`, nada se envía | `test_perder_huella_key_no_desactiva_en_silencio_la_proteccion` |
| Corrupción de archivos | `.bak` + revisión `GENERAL`; los dos dañados: todo bloqueado | `test_archivo_danado…`, `test_archivo_y_respaldo_danados…`, `test_editar_el_archivo_a_mano…` |
| Restauración desde respaldos | Lo posterior al respaldo sigue bloqueando; siempre `GENERAL` | §5 |
| Reinicios durante escrituras | 12 `kill -9` en mitad de escrituras: ninguna confirmada perdida | `test_matar_forms_mientras_escribe_nunca_pierde_una_confirmada` (Linux) |
| Varias instancias | **Hallazgo:** una segunda interfaz sobre el mismo registro podía enviar. Ahora no envía (`otro_forms_en_marcha`) | `test_segunda_interfaz_con_el_mismo_registro_no_envia` |
| Doble clic | Un solo envío | `test_doble_clic_en_la_interfaz_envia_una_sola_vez` |
| Envíos humanos directos | `incierta`, aprobación anulada | `test_envio_directo_de_la_persona…`, §3 |

**Hallazgo menor corregido:** si `iniciar` detectaba un bloqueo (carrera con otro proceso), la interfaz decía «no se
pudo guardar el registro» (403): `OperacionBloqueada` es `PermissionError`, que en Python es `OSError`. Ahora 409.

**Límite que sigue (sin cambios):** Chromium puede repetir por su cuenta un POST cortado y ese reintento no se ve desde
Forms (revisión final §1). **Forms no garantiza la ausencia de duplicados en portales sin protección contra
repeticiones.** Garantiza un solo clic por aprobación, una sola operación registrada y que nada se repita solo.

## 5. Respaldo y restauración (bloque D)

```
python -m cauce_form_connector estado respaldar --destino CARPETA
python -m cauce_form_connector estado verificar CARPETA\respaldo-forms-AAAAMMDDTHHMMSSZ.zip
python -m cauce_form_connector estado restaurar CARPETA\respaldo-forms-....zip --confirmar RESTAURAR
```

- **Contenido:** registro (con archivadas y `clave_id`), `huella.key`, `puesto.json` (identidad del puesto), ids de
  solicitud consumidos, `allowed_hosts.json` y perfiles. **No** incluye la credencial de Network Engine (secreto; DPAPI).
- **Protección:** el `.zip` se crea `0600`; en Windows la carpeta destino recibe la misma ACL que la de estado (usuario
  y SYSTEM). No va cifrado: **guárdelo en un medio cifrado** porque contiene `huella.key`.
- **Integridad:** manifiesto con SHA-256 de cada archivo y resumen del manifiesto entero; se verifica al crearlo y antes
  de restaurar. Detecta daños o cambios accidentales; no protege frente a quien reescriba respaldo y manifiesto a la vez.
- **Restaurar nunca desbloquea en silencio:** aparta lo actual (no lo borra), suma las operaciones actuales (misma
  clave), convierte `en_curso` en `incierta` y deja **siempre** revisión `GENERAL`. Si el registro actual usaba otra
  clave, la marca lo dice y lo apartado se revisa a mano. No se restaura con una interfaz abierta.

Pruebas: `test_respaldo_contiene_lo_necesario_sin_secretos_y_se_verifica`, `test_respaldo_alterado_no_se_restaura`
(4 variantes), `test_restaurar_un_respaldo_antiguo_no_desbloquea_lo_posterior`, `test_restaurar_tras_perder_la_carpeta_entera`,
`test_restaurar_con_un_registro_actual_de_otra_clave_lo_dice`, `test_no_se_restaura_con_forms_abierto`,
`test_orden_estado_restaurar_exige_confirmacion`.

## 6. Matriz de funcionalidades

| Funcionalidad | PR | En la candidata | Pasa por el registro | Evidencia |
|---|---|---|---|---|
| Autorrellenar desde expediente de Monitor (perfil configurable) | #3 | Sí | Sí (vía `preparar`) | `test_puente_monitor.py` |
| Analizador de formularios externos (solo lectura, escrituras bloqueadas) | #4 | Sí | No envía | `test_analizador_formularios.py`, `test_analisis_no_destructivo.py` |
| Listas JavaScript, fechas, Ext JS, ocho campos | #4 | Sí | No envía | `test_extjs_ocho_campos.py`, `test_listas_*.py`, `test_demo_ocho_campos.py` |
| Lanzadores de Windows sin `py` | #4 | Sí | — | CI `windows-bat` |
| La persona termina en la misma ventana | #5 | Sí | Sí (§3) | `test_tras_el_envio_humano_la_ventana_sigue_abierta…` |
| Diagnóstico nativo con Network Engine RC3 (credencial `session`) | #6 | Sí | — | `test_red_ne_rc3.py` |
| Registro durable y envío protegido | #6 | Sí | Es el registro | `test_operaciones.py`, `test_envio_protegido.py` |
| Compatibilidad con Monitor 7b92446 | #6 | Sí | — | `test_compat_monitor.py` |
| Envíos de la persona por JavaScript, en reposo y en `preparar` | rc2 | Sí | Sí | `test_consolidacion.py` |
| Revisión con resultado, una sola interfaz enviando | rc2 | Sí | Sí | `test_consolidacion.py` |
| Respaldo y restauración | rc2 | Sí | Sí | `test_consolidacion.py` |

## 7. Validación

| Ejecución | Dónde | Resultado |
|---|---|---|
| **Commit estable definitivo `08eeaa3`** (incluye A1 y E10): suite completa | contenedor Linux, con RC3 y E10 reales | **318 superadas**, 2 omitidas |
| **CI run `46120201645` sobre `08eeaa3`** · `ubuntu-latest` / `windows-latest` / `windows-bat` | GitHub | **319 superadas** (1 omitida) / **317 superadas** (3 omitidas) / verde; jornada corta `ok` |
| Suite completa sobre el merge (#5 + #6, sin cambios nuevos) | contenedor Linux | **289 superadas**, 2 omitidas |
| Suite completa de la candidata (`bd5c066`), incluye las 23 pruebas nuevas | contenedor Linux | **312 superadas**, 2 omitidas (permisos POSIX como root; ACL de Windows) · `evidence/rc2/pytest_local.txt` |
| CI run `46116869232` sobre `bd5c066` · `ubuntu-latest` | GitHub | **313 superadas**, 1 omitida (ACL de Windows); jornada corta `ok` |
| CI · `windows-latest` | GitHub (Windows Server del runner) | **311 superadas**, 3 omitidas (`kill -9`, permisos POSIX, SIGSTOP: no existen en Windows). Incluye ACL efectiva, DPAPI, respaldo y restauración |
| CI · `windows-bat` | GitHub | Instalador y analizador `.bat` sin `py`: verde |
| Integración con Network Engine RC3 `f93dd58` (proceso real, modo simulado) | Linux y CI | dentro de la suite: `test_red_ne_rc3.py` en verde |
| Compatibilidad con Monitor `7b92446` (su código real de lectura y revisiones) y contrato `9e7f88d` | Linux y CI | `test_compat_monitor.py` y `test_respuestas_aceptadas_por_el_registro_de_monitor` en verde |
| Navegador real | Chromium de Playwright, sin ventana, contra el portal propio | Todas las pruebas `browser` (envío protegido, envíos de la persona, `preparar`, Ext JS) |

Jornadas simuladas sobre `bd5c066` (los cambios de `08eeaa3` no alteran lo que se envía a RC3; en `08eeaa3` solo se repitió la jornada corta del CI) (`evidence/rc2/jornadas/`, 11,9 min, procesos reales: Forms, Network Engine RC3 y
portal ficticio; red simulada):

| Jornada | Fallos provocados | Recibidos | POST vistos | **Duplicados** | Confirmadas | Inciertas | Rechazadas | Bloqueos correctos (409) |
|---|---|---|---|---|---|---|---|---|
| 6 | corte después/antes, motor apagado, doble clic, Forms reiniciado | 5 | 8 | **0** | 4 | 2 | 0 | 3 |
| 10 | + red caída | 9 | 12 | **0** | 9 | 1 | 0 | 2 |
| 20 | + portal lento, capacidad, navegador cerrado | 19 | 23 | **0** | 15 | 4 | 1 | 5 |
| 50 | los mismos 9 tipos | 49 | 53 | **0** | 45 | 4 | 1 | 5 |

«POST vistos» incluye los reintentos que el portal reconoció por el token de un solo uso (no son trámites nuevos).
Mismas conclusiones que las jornadas de RC3: ningún duplicado y todo lo dudoso queda `incierta`.

**No se repitió** la prueba prolongada de 1 h (la de RC3, sobre `367ed54`, sigue siendo la referencia). Los cambios de
esta candidata que afectan al consumo son el bombeo de eventos en reposo (una llamada de 1 ms cada 0,1 s); no se midió
su efecto en CPU durante horas. **No es** una validación en Windows 11 físico ni con red física.

## 8. Coordinación con E10

Aviso enviado al responsable de E10: último commit de código del PR #6 = `fdd099a`; candidata consolidada = ver
manifiesto. Cambios de contrato **visibles para Monitor**: ninguno en `cauce.conector.v1` (mismos estados y códigos).
Cambios de comportamiento que debe conocer:

1. `preparar` puede responder `detenido_intervencion_humana` / `INTERVENCION_HUMANA_REQUERIDA` sin abrir el navegador
   cuando el expediente tiene una operación que bloquea en Forms (stderr: `OPERACION_PREVIA <codigo>`).
2. `preparar` escribe en la carpeta de datos de Forms (`CAUCE_FORMS_DATA_DIR`, por defecto `state`) solo huella HMAC e
   ids opacos; stderr añade `OPERACION_REGISTRADA op_…` o `OPERACION_NO_REGISTRADA`.
3. Network Engine: resolver la marca **solo se anota** en Forms (`revision_red`); la operación sigue incierta, sigue
   bloqueando y la alerta no se quita hasta la revisión en el puesto (§13, A1).

Solicitudes (no implementadas en los otros motores):

- **F1 (Monitor, contrato):** un estado o código para «salió una escritura al sitio que no se puede atribuir a la
  persona» y uno propio para «expediente bloqueado por una operación previa» (hoy se usa `INTERVENCION_HUMANA_REQUERIDA`).
- **F2 (Monitor):** guardar el `solicitud_id` junto a `enviado_por_persona_observado` para cruzarlo con `ref_solicitud`.
- Siguen vigentes las solicitudes a Network Engine del informe RC3 (S1–S5 las lleva el otro agente).

## 9. Riesgos pendientes

1. **Portales reales sin protección contra repeticiones:** el reintento interno de Chromium puede duplicar. No usar ahí.
2. **Falsos positivos de envío humano:** una página que haga `POST` automáticos a su propio sitio (autoguardado,
   telemetría propia) marcará inciertas y bloqueará esos expedientes hasta revisión. Es el lado seguro, pero en un
   portal así Forms sería poco usable. Escrituras a **otros** sitios no se cuentan (un envío a otro dominio no se ve).
3. **Envíos que no pasan por la red del navegador** (p. ej. `navigator.sendBeacon` puede no pasar por el interceptor;
   WebSocket) no se detectan.
4. **`preparar` y la interfaz usan huellas distintas** (campos lógicos + formulario frente a columnas de la fila): el
   mismo expediente enviado por las dos vías no se cruza automáticamente.
5. **Respaldos sin cifrar** (contienen `huella.key`): medio cifrado obligatorio.
6. **Windows 11 físico sin probar:** ACL, DPAPI y lanzadores solo en el runner de GitHub.
7. **Escala:** > ~50.000 confirmadas, migrar el registro a SQLite.
8. **PR #8 de Network Engine:** pendiente de la conformidad final de Ronaldo.

## 10. Evidencia de seguridad

- Ninguna ruta de producción envía sin el registro: guarda de AST (`test_ninguna_ruta_de_produccion_envia_sin_el_registro_durable`).
- Sin datos personales en registro, respaldo ni salida de `preparar`: `test_el_archivo_no_guarda_datos_personales`,
  prueba de `preparar` (§3), `test_proceso_real_stdout_json_unico_sin_valores_y_sin_archivos`.
- Sin secretos en el respaldo: `test_respaldo_contiene_lo_necesario_sin_secretos_y_se_verifica`.
- Respaldo alterado no se restaura: 4 variantes.
- Credencial de Network Engine solo de ámbito `session`; Forms nunca resuelve marcas (RC3, sin cambios).
- Analizador: ninguna escritura sale durante el análisis (#4, `test_analisis_no_destructivo.py`).
- Portal de pruebas solo en 127.0.0.1; ningún portal externo se automatizó.

## 11. Instalación aislada (puesto de laboratorio)

Junto a la instalación operativa, sin tocarla:

1. `git clone https://github.com/dass-666/CAUCE-UNIVERSAL-CONEXION C:\cauce-lab\forms-rc2` y
   `git checkout claude/candidata-consolidada` (o el commit del manifiesto).
2. `instalar_windows.bat` dentro de esa carpeta: crea su propio `.venv` y su Chromium en `.pw-browsers` (no usa el del sistema ni cambia rutas globales).
3. Variables solo en esa consola: `set CAUCE_FORMS_DATA_DIR=C:\cauce-lab\forms-rc2\state` y `set CAUCE_STATE_DIR=%CAUCE_FORMS_DATA_DIR%`
   (carpeta de estado propia, **no** la de la instalación operativa).
4. Network Engine (opcional): credencial nueva de ámbito `session` para este puesto
   (`cauce token create --name forms-lab-rc2 --scope session --out ...`) y `set CAUCE_NE_SESSION_TOKEN_FILE=...`.
5. Portal de laboratorio: `python -m lab_portal.servidor --port 8790`.
6. Primer respaldo antes de trabajar: `python -m cauce_form_connector estado respaldar --destino D:\respaldos-forms-lab`.

Para volver atrás basta borrar `C:\cauce-lab\forms-rc2`: no deja nada fuera de esa carpeta (salvo la credencial que se
revoca en Network Engine).

## 12. Manifiesto

`docs/MANIFIESTO_FORMS_RC2.txt`: commits sobre `main`, dependencias fijadas y SHA-256 de cada archivo de código,
laboratorio, pruebas y CI.

## 13. A1 de E10 y contrato de sesiones E10 (Network Engine PR #16, `4f223a7`)

**A1 (gravedad alta): «resolver una marca de red en Network Engine convierte una operación incierta en revisada y
permite repetirla».**

| Versión de Forms | Resultado de la misma comprobación (incierta → marca vista → resolve en NE → ¿se puede enviar?) |
|---|---|
| `e55a45d` | **Presente**: pasa a `revisada` y el expediente se puede volver a enviar |
| `bd5c066` (0.2.0rc2 inicial) | Ya no permitía reenviar (`expediente_revisada`), pero aún cambiaba el estado a `revisada` y quitaba la alerta: el puesto habría declarado «nada pendiente» al motor |
| `08eeaa3` (definitiva) | **Resuelto del todo**: la revisión de red solo añade `revision_red` (id `rv_…`, huella de la nota, `confirma_recepcion: false`). La operación sigue `incierta`, sigue pidiendo revisión y bloqueando. Solo la revisión en el puesto (`operaciones revisar --resultado`) o un acuse verificable la cierran |

Pruebas: `test_a1_resolver_la_marca_en_network_engine_solo_anota` (registro, incluso si el motor dijera
`confirms_receipt: true`), `test_operador_resuelve_en_ne_y_forms_lo_ve_en_la_misma_sesion` (Network Engine RC3 real:
tras el resolve el expediente sigue bloqueado y Forms vuelve a entregar la marca) y
`test_a1_con_e10_ni_siquiera_un_resolve_aceptado_desbloquea` (E10 real).

**Contrato E10 integrado** (sin debilitar la protección local; con RC3 todo sigue igual):

| E10 | En Forms | Prueba (motor E10 real) |
|---|---|---|
| Latido con `local_review` | `pending` si el registro tiene inciertas o revisión general; `none` si no. Solo se envía si la vista de la sesión trae `local_review` (RC3 rechaza campos desconocidos con 400; comprobado que a RC3 no se le envía) | `test_forms_declara_local_review_pendiente_y_el_motor_no_deja_cerrar_la_marca` |
| `409 condition_persists` al resolver | Consecuencia: el operador no puede cerrar la marca mientras el puesto tenga inciertas | idem |
| `GET /sessions/{id}/reviews` | Se consulta al ver una resolución, solo para anotarla; se guarda id y huella de la nota, nunca la nota | `test_revision_en_el_puesto_y_despues_en_el_motor_queda_limpio_y_la_revision_de_red_no_confirma` |
| S1 `retry_after_s` | En `409 credential_has_session` Forms espera lo indicado; después la nueva sesión sustituye a la callada y hereda la marca | `test_s1_forms_pierde_su_id_y_recupera_la_sesion_heredando_la_marca` |
| Cliente | `heartbeat(local_review)` y `reviews()` copiados de `client.py` E10, con huellas en `red/PIN.txt` (`e10-sha256`) | `test_pin_e10_coincide_con_el_motor` |

**Hallazgos A2–A8:** el documento `SOLICITUDES_RONALDO_E10.md` no está en el repositorio de Network Engine ni en la
carpeta de este proyecto; no se pudieron comprobar uno por uno. Ver el aviso a E10.
