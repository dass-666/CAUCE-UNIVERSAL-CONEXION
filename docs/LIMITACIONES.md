# Informe de limitaciones y funciones pendientes

Fecha: 2026-11-20 · Versión 0.1.0 · Revisión de seguridad: [SEGURIDAD.md](SEGURIDAD.md)

## 1. Qué está verificado y cómo

| Afirmación | Evidencia | Entorno |
|---|---|---|
| Detecta campos, etiquetas, tipos, validaciones, opciones, obligatorios | `tests/test_html_analyzer.py` (8 pruebas) | Linux, Python 3.13 |
| Detecta campos creados por JavaScript y los que aparecen al elegir opciones | `tests/test_lab_forms_e2e.py::test_lab2_*` | Linux, Chromium headless |
| Cada carácter genera keydown/keypress/input/keyup, incluidos ñ y acentos | `tests/test_sequential_typing.py` y `evidence/informe_demo.md` | Linux, Chromium headless |
| El conector no asigna `.value` en campos de texto | Contador de asignaciones = 0 en el laboratorio 1 | Linux, Chromium headless |
| Las confirmaciones de correo y teléfono coinciden y se detectan las que no | `test_lab3_*` | Linux, Chromium headless |
| Se detiene antes de enviar; nunca pulsa Enter en campos de una línea | `not_submitted` en todas las pruebas e2e | Linux, Chromium headless |
| Bloquea páginas con CAPTCHA (simulado) | `test_captcha_page_is_not_filled` | Linux |
| Auditoría sin datos personales | `test_audit_log_contains_no_personal_data` | Linux |
| La interfaz funciona de extremo a extremo | `tests/test_web_app.py::test_ui_end_to_end` + `evidence/ui_revision.png` | Linux, Chromium headless |

| Aislamiento del HTML importado, redirecciones, API frente a otros sitios | `tests/test_security.py` (6 pruebas) | Linux |
| Ejemplo de integración con Cauce Monitor sin envío ni datos persistidos | `tests/test_ejemplo_cauce_monitor.py` | Linux |

Resultado de referencia: **70 pruebas superadas**, demo `DEMO SUPERADA` y ejemplo `EJEMPLO SUPERADO`, en Linux local, en GitHub Actions (`ubuntu-latest` y `windows-latest`) y en un PC con Windows 11 (recorrido con ventana visible incluido).

## 2. No verificado todavía

| Punto | Riesgo | Cómo verificarlo |
|---|---|---|
| Windows en otros equipos | Bajo: verificado en CI y en un PC Windows 11; otros equipos pueden tener antivirus o políticas que bloqueen Chromium | `instalar_windows.bat`, `probar_windows.bat`, `python scripts\recorrido_ui.py` |
| Formularios reales de terceros | Alto: cada sitio es distinto | Solo con autorización; empezar con perfiles guardados y revisión campo a campo |
| Rendimiento con formularios grandes (>100 campos) | Medio: el re-escaneo por pasadas es O(n²) en el peor caso | Medir con un laboratorio grande |
| Firefox/WebKit | Bajo: no hay CDP, los acentos solo emiten `input` | Probar con `p.firefox` si se necesita |

## 3. Limitaciones conocidas (por diseño o por alcance)

1. **iframes:** no se analizan campos dentro de iframes (se avisa). Muchos pagos y CAPTCHA viven en iframes.
2. **Shadow DOM:** el detector usa `querySelectorAll`, que no entra en shadow roots. Componentes web (p. ej. Salesforce LWC, algunos Ionic) no se detectan.
3. **Controles personalizados:** selectores tipo React-Select, calendarios JavaScript, editores enriquecidos y `contenteditable` no se detectan como campos. Un `div` que imita un desplegable no es un `<select>`.
4. **Archivos adjuntos (`type=file`):** se omiten a propósito.
5. **Fechas nativas:** `type=date` se rellena con `fill` (mecanismo nativo), no carácter por carácter, tal como pide la regla para calendarios. Una fecha en un `type=text` con máscara sí se teclea.
6. **Claves de campos sin `name` ni `id`:** se numeran por posición; si la página inserta campos antes, la clave puede cambiar entre pasadas y el perfil guardado no casará.
7. **Campos que aparecen durante el relleno:** solo se rellenan si la confianza automática es ≥ 0,80 y la columna está libre; si no, quedan como «omitido – revisar».
8. **Diccionario de conceptos:** cubre campos comunes en español e inglés (nombre, apellidos, correo, teléfono, documento, dirección, CP, ciudad, país, empresa, cargo, fechas…). Formularios técnicos o de un sector concreto necesitarán corrección manual la primera vez (luego se guarda el perfil).
9. **Detección anti-bot heurística:** busca reCAPTCHA, hCaptcha, Turnstile, `data-sitekey`, textos típicos y `<meta name="automation" content="none">`. Puede no detectar sistemas invisibles (puntuación de comportamiento) y puede dar falsos positivos. Ante la duda, bloquea.
10. **Interfaz local de una sola persona:** sin usuarios ni contraseña. Protegida solo por escuchar en 127.0.0.1 y por el filtro de `Host`/`Origin`. **No la exponga en la red.**
11. **Excel:** se lee la primera hoja; las fórmulas se leen con su último valor calculado (si el archivo nunca se abrió en Excel, pueden aparecer vacías).
12. **Velocidad:** con 35 ms por tecla, 100 caracteres tardan ~3,5 s. Es deliberado y configurable; no se añaden pausas aleatorias porque eso sería técnica de evasión.
13. **HTML pegado:** se ejecutan sus scripts en línea dentro de Chromium, pero sin acceso a red. Si el HTML depende de recursos externos (CSS/JS de un CDN) no cargarán: use la URL autorizada o el análisis estático.

## 3 bis. Analizador de formularios externos (`analizar-formulario`)

Verificado (pruebas `tests/test_analizador_formularios.py`, solo con páginas de laboratorio locales y datos ficticios):
detección de texto, numéricos, selectores, listas y opciones, fechas nativas y en texto, confirmaciones, validaciones
visibles, campos creados por JavaScript y bloques condicionales; solo una petición GET a la página y ningún POST;
el perfil borrador lo acepta `cargar_formulario` y, tras aprobarlo, `autorrellenar` rellena 7 de 7 campos del lab 03.

Límites:
1. **Expediente de ejemplo:** la comparación usa un expediente FICTICIO (`sample_data/expediente_ficticio*.json`). El formato
   real de los expedientes de Cauce Monitor no está confirmado; con otro formato cambian las rutas propuestas.
2. **Solo lo que se ve al cargar:** campos que aparecen tras elegir una opción, pasar de página (asistentes por pasos) o
   iniciar sesión no se ven, porque el analizador no interactúa. Un bloque oculto ya presente se marca «oculto condicional».
3. **«Creado por JavaScript»** compara con el HTML que sirvió el servidor; si no se pudo leer, se avisa y no se marca.
4. **Mensajes de validación:** solo los textos asociados por `aria-describedby`, `title` y atributos HTML (`pattern`,
   `min/max`, `maxlength`…). Las reglas que viven solo en JavaScript no se ven hasta que la página muestra el error.
5. **Decisiones conservadoras:** documento de identidad, consentimientos, campos condicionales, listas cuyo valor no encaja,
   fechas en texto y datos que no cumplen el `pattern` van a «revisar»; archivos, contraseñas y CAPTCHA, a «humano».
6. **Sitios que bloquean robots o exigen sesión:** no se intenta eludir nada. Si el sitio responde con un bloqueo o pide
   iniciar sesión, el informe saldrá vacío o con el formulario de acceso.
7. **Windows:** verificado solo en GitHub Actions (`windows-latest`); `analizar_formulario_windows.bat` no se ha probado aún
   en el PC de Windows 11.

## 4. Riesgos legales y de uso (cuestionamiento del requisito de URL)

Analizar y rellenar páginas de terceros por URL es la parte de mayor riesgo del proyecto:

- Muchos términos de servicio prohíben la automatización. La lista de dominios permitidos y la casilla de autorización
  **no constituyen cumplimiento legal**: solo evitan el uso accidental.
- Rellenar con datos personales de terceros (aunque vengan de un Excel propio) implica tratamiento de datos: requiere base
  legal y, según el país, aviso de privacidad (en México, LFPDPPP).
- Recomendación: limitar el uso por URL a sistemas propios o a portales con acuerdo escrito, y registrar en Cauce Monitor quién
  autorizó cada dominio y cuándo.

## 5. Funciones pendientes (priorizadas)

| Prioridad | Función | Valor | Coste estimado | Recomendación |
|---|---|---|---|---|
| Alta | — | (Prueba física en Windows completada en v0.1) | — | — |
| Alta | Soporte de iframes del mismo origen | Muchos formularios reales los usan | Medio (1–2 días) | Siguiente iteración |
| Media | Soporte de shadow DOM abierto | Formularios con componentes web | Medio | Cuando aparezca un caso real |
| Media | Rellenar varias filas en lote con aprobación por fila | Productividad | Medio | Después de validar con un caso real |
| Media | `/api/v1` + token local + cola de trabajos | Integración con Cauce Monitor | Medio | Cuando se decida el modo de integración |
| Baja | Resolvedor de IA opcional (modelo local p. ej. vía Ollama, o API de pago) | Reduce correcciones manuales en formularios raros | Bajo de programar; coste por uso si es de pago | Solo si las correcciones manuales resultan frecuentes; los perfiles guardados ya cubren formularios repetidos |
| Baja | Controles personalizados (React-Select, datepickers) mediante «adaptadores» por librería | Cobertura | Alto (uno por librería) | Bajo demanda |
| Baja | Empaquetado `.exe` (PyInstaller) | Instalación sin Python | Medio; Chromium pesa | Solo si hay usuarios no técnicos |
