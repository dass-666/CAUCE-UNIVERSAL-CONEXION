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

Resultado de referencia: **70 pruebas superadas**, demo `DEMO SUPERADA` y ejemplo `EJEMPLO SUPERADO`, en Linux local y en GitHub Actions (`ubuntu-latest` y `windows-latest`).

## 2. No verificado todavía

| Punto | Riesgo | Cómo verificarlo |
|---|---|---|
| **Windows en un PC real** | Bajo-medio: `.bat`, permisos de PowerShell, antivirus. (Pruebas y demo ya pasan en GitHub Actions `windows-latest`) | Ejecutar `instalar_windows.bat` y `probar_windows.bat` |
| Modo con ventana visible (`headless=False`) | Bajo | `python scripts/demo_lab.py --headed` |
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
| Alta | Prueba manual en un PC Windows (CI `windows-latest` ya en verde) | Confirma instalación con `.bat` | Bajo (minutos) | Ahora |
| Alta | Soporte de iframes del mismo origen | Muchos formularios reales los usan | Medio (1–2 días) | Siguiente iteración |
| Media | Soporte de shadow DOM abierto | Formularios con componentes web | Medio | Cuando aparezca un caso real |
| Media | Rellenar varias filas en lote con aprobación por fila | Productividad | Medio | Después de validar con un caso real |
| Media | `/api/v1` + token local + cola de trabajos | Integración con Cauce Monitor | Medio | Cuando se decida el modo de integración |
| Baja | Resolvedor de IA opcional (modelo local p. ej. vía Ollama, o API de pago) | Reduce correcciones manuales en formularios raros | Bajo de programar; coste por uso si es de pago | Solo si las correcciones manuales resultan frecuentes; los perfiles guardados ya cubren formularios repetidos |
| Baja | Controles personalizados (React-Select, datepickers) mediante «adaptadores» por librería | Cobertura | Alto (uno por librería) | Bajo demanda |
| Baja | Empaquetado `.exe` (PyInstaller) | Instalación sin Python | Medio; Chromium pesa | Solo si hay usuarios no técnicos |
