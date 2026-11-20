# Arquitectura

## Decisión: Python + Playwright

| Opción | A favor | En contra |
|---|---|---|
| **Python + Playwright (elegida)** | Excel/CSV nativos (`openpyxl`, `csv`); un único lenguaje para análisis, motor e interfaz; instalación sencilla en Windows con `venv`; Playwright para Python tiene las mismas capacidades que el de Node | La API síncrona de Playwright no es segura entre hilos → se resuelve con un hilo dedicado (`BrowserWorker`) |
| Node + Playwright | Playwright nace en Node; mismo lenguaje que el detector JS | Excel requiere librerías extra; dos ecosistemas si Cauce Monitor es Python |
| Selenium | Muy extendido | Esperas manuales, peor soporte de páginas dinámicas, sin acceso CDP sencillo |

## Flujo

```
Fuente (HTML pegado / archivo / URL autorizada)
   │            └─ url_policy: solo localhost, file:// o dominios permitidos + declaración
   ▼
Inspector (Playwright)  ── espera red + estabilidad del DOM (MutationObserver)
   │      └─ HTML pegado: sin red (todas las peticiones abortadas)
   ▼
detector.js (en la página) → form_detector.py → AnalysisResult
   │      └─ SafetyReport: CAPTCHA / meta automation / data-human-only ⇒ bloqueado
   ▼
data_adapter (JSON/CSV/XLSX) → matcher → Mapping[] (auto / revisar / sin_datos)
   ▼
Revisión humana en la interfaz (corrige, guarda perfil)
   ▼
engine.fill_form  ── por pasadas: tras cada select/radio/checkbox se re-escanea
   │      ├─ sequential_typer: 1 tecla por carácter (keyboard.press / CDP para ñ, á…)
   │      └─ validator: valor real, checkValidity, errores visibles, confirmaciones
   ▼
FillReport  ── se detiene: NO pulsa enviar ni Enter
   ▼
Confirmación humana (persona envía, o escribe ENVIAR)
```

## Por qué así

- **Detector en el navegador:** el mismo código sirve para HTML pegado y URL, y ve el DOM real tras el JavaScript.
  El analizador estático (BeautifulSoup) queda como vía rápida sin Chromium y produce la misma estructura.
- **Escritura:** `keyboard.press` por carácter genera `keydown → keypress → input → keyup`. Para caracteres fuera
  de la distribución EE. UU. (ñ, á, ¿) Playwright solo emite `input`; por eso se usa `Input.dispatchKeyEvent` (CDP),
  que genera los cuatro eventos. Comprobado en `tests/test_sequential_typing.py`.
- **Relleno por pasadas:** los formularios dinámicos muestran campos según lo elegido. Un campo que aparece y no fue
  revisado solo se rellena si su correspondencia automática tiene confianza ≥ 0,80; si no, se informa como pendiente.
- **Parada antes de enviar:** el motor no tiene ninguna ruta que pulse el botón de envío. La única función que lo hace
  (`submit_with_human_confirmation`) exige la frase `ENVIAR` y está bloqueada en páginas con CAPTCHA.
- **Auditoría:** lista blanca de claves; nunca se guardan valores; las URLs pierden query y fragmento.
- **Interfaz local:** escucha en 127.0.0.1 y rechaza peticiones con `Host` u `Origin` ajenos (CSRF / DNS rebinding).

## IA opcional

`matcher.AmbiguityResolver` es una interfaz con un método `suggest(field, columns)`. Se llama solo para campos sin
correspondencia y su sugerencia siempre queda en estado «revisar». No hay ninguna implementación incluida ni
dependencia de APIs de pago. Ver pendientes en `LIMITACIONES.md`.
