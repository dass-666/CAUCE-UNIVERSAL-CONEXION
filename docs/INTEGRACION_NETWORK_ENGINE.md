# Integración nativa con Cauce Network Engine RC3

Versión de Forms: 0.2.0rc1 (candidata, sin publicar) · Network Engine: RC3 `f93dd58` · Monitor: `7b92446`.
Informe completo con pruebas y métricas: [`INFORME_INTEGRACION_NATIVA_FORMS_RC3.md`](INFORME_INTEGRACION_NATIVA_FORMS_RC3.md).

## Qué hace y qué no

Forms puede **informar** a Network Engine del estado de su sesión de navegador (latidos `ok`/`error` y si hay una
operación en curso) y **leer** lo que el motor sabe de la conectividad. Con eso la interfaz muestra un «Estado del puesto».

- Network Engine es **opcional**. Sin configurar, Forms funciona igual y dice «sin diagnóstico de red» (no «sin internet»).
- Forms **no** cambia rutas, conexiones ni nada de la red. **No** resuelve marcas «revisar a mano»: eso lo hace el
  operador (Network Engine o el centro de revisiones de Monitor).
- Forms usa **solo** una credencial de ámbito `session`. Nunca la de administración, ni la `read`/`review` de Monitor.

## Instalación aislada (Windows 11)

Forms **no** instala Network Engine: lleva una copia mínima y fijada del cliente (`cauce_form_connector/red/cliente_ne.py`,
origen y huellas en `red/PIN.txt`, comprobadas por `tests/test_red_cliente_pin.py`).

1. Instale Forms en su propia carpeta y su propio entorno (`instalar_windows.bat`), como siempre.
2. El **administrador** del motor crea la credencial (en la carpeta de Network Engine, con su Python):

   ```
   python -m cauce token create --name forms-puesto-1 --scope session --out C:\CauceForms\ne-session.token
   ```

   En Windows el archivo queda cifrado con DPAPI para el usuario que lo creó: **créela con el mismo usuario de Windows
   que ejecutará Forms**. Una credencial = una sesión viva: un puesto, una credencial.
3. Configure Forms (variables de entorno, por ejemplo en el `.bat` de arranque):

   | Variable | Valor | Por omisión |
   |---|---|---|
   | `CAUCE_NE_SESSION_TOKEN_FILE` | ruta del archivo anterior | vacío = sin Network Engine |
   | `CAUCE_NE_PORT` | puerto local del motor | `8780` |
   | `CAUCE_NE_CONNECTION_ID` | conexión del motor a vigilar (opcional) | ninguna |
   | `CAUCE_NE_LATIDO_S` | segundos entre latidos (mín. 0,55) | `7` |
   | `CAUCE_FORMS_DATA_DIR` | carpeta de estado de Forms (registro de operaciones, identidad del puesto) | `state` |
   | `CAUCE_FORMS_PUESTO` | etiqueta fija del puesto (opcional; sin datos personales) | `forms-` + 8 hex al azar |

4. Arranque Forms. La sección «Estado del puesto» debe decir «Network Engine conectado».

Si el motor está apagado, la credencial fue revocada o no tiene el ámbito correcto, Forms lo muestra y **sigue funcionando**.

## Estados que se muestran

| Indicador | Valores |
|---|---|
| Network Engine | conectado · no configurado · no disponible (sin diagnóstico) · credencial no válida (401) · credencial con permisos incorrectos (403) · limitado (429) · sesión ocupada por otro proceso (409) |
| Conectividad | verificada · degradada · perdida · desactualizada (el dato envejeció) · desconocida · sin diagnóstico |
| Sesión | iniciada · activa · pérdida de conectividad · recuperada · requiere intervención · incierta · interrumpida · cerrada |
| Operación | sin operación · en curso · resultado desconocido / revisión pendiente |

## Envío protegido (cada acción confirmada se ejecuta como máximo una vez)

1. Antes del clic, Forms escribe en disco (`operaciones.json`, con copia `.bak` y resumen SHA-256) una operación
   `en_curso`. Si no puede escribir, **no envía**.
2. Solo se da por **confirmada** con un acuse verificable del portal (en el laboratorio: `GET /api/acuse/{token}` en
   127.0.0.1). Un clic observado **no** es una recepción.
3. Si no hay acuse, el resultado es **incierto**: Forms no reenvía y bloquea ese expediente (y uno confirmado) hasta
   que un operador lo revise. Reinicios, recuperación de la red, credencial revocada o sesión cerrada **no** lo quitan.
4. La huella del expediente es un HMAC con una clave local aleatoria (`huella.key`): el archivo no contiene datos personales.

### Revisión del operador

```
python -m cauce_form_connector operaciones listar
python -m cauce_form_connector operaciones revisar op_xxxxxxxxxxxx --nota "qué comprobó" --confirmar op_xxxxxxxxxxxx --resultado no_recibido
```

`--resultado` es obligatorio (0.2.0rc2): `recibido` (el portal lo tiene: el expediente sigue bloqueado), `no_recibido`
(comprobado que no llegó: se podrá enviar otra vez) o `sin_determinar` (quita la alerta y sigue bloqueado). Solo
`no_recibido` desbloquea. La revisión queda auditada. Si el registro se dañó, Forms pide una revisión `GENERAL`
(`--confirmar GENERAL`). La marca del motor la resuelve el operador del motor (`cauce` o el centro de revisiones de
Monitor). Si Forms ve que el operador resolvió la marca **de su misma sesión** en Network Engine, quita la alerta, pero
el expediente sigue bloqueado hasta que alguien deje el resultado con esta orden.

### Envíos de la persona fuera de ENVIAR (0.2.0rc2)

Si la persona envía desde la ventana (botón del portal, tecla Intro o un botón que usa JavaScript sin evento `submit`),
Forms lo detecta por el evento `submit` **o** por cualquier petición `POST/PUT/PATCH/DELETE` al mismo sitio del
formulario que Forms no originó. La anota como `incierta` **antes** de dejarla salir; si no puede anotarla, la corta.
En el modo `preparar` (Monitor) se anota igual, con el `solicitud_id` (`ref_solicitud`) para cruzarla con el registro
de Monitor, y un expediente bloqueado no se vuelve a preparar (`detenido_intervencion_humana`).

### Respaldo y restauración

```
python -m cauce_form_connector estado respaldar --destino D:\respaldos-forms
python -m cauce_form_connector estado verificar D:\respaldos-forms\respaldo-forms-AAAAMMDDTHHMMSSZ.zip
python -m cauce_form_connector estado restaurar D:\respaldos-forms\respaldo-forms-....zip --confirmar RESTAURAR
```

El respaldo incluye registro (con archivadas), `huella.key`, `puesto.json`, ids de solicitud consumidos, lista de
sitios y perfiles; no incluye la credencial de Network Engine. Restaurar aparta lo actual (no lo borra), suma las
operaciones actuales y deja siempre una revisión `GENERAL`: nada se envía hasta que un operador revise lo ocurrido
desde la fecha del respaldo. Guarde los respaldos en un medio cifrado: contienen `huella.key`.

## Laboratorio

- Portal ficticio propio: `python -m lab_portal.servidor --port 8790` (solo 127.0.0.1; fallos deliberados en `/_lab/*`).
- Jornadas: `python scripts/jornada_laboratorio.py --ne-repo RUTA_RC3 --expedientes 6 10 20 50`.
- Prueba prolongada: `... --duracion-min 60`. Capturas: `python scripts/capturas_rc3.py --ne-repo RUTA_RC3`.
- Paso 09b con Forms nativo (propuesta para Network Engine): `scripts/paso_09b_nativo.py`.
- Pruebas contra el motor real: `CAUCE_NE_REPO=RUTA_RC3 pytest tests/test_red_ne_rc3.py`; con Monitor además
  `CAUCE_MONITOR_REPO=RUTA_MONITOR pytest tests/test_compat_monitor.py`.

Todo lo anterior usa datos ficticios, la red **simulada** del motor y el portal propio. No es una validación física.
