"""Listas desplegables hechas con JavaScript (con o sin atributos ARIA).

Reglas comunes a detección, relleno y verificación, para que las tres vean el mismo control:
- contenedor: el ancestro cercano (hasta 3 niveles) que tiene UN solo input oculto (el valor interno del control);
- lista: la indicada por aria-controls/aria-owns, si no la [role=listbox] del contenedor, si no el propio contenedor;
- opciones: [role=option], si no [data-value], si no <li>, si no elementos con clase «option/item»;
- valor de una opción: data-value, si no su id, si no su texto.
Si las opciones solo aparecen al abrir la lista, se obtienen con un clic normal (`sondear_opciones`) y se cierra
con Escape. Nunca se escribe en el input oculto: se elige la opción con un clic, como una persona.
"""
from __future__ import annotations

from typing import Any

from .models import FieldOption

COMUN_JS = r"""
const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
const visible = (e) => { if (!e.isConnected) return false; const st = getComputedStyle(e);
  if (st.display === "none" || st.visibility === "hidden") return false; const r = e.getBoundingClientRect();
  return r.width > 0 && r.height > 0; };
const contenedor = (el) => { for (let n = el.parentElement, k = 0; n && k < 3; n = n.parentElement, k++) {
  if (n.querySelectorAll("input[type=hidden]").length === 1) return n; } return el.parentElement; };
const oculto = (el) => el.dataset.input ? document.getElementById(el.dataset.input)
  : (contenedor(el) ? contenedor(el).querySelector("input[type=hidden]") : null);
const lista = (el) => document.getElementById(el.getAttribute("aria-controls") || "")
  || document.getElementById(el.getAttribute("aria-owns") || "")
  || (contenedor(el) ? contenedor(el).querySelector("[role=listbox]") || contenedor(el) : null);
const SELS = ["[role=option]", "[data-value]", "li", "[class*=option i]", "[class*=item i]"];
const opciones = (raiz, el) => { if (!raiz) return [];
  for (const s of SELS) { const xs = Array.from(raiz.querySelectorAll(s)).filter((o) => o !== el && !el.contains(o)
      && !o.contains(el) && !o.matches("input,select,textarea") && clean(o.textContent));
    if (xs.length) return xs; } return []; };
const valor = (o) => o.getAttribute("data-value") || o.id || clean(o.innerText || o.textContent);
"""

# Opciones visibles ahora mismo (dentro de la lista o, si la página las pinta fuera, las nuevas tras abrir)
OPCIONES_VISIBLES_JS = "(el) => {" + COMUN_JS + r"""
  let xs = opciones(lista(el), el).filter(visible);
  if (!xs.length && window.__cauce_previas) {
    for (const s of SELS) { xs = Array.from(document.querySelectorAll(s)).filter((o) => visible(o)
        && !window.__cauce_previas.has(o) && !el.contains(o) && clean(o.textContent)); if (xs.length) break; }
  }
  return xs.map((o) => ({ value: valor(o), label: clean(o.innerText || o.textContent), selected: false }));
}"""

MARCAR_PREVIAS_JS = "(el) => {" + COMUN_JS + r"""
  window.__cauce_previas = new Set(SELS.flatMap((s) => Array.from(document.querySelectorAll(s)).filter(visible)));
  return window.__cauce_previas.size;
}"""

ESTA_ABIERTA_JS = "(el) => {" + COMUN_JS + r""" return opciones(lista(el), el).some(visible); }"""

# Opción a pulsar: por valor y, si no, por texto exacto; primero en la lista del control y luego entre las nuevas visibles
BUSCAR_OPCION_JS = "([el, v, etiqueta]) => {" + COMUN_JS + r"""
  const ok = (o) => valor(o) === v || (etiqueta && clean(o.innerText || o.textContent) === etiqueta);
  let o = opciones(lista(el), el).find(ok);
  if (!o && window.__cauce_previas) {
    for (const s of SELS) { o = Array.from(document.querySelectorAll(s)).find((x) => visible(x)
        && !window.__cauce_previas.has(x) && !el.contains(x) && ok(x)); if (o) break; }
  }
  return o || null;
}"""

# ¿Qué reconoce el propio control? Coherencia entre opción marcada (ARIA o clase selected/active), valor interno y texto
LEER_SELECCION_JS = "(el) => {" + COMUN_JS + r"""
  const xs = opciones(lista(el), el), h = oculto(el);
  const usaAria = xs.some((o) => o.hasAttribute("aria-selected"));
  let v = "", ok = true, msg = "";
  if (usaAria) {
    const m = xs.filter((o) => o.getAttribute("aria-selected") === "true");
    if (m.length !== 1) { ok = false; msg = "el control no marca exactamente una opción como seleccionada"; } else v = valor(m[0]);
  } else if (h) { v = h.value;
  } else { ok = false; msg = "el control no expone qué opción está seleccionada (sin valor interno ni marca ARIA)"; }
  if (ok && h && h.value !== v) { ok = false; msg = "el valor interno del control no coincide con la opción marcada"; }
  const e = xs.find((o) => valor(o) === v);
  if (ok && v && e && !clean(el.innerText || el.textContent).includes(clean(e.innerText || e.textContent))) {
    ok = false; msg = "el control no muestra la opción elegida"; }
  if (ok && !v) { ok = false; msg = "el control no tiene ninguna opción seleccionada"; }
  return { value: v, valid: ok, message: msg, pageError: "", ariaInvalid: el.getAttribute("aria-invalid") === "true" };
}"""


def cerrar(page: Any, loc: Any) -> None:
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        if loc.evaluate(ESTA_ABIERTA_JS):
            loc.click()
            page.wait_for_timeout(150)
    except Exception:
        pass


def abrir(page: Any, loc: Any) -> None:
    loc.evaluate(MARCAR_PREVIAS_JS)
    loc.scroll_into_view_if_needed()
    loc.click()
    page.wait_for_timeout(250)


def sondear_opciones(page: Any, selector: str) -> list[FieldOption]:
    """Abre la lista con un clic normal, lee las opciones visibles y la vuelve a cerrar. No elige nada."""
    loc = page.locator(selector).first
    try:
        abrir(page, loc)
        crudo = loc.evaluate(OPCIONES_VISIBLES_JS)
    except Exception:
        return []
    finally:
        cerrar(page, loc)
    vistos, out = set(), []
    for o in crudo:
        if o["value"] and o["value"] not in vistos:
            vistos.add(o["value"])
            out.append(FieldOption(value=o["value"], label=o["label"], selected=False))
    return out


def completar_opciones(page: Any, campos: list) -> None:
    """Para las listas personalizadas cuyas opciones no están en la página hasta abrirlas."""
    for f in campos:
        if f.type == "custom-select" and not f.options and f.visible and f.fillable:
            f.options = sondear_opciones(page, f.selector)
