"""Controles «cuadro de texto + lista desplegable» (combos editables): Ext JS 3.x ComboBox, ARIA combobox,
`<input list>` con datalist y componentes parecidos. Sin reglas por URL: se reconocen por su DOM (ver `detector.js`).

Estrategia (en este orden; cada paso se verifica, nunca se da por bueno porque el texto sea visible):
1. Si el campo admite escritura, se teclea el dato carácter por carácter (aunque tenga flecha de lista).
2. Si aparecen opciones: se busca la coincidencia EXACTA (sin distinguir mayúsculas, acentos ni espacios) y se
   elige con un clic real. Una sola coincidencia exacta; si hay cero o varias, el campo queda pendiente.
3. Si no aparecen opciones, el campo se trata como texto libre y se comprueba que conserva el valor al salir.
4. La verificación lee, además del texto, el VALOR INTERNO que registró el control: campo oculto hermano (en Ext JS,
   `hiddenName`) y, si existe el componente Ext, `getValue()`/`findRecord` coherentes. Texto visible sin valor
   interno ⇒ `pendiente_revision`.
5. Descubrir el catálogo (`discover_catalogs`) abre la lista pero no elige ni escribe nada.

Límites conocidos (documentados en docs/LIMITACIONES.md): listas que cargan solo por páginas, controles sin campo
oculto ni componente legible (solo se comprueba que el texto se conserva tras salir del campo), iframes y Shadow DOM.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .form_detector import strip_accents
from .models import FieldDescriptor

# Elementos que las bibliotecas usan para las opciones de una lista abierta (solo se consideran los VISIBLES).
OPTION_CSS = ", ".join([
    '[role="option"]', ".x-combo-list-item", ".select2-results__option", ".ui-menu-item", ".autocomplete-suggestion",
    ".dropdown-menu li", ".dropdown-item", ".tt-suggestion", ".Select-option", '[class*="combo-list-item"]',
])
MAX_OPTIONS = 200
MAX_OPTIONS_SEEN = 50
FIRST_WAIT_MS = 1600      # Ext JS en modo remoto espera ~500 ms tras la última tecla antes de consultar
STABLE_MS = 300

AUTH_NAME_RE = re.compile(r"(^|[_\-.\[\s])(pass(word|wd|code)?|contrase\w*|clave[_ ]?de[_ ]?acceso|otp|token|captcha)($|[_\-.\]\s])", re.I)
CONSENT_RE = re.compile(r"(acept|consent|autoriz|t[eé]rminos|terms|privacidad|privacy|gdpr|pol[ií]tica|conforme)", re.I)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(s or "").lower()).strip()


def protected_reason(f: FieldDescriptor, *, automatic: bool = False) -> str:
    """Motivo por el que NO se escribe en el campo ('' si se puede). Autenticación: siempre. Consentimiento: en la
    estrategia automática (campos sin correspondencia aprobada por una persona)."""
    if f.type == "password" or f.autocomplete in ("current-password", "new-password", "one-time-code"):
        return "campo de autenticación: el conector no lo escribe"
    if AUTH_NAME_RE.search(f"{f.name} {f.id}"):
        return "campo de autenticación: el conector no lo escribe"
    if automatic and f.type in ("checkbox", "radio") and CONSENT_RE.search(f"{f.label} {f.name} {f.id}"):
        return "campo de consentimiento: solo lo marca una persona o una correspondencia aprobada"
    return ""


# ---------------------------------------------------------------- lectura de opciones
def _options_locator(page: Any):
    return page.locator(OPTION_CSS).filter(visible=True)


def read_visible_options(page: Any, limit: int = MAX_OPTIONS) -> list[dict[str, str]]:
    loc = _options_locator(page)
    try:
        n = min(loc.count(), limit)
    except Exception:
        return []
    out = []
    for i in range(n):
        el = loc.nth(i)
        try:
            text = re.sub(r"\s+", " ", el.inner_text(timeout=500) or "").strip()
            val = el.evaluate("e => e.getAttribute('data-value') || e.getAttribute('data-id') || e.getAttribute('value') || ''")
        except Exception:
            continue
        if text:
            out.append({"label": text, "value": val or "", "index": str(i)})
    return out


def wait_for_options(page: Any, first_wait_ms: int = FIRST_WAIT_MS, stable_ms: int = STABLE_MS) -> list[dict[str, str]]:
    """Espera a que aparezca (y se estabilice) una lista de opciones. Vacío si no aparece ninguna."""
    waited, last, stable = 0, None, 0
    while waited <= first_wait_ms + 3000:
        cur = read_visible_options(page)
        sig = [o["label"] for o in cur]
        if cur and sig == last:
            stable += 100
            if stable >= stable_ms:
                return cur
        else:
            stable = 0
        last = sig if cur else None
        if not cur and waited >= first_wait_ms:
            return []
        page.wait_for_timeout(100)
        waited += 100
    return read_visible_options(page)


def datalist_options(page: Any, f: FieldDescriptor) -> list[dict[str, str]]:
    js = """(el) => { const id = el.getAttribute('list'); const dl = id && document.getElementById(id);
        return dl ? Array.from(dl.options).map(o => ({label: (o.label || o.value || '').trim(), value: o.value || ''})) : []; }"""
    try:
        return [o for o in page.locator(f.selector).first.evaluate(js) if o["label"]][:MAX_OPTIONS]
    except Exception:
        return []


def exact_matches(options: list[dict[str, str]], target: str) -> list[dict[str, str]]:
    t = norm(target)
    return [o for o in options if t and (norm(o["label"]) == t or (o.get("value") and norm(o["value"]) == t))]


# ---------------------------------------------------------------- relleno
def fill_combo(typer: Any, f: FieldDescriptor, target: str) -> dict[str, Any]:
    """Teclea `target` y, si aparece la lista, elige la opción exacta. Devuelve el dict de `SequentialTyper.apply`
    más `selection`, `options_seen`, `option_value` y, si queda dudoso, `pending`."""
    page = typer.page
    n = typer.type_chars(f, target)
    opts = wait_for_options(page)
    out: dict[str, Any] = {"method": "combo_texto", "keystrokes": n, "applied": target, "selection": "texto_libre",
                           "options_seen": [], "option_value": "", "pending": "",
                           "insert_text_fallbacks": typer.last_insert_text_fallbacks}
    if opts:
        out["options_seen"] = [o["label"] for o in opts][:MAX_OPTIONS_SEEN]
        found = exact_matches(opts, target)
        if len(found) == 1:
            chosen = found[0]
            _options_locator(page).nth(int(chosen["index"])).click()
            page.wait_for_timeout(150)
            out.update(method="combo_opcion", selection="opcion", applied=chosen["label"], option_value=chosen["value"])
        elif not found:
            out["pending"] = "aparecieron opciones pero ninguna coincide exactamente con el dato: elegir o aprobar una equivalencia"
            _escape(page)
        else:
            out["pending"] = "varias opciones coinciden exactamente con el dato: elegir una"
            _escape(page)
    typer.leave_field(f)
    page.wait_for_timeout(250)      # el control procesa el blur (Ext JS: assertValue); se verifica DESPUÉS, no durante
    return out


def _escape(page: Any) -> None:
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


# ---------------------------------------------------------------- verificación del valor interno
READ_COMBO_JS = """
(el, valueSel) => {
  const hidden = valueSel ? document.querySelector(valueSel) : null;
  let ext = null;
  try {
    if (window.Ext && Ext.getCmp && el.id) {
      const c = Ext.getCmp(el.id);
      if (c && typeof c.getValue === 'function') {
        const v = c.getValue();
        let consistent = null;
        if (c.store && c.displayField && typeof c.findRecord === 'function') {
          const rec = c.findRecord(c.displayField, c.getRawValue());
          if (rec && c.valueField) consistent = String(rec.get(c.valueField)) === String(v);
        }
        ext = { value: v === undefined || v === null ? '' : String(v), valid: typeof c.isValid === 'function' ? c.isValid(true) : null,
                consistent, forceSelection: !!c.forceSelection };
      }
    }
  } catch (e) { ext = null; }
  const invalidClass = /\\b(x-form-invalid|is-invalid|has-error)\\b/.test(el.className || '');
  return { text: el.value, hasHidden: !!hidden, internal: hidden ? hidden.value : '', ext, invalidClass };
}
"""


def read_combo(page: Any, f: FieldDescriptor) -> dict[str, Any]:
    return page.locator(f.selector).first.evaluate(READ_COMBO_JS, f.value_selector or None)


def judge_combo(f: FieldDescriptor, st: dict[str, Any], expected: str, selection: str, option_value: str) -> tuple[str, str, str]:
    """(estado, detalle, valor_interno). Estados: ok | ok_normalizado | diferente | invalido | pendiente_revision."""
    actual = st["text"]
    internal = st["internal"] if st["hasHidden"] else (st["ext"]["value"] if st.get("ext") else "")
    if actual == expected:
        status, detail = "ok", ""
    elif norm(actual) == norm(expected) and actual.strip():
        status, detail = "ok_normalizado", "difiere solo en mayúsculas, acentos o espacios"
    else:
        return ("pendiente_revision", "el control no conservó el valor esperado (puede exigir elegir una opción de la lista): "
                "revisar", internal)
    ext = st.get("ext")
    if st["hasHidden"] and not (st["internal"] or "").strip():
        return "pendiente_revision", "el texto es visible pero el control no registró valor interno (campo oculto vacío)", internal
    if ext is not None and not str(ext["value"]).strip():
        return "pendiente_revision", "el texto es visible pero el componente no tiene valor (getValue vacío)", internal
    if ext is not None and ext.get("consistent") is False:
        return "pendiente_revision", "el valor interno del componente no corresponde al registro de la lista", internal
    if selection == "opcion" and option_value and st["hasHidden"] and (st["internal"] or "") != option_value:
        return "pendiente_revision", "el valor interno no coincide con el valor declarado por la opción elegida", internal
    if ext is not None and ext.get("valid") is False:
        return "invalido", "el componente marca el campo como no válido", internal
    if st["invalidClass"]:
        return "invalido", "la página marca el campo como no válido", internal
    return status, detail, internal


# ---------------------------------------------------------------- catálogo
def discover_catalogs(session: Any, fields: Optional[list[FieldDescriptor]] = None) -> dict[str, list[str]]:
    """Abre la lista de cada combo editable y devuelve {clave_del_campo: [etiquetas]}. NO elige ni escribe nada:
    solo hace clic en la flecha (o en el cuadro) y cierra con Escape. Los campos protegidos se omiten."""
    page = session.page
    fields = fields if fields is not None else session.inspect().all_fields
    out: dict[str, list[str]] = {}
    for f in fields:
        if f.widget != "combo" or not f.visible or not f.fillable or protected_reason(f):
            continue
        labels = [o["label"] for o in datalist_options(page, f)]
        if not labels:
            try:
                if f.trigger_selector:
                    page.locator(f.trigger_selector).first.click()
                else:
                    page.locator(f.selector).first.click()
                    page.keyboard.press("ArrowDown")
                labels = [o["label"] for o in wait_for_options(page)]
                _escape(page)
                page.wait_for_timeout(100)
            except Exception:
                labels = []
        out[f.key] = labels[:MAX_OPTIONS]
    return out
