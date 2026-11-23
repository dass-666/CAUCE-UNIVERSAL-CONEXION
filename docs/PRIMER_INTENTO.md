# Primer intento supervisado con un formulario externo (Windows)

Objetivo: rellenar un formulario externo AUTORIZADO con datos SINTÉTICOS, revisarlo en la ventana y cerrarla.
**Nadie pulsa «Enviar».** El conector nunca envía.

## Antes de empezar (humano)
1. Autorización escrita del sitio para automatizar ese formulario. Anote una referencia corta: `aut_<sitio>_<fecha>`.
2. Confirme que el formulario no tiene CAPTCHA ni exige iniciar sesión (si lo tiene, el conector se detiene).
3. Use solo `sample_data\expediente_monitor_sintetico.json` (o una copia con datos inventados).

## Pasos (cmd, carpeta aislada)
```
git clone -b claude/analizador-formularios-externos https://github.com/dass-666/CAUCE-UNIVERSAL-CONEXION D:\cauce-ucf-intento1
cd /d D:\cauce-ucf-intento1
instalar_windows.bat
```
1. **Permitir el dominio:** edite `config\allowed_hosts.json` → `"allowed_hosts": ["tramites.ejemplo.gob.mx"]` (solo el dominio).
2. **Analizar (solo lectura):** `analizar_formulario_windows.bat "https://tramites.ejemplo.gob.mx/cita"` → responda **S**.
   Se abre `vista_previa.html`. Revise cada campo, marque solo lo correcto, llene las tablas de equivalencias
   (trámite, sucursal…) y pulse «Descargar perfil revisado» (queda en Descargas como `perfil_….json`). Cópielo como `D:\cauce-ucf-intento1\perfil_intento1.json`.
3. **Aprobar y rellenar (ventana visible, 5 min para revisar):**
```
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\.pw-browsers"
.venv\Scripts\python -m cauce_form_connector autorrellenar --formulario perfil_intento1.json --expediente sample_data\expediente_monitor_sintetico.json --aprobar-perfil rev_ronaldo --authorized --autorizacion aut_<sitio>_<fecha>
```
4. Revise la ventana: campos correctos, confirmaciones iguales, nada en RFC/CURP/consentimientos. **Cierre la ventana
   sin enviar.** En la consola debe aparecer una línea `[OK]` por campo y «El conector no envía».

## Si algo falla
| Mensaje | Qué significa | Qué hacer |
|---|---|---|
| `URL no autorizada … lista de permitidos` | Dominio no está en `allowed_hosts.json` | Añadirlo (paso 1) |
| `URL no autorizada … --authorized` | Sitio externo sin declarar | Añadir `--authorized` (y el dominio en la lista de permitidos); `--autorizacion aut_…` es opcional |
| `La página exige intervención humana` | CAPTCHA o marca «solo humanos» | Detener: no se elude |
| `Error de configuración: …` | Perfil o tabla inválidos (el motivo se muestra) | Corregir en la vista previa y repetir |
| `estructura_cambiada` / `PERFIL_NO_COINCIDE` | El sitio cambió entre aprobar y rellenar (p. ej. listas de horarios que cambian) | Volver a analizar y aprobar; si cambia cada vez, ese campo debe quedar para la persona |
| `[!!]` en un campo | El sitio rechazó o transformó el valor | Revisar en la ventana; corregir el perfil |
