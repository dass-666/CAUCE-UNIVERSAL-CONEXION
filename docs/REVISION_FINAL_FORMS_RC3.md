# Revisión final · Forms 0.2.0rc1 (candidata de laboratorio)

Fecha: 2026-11-28 · Rama `claude/integracion-nativa-rc3` · PR #6 en borrador, sin fusionar.
Complementa a [`INFORME_INTEGRACION_NATIVA_FORMS_RC3.md`](INFORME_INTEGRACION_NATIVA_FORMS_RC3.md). Todo se probó con el portal
propio de laboratorio y datos ficticios; Network Engine y Monitor no se modificaron.

## Resumen

La revisión encontró **tres defectos reales** en la protección contra duplicados y los corrigió. Además dejó medido y
documentado un **límite** que Forms no puede resolver solo: los reintentos internos de Chromium.

| # | Punto | Resultado | Gravedad del hallazgo |
|---|---|---|---|
| 1 | Reintentos internos de Chromium | Límite medido y documentado con una prueba | **Alta en portales reales** sin token de un solo uso |
| 2 | Rutas de envío fuera del registro | **Defecto corregido**: el envío directo de la persona en la ventana no se registraba | Alta |
| 3 | `huella.key` y permisos en Windows | **Defecto corregido** (pérdida silenciosa de la protección) + ACL efectiva en Windows | Alta / Media |
| 4 | Crecimiento de `operaciones.json` | **Defecto corregido**: las confirmadas antiguas se borraban y perdían el bloqueo | Alta a medio plazo |
| 5 | Conflictos con #3, #4 y #5 | #3 sin conflictos; #4 y #5 con conflictos de texto en 2 archivos | Media (orden de fusión) |
| 6 | CI del último commit | Verde en Linux y Windows (`fdd099a`) | — |

## 1. Reintentos internos de Chromium

**Medido:** si la conexión reutilizada se corta antes de que llegue la respuesta, Chromium **repite el POST por su
cuenta**. En este contenedor ocurrió 6 de 6 veces (3 cortes antes y 3 después de registrar); en el runner Linux del CI,
con la misma versión, **no** ocurrió: depende de si la conexión se reutilizó. Cuando ocurre, el reintento **no aparece**
en los eventos de red de Playwright: Forms ve 1 petición y el portal recibe 2 (`posts_vistos` = 1). Es decir, no es
predecible ni detectable desde Forms.

- Con el portal propio (token de un solo uso) no hay duplicado: el portal reconoce el mismo formulario.
- Con un portal **sin** esa protección hay **un trámite duplicado que Forms no puede ver ni impedir**. Lo demuestra
  `test_limite_portal_sin_idempotencia_el_reintento_interno_de_chromium_duplica` (modo de laboratorio sin idempotencia).
- La garantía de Forms es: **un solo clic por aprobación y una sola operación registrada**. No llega más abajo de la red.
- Se añadió `posts_vistos` a cada operación: detecta peticiones repetidas por el propio JavaScript de la página, no las
  de Chromium.

**Recomendación:** no usar Forms para envíos en portales reales sin acuse verificable y sin protección contra
repeticiones. Una alternativa técnica (enviar la petición desde Playwright con `route.fetch`, que no reintenta) cambia
cómo se habla con el portal y **no conviene ahora**: añade complejidad y riesgo sin un portal real autorizado donde probarla.

## 2. Rutas de envío y registro durable

| Ruta | ¿Pasa por el registro? |
|---|---|
| Interfaz, botón ENVIAR (`/api/submit` → `enviar_protegido`) | Sí, antes del clic |
| **La persona pulsa el botón del portal en la ventana** (sin ENVIAR) | **No pasaba. Corregido**: ahora queda `incierta` (`envio_humano_directo_sin_acuse`), se anula la aprobación de esa fila y el expediente queda bloqueado. Prueba: `test_envio_directo_de_la_persona_en_la_ventana_queda_en_el_registro` (falla sin la corrección). |
| CLI (`fill`, `analyze`, `propose`, `preparar`, `operaciones`) | No envían; comprobado por `test_ninguna_ruta_de_produccion_envia_sin_el_registro_durable` |
| `submit_with_human_confirmation` (función antigua, sin registro) | Solo la usan las pruebas H1; la misma prueba falla si un módulo de producción la usa |
| `preparar` (contrato con Monitor): la persona envía en la ventana | Se observa y se informa a Monitor (`envio_humano_observado`); **no** se anota en el registro de Forms. Hoy el responsable de no repetir es Monitor. |

Límite: el aviso de envío directo depende del evento `submit` del formulario. Un portal que envía por JavaScript
(`fetch`) sin formulario no lo dispara: ese envío no quedaría registrado.

## 3. `huella.key` y permisos efectivos en Windows

Defectos encontrados y corregidos:

- **Perder o dañar `huella.key` desactivaba la protección en silencio.** Se creaba otra clave y las huellas de lo ya
  enviado dejaban de casar: esos expedientes podían enviarse otra vez. Ahora el registro guarda el identificador de la
  clave (resumen SHA-256 corto, no la clave); si no coincide, se pide una revisión `GENERAL` y no se envía nada. Una clave
  dañada se aparta (no se borra). Prueba: `test_perder_huella_key_no_desactiva_en_silencio_la_proteccion`.
- **Dos procesos podían crear claves distintas a la vez** (`O_TRUNC`). Ahora se crea con `O_EXCL` y el segundo usa la del primero.
- **Si la clave no se podía guardar,** Forms seguía con una clave solo en memoria. Ahora no envía.

Permisos en Windows: `0o600` en Windows **no** protege (solo marca «solo lectura»). Los permisos reales son la ACL
heredada; en una carpeta creada en `C:\` suele incluir a «Usuarios autenticados». Forms ahora quita la herencia de su
carpeta de estado y deja acceso solo al usuario actual y a SYSTEM (`icacls`, sin shell). Prueba solo para Windows:
`test_windows_permisos_efectivos_solo_usuario_y_system` (se ejecuta en el runner Windows del CI, no en Windows 11 físico).

Límites que siguen: un **administrador** del equipo puede leer o borrar la carpeta; el mismo usuario de Windows (y
cualquier programa suyo) también. Si alguien borra `huella.key`, ahora se nota y se bloquea, pero no se recuperan las
huellas antiguas: el operador debe revisar a mano lo enviado antes. **Conviene incluir la carpeta de estado en las copias
de seguridad.**

## 4. Crecimiento de `operaciones.json` y archivado

Defecto encontrado y corregido: al pasar de 500 operaciones cerradas (`MAX_CERRADAS`), las más antiguas **se borraban,
también las confirmadas**, y ese expediente podía enviarse otra vez. Con 50 envíos al día habría ocurrido a los 10 días.
La prueba prolongada (330 operaciones) no llegó a ese límite.

Ahora, cuando una confirmada sale de la lista, su huella pasa a `confirmadas_archivadas` (huella y fecha; sin datos
personales). Esa lista nunca se recorta, está cubierta por el mismo resumen SHA-256 (borrarla a mano se detecta) y sigue
bloqueando. Prueba: `test_confirmadas_que_salen_de_la_lista_siguen_bloqueando`.

Tamaño: unos 45 bytes por expediente confirmado archivado; 10.000 son unos 450 KB que se reescriben en cada cambio.
Suficiente para un puesto de laboratorio. **Por encima de ~50.000 expedientes** conviene pasar a SQLite (pendiente, no ahora).

## 5. Compatibilidad con los PR #3, #4 y #5

`git merge --no-commit` de cada rama sobre el último commit de la candidata (nada se publicó):

| PR | Rama | Conflictos | Interacción de comportamiento |
|---|---|---|---|
| #3 | `integracion-monitor-autorrelleno` | Ninguno | Solo rellena; no envía. |
| #4 | `analizador-formularios-externos` | `tests.yml` (2 bloques), `page_inspector.py` (1 bloque) | Durante el análisis bloquea toda petición que no sea GET: compatible con el registro. |
| #5 | `persona-termina-en-la-ventana` | Los mismos dos archivos | Cambia `preparar` para que la persona termine en la ventana. Ese envío humano se informa a Monitor, no al registro de Forms (ver §2). Después de fusionar #5 conviene decidir si `preparar` también lo anota en el registro. |

Los conflictos son de código añadido en los dos lados (atributos de `BrowserSession` y pasos de CI): se resuelven
conservando ambos. #4 y #5 comparten base, así que el orden recomendado es **#5, luego #4, luego esta candidata**.

## 6. CI definitiva

Run `46112498578` sobre `fdd099a` (último commit de código): **verde en los dos sistemas**.

| Runner | Resultado | Omitidas (motivo) |
|---|---|---|
| ubuntu-latest | 197 passed, 1 skipped; jornada de CI `ok` | ACL de Windows (solo Windows) |
| windows-latest | 196 passed, 2 skipped | permisos POSIX; SIGSTOP no existe en Windows |

Los dos commits anteriores (`13baee4`, `23928de`) fallaron en Windows con `Permission denied` en `operaciones.vivo` y
`operaciones.lock`: `icacls /T` quitaba la herencia también a cada archivo. `fdd099a` aplica la ACL solo a la carpeta,
una vez (marcador `.acl_aplicada`). `13baee4` además falló en Linux por la prueba de reintento de Chromium, que ahora
comprueba solo lo invariable (§1). Los commits posteriores a `fdd099a` solo tocan documentación.

## Riesgos pendientes

1. **Portales reales sin protección contra repeticiones:** el reintento interno de Chromium puede duplicar (§1). No usar ahí.
2. **Envíos por JavaScript sin evento `submit`** no se registran si los hace la persona en la ventana (§2).
3. **`preparar`:** el envío humano lo registra Monitor, no Forms (§2, §5).
4. **Copias de seguridad** de la carpeta de estado: sin `huella.key` se bloquea, pero lo anterior se revisa a mano (§3).
5. **Windows 11 físico** sin probar: la ACL se comprobó en el runner Windows del CI, no en el equipo de Ronaldo.
6. **Escala:** más allá de ~50.000 confirmadas, migrar el registro a SQLite (§4).
7. **PR #8 de Network Engine:** no lo cerré. Lo aprobaste «sujeto a la conformidad de Ronaldo»; si me confirmas que lo
   cierre, lo cierro como sustituido, sin borrar su rama ni su historial.
