"""Validador de resultados.

Después de escribir, lee de la página el valor REAL de cada campo y lo compara
con el esperado. También recoge la validación HTML5 (`checkValidity`) y los
mensajes de error visibles que la propia página muestre junto al campo.
"""
from __future__ import annotations

import re
from typing import Any

from .form_detector import strip_accents
from .models import FieldDescriptor, FieldResult

READ_STATE_JS = """
(el) => {
  const tag = el.tagName.toLowerCase();
  const type = (el.type || tag).toLowerCase();
  let value;
  if (type === 'checkbox') value = el.checked ? 'true' : 'false';
  else if (tag === 'select') value = Array.from(el.selectedOptions).map(o => o.value).join('|');
  else value = el.value;
  // Mensaje de error de la propia página: aria-describedby, aria-errormessage o hermano .error
  let pageError = '';
  const ids = ((el.getAttribute('aria-describedby') || '') + ' ' + (el.getAttribute('aria-errormessage') || '')).trim();
  for (const id of ids.split(/\\s+/).filter(Boolean)) {
    const n = document.getElementById(id);
    if (n && n.offsetParent !== null && /error|invalid|incorrect|no coincide|obligatorio/i.test(n.className + ' ' + n.textContent)) {
      pageError = n.textContent.trim();
    }
  }
  if (!pageError && el.parentElement) {
    const n = el.parentElement.querySelector('.error, .invalid-feedback, [role=alert]');
    if (n && n.offsetParent !== null) pageError = n.textContent.trim();
  }
  return { value, valid: el.checkValidity ? el.checkValidity() : null,
           message: el.validationMessage || '', pageError,
           ariaInvalid: el.getAttribute('aria-invalid') === 'true' };
}
"""

READ_RADIO_JS = """
(name) => {
  const els = Array.from(document.querySelectorAll(`input[type=radio][name="${name}"]`));
  const c = els.find(e => e.checked);
  return { value: c ? c.value : '', valid: els.length ? els[0].checkValidity() : null,
           message: els.length ? els[0].validationMessage : '', pageError: '', ariaInvalid: false };
}
"""


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def compare(f: FieldDescriptor, expected: str, actual: str) -> tuple[str, str]:
    """Devuelve (estado, detalle)."""
    if actual == expected:
        return "ok", ""
    if f.type in ("tel", "number") or f.inputmode in ("tel", "numeric"):
        if _digits(actual) and _digits(actual) == _digits(expected):
            return "ok_normalizado", "la página aplicó un formato (máscara); los dígitos coinciden"
    if (f.type == "email" or "@" in expected) and actual.strip().lower() == expected.strip().lower():
        return "ok_normalizado", "correo: la página " + transformacion(expected, actual)
    if actual.strip() == expected.strip():
        return "ok_normalizado", "difiere solo en espacios al inicio/fin"
    if f.maxlength is not None and expected.startswith(actual) and len(actual) == f.maxlength:
        return "diferente", f"el campo limita a {f.maxlength} caracteres (maxlength) y truncó el dato"
    if strip_accents(actual) == strip_accents(expected) and actual != expected:
        return "diferente", "se perdieron acentos u otros caracteres especiales"
    return "diferente", "el valor registrado no coincide: la página " + transformacion(expected, actual)


def transformacion(expected: str, actual: str) -> str:
    """Describe qué cambió la página, SIN mostrar ningún valor (solo tipo de cambio y longitudes)."""
    if not actual:
        return "dejó el campo vacío"
    if actual == expected.lower():
        return "convirtió el texto a minúsculas"
    if actual == expected.upper():
        return "convirtió el texto a mayúsculas"
    if actual.lower() == expected.lower():
        return "cambió mayúsculas/minúsculas"
    if actual.strip() == expected.strip():
        return "quitó espacios al inicio o al final"
    if actual.lower().strip() == expected.lower().strip():
        return "cambió mayúsculas/minúsculas y quitó espacios al inicio o al final"
    if re.sub(r"\s+", "", actual) == re.sub(r"\s+", "", expected):
        return "cambió los espacios internos"
    if _digits(actual) and _digits(actual) == _digits(expected):
        return "cambió el formato (mismos dígitos)"
    return f"cambió el valor ({len(expected)} → {len(actual)} caracteres)"


def read_state(page: Any, f: FieldDescriptor) -> dict[str, Any]:
    if f.type == "radio":
        return page.evaluate(READ_RADIO_JS, f.name)
    return page.locator(f.selector).first.evaluate(READ_STATE_JS)


def verify_field(page: Any, f: FieldDescriptor, result: FieldResult, expected_applied: str) -> FieldResult:
    try:
        st = read_state(page, f)
    except Exception as exc:  # el campo desapareció tras otra interacción
        result.status, result.detail = "error", f"no se pudo leer el campo: {type(exc).__name__}"
        return result
    result.actual = st["value"]
    result.valid_html5 = st["valid"]
    result.validation_message = st["message"]
    result.page_error = st["pageError"]
    status, detail = compare(f, expected_applied, st["value"])
    if status.startswith("ok") and (st["valid"] is False or st["pageError"] or st["ariaInvalid"]):
        status = "invalido"
        detail = st["pageError"] or st["message"] or "la página marca el campo como no válido"
    result.status, result.detail = status, detail or result.detail
    return result


def check_confirmations(fields: list[FieldDescriptor], results: dict[str, FieldResult]) -> list[dict[str, Any]]:
    """Comprueba que cada campo de confirmación coincide con su original."""
    out = []
    by_key = {f.key: f for f in fields}
    for f in fields:
        if not f.confirms:
            continue
        a, b = results.get(f.confirms), results.get(f.key)
        entry = {"original": f.confirms, "confirmacion": f.key,
                 "label": by_key[f.confirms].label if f.confirms in by_key else f.confirms}
        if a is None or b is None:
            entry.update(match=False, detail="uno de los dos campos no se rellenó")
        else:
            correo = "@" in a.actual and "@" in b.actual      # los correos no distinguen mayúsculas en la práctica
            igual = a.actual.strip().lower() == b.actual.strip().lower() if correo else a.actual == b.actual
            entry.update(match=(igual and a.actual != ""),
                         detail=("coinciden" if a.actual == b.actual else "coinciden salvo mayúsculas/espacios")
                         if igual and a.actual else "NO coinciden")
        out.append(entry)
    return out
