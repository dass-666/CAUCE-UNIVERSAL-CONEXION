"""Listas desplegables hechas con JavaScript (con o sin ARIA, con o sin <select> nativo detrás).

Principio: no se depende de la estructura HTML de ninguna librería. Se actúa como una persona y se observa:
- ABRIR: clic normal en el control. Antes se anota qué elementos eran visibles; las opciones son lo que APARECE.
- OPCIONES: entre lo nuevo, los elementos con marca de opción ([role=option], [data-value], <li>, clase option/item...)
  o, si no hay ninguna marca, el grupo más numeroso de hermanos nuevos con texto.
- ELEGIR: clic normal en la opción (por valor o por su texto exacto). Si la lista tiene buscador y la opción no está
  a la vista (listas largas o virtualizadas), se teclea el texto de la opción y se vuelve a mirar.
- VERIFICAR: qué registró el formulario. Por orden de fuerza: el <select> nativo que el widget sustituye; el input
  oculto del widget; la opción marcada con aria-selected; y siempre que el control muestre la opción elegida.
Nunca se asigna un valor por programa (ni al select ni al input oculto) y nunca se pulsa un botón de envío.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import FieldOption

COMUN_JS = r"""
const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
const norm = (s) => clean(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
const cls = (e) => (e.getAttribute && e.getAttribute("class")) || "";
// Visible de verdad: ni display/visibility/opacity, ni fuera de pantalla, ni recortado por un ancestro (clip, clip-path,
// alto cero con overflow oculto), que son las formas habituales de «esconder» una lista cerrada.
const recortado = (n) => { const st = getComputedStyle(n);
  if (st.opacity === "0" || st.visibility === "hidden" || st.display === "none") return true;
  if (st.clip && st.clip !== "auto" && /rect\(0(px)?[, ]+0(px)?[, ]+0(px)?[, ]+0(px)?\)/.test(st.clip)) return true;
  if (st.clipPath && /inset\((100|50)%/.test(st.clipPath)) return true;
  if ((st.overflowY === "hidden" || st.overflowX === "hidden")) { const r = n.getBoundingClientRect(); if (r.height <= 1 || r.width <= 1) return true; }
  return false; };
const visible = (e) => { if (!e || !e.isConnected) return false;
  const r = e.getBoundingClientRect();
  if (!(r.width > 1 && r.height > 1 && r.right > 0 && r.bottom > 0)) return false;
  for (let n = e; n && n !== document.documentElement; n = n.parentElement) { if (recortado(n)) return false; }
  return true; };
const texto = (o) => clean(o.innerText || o.textContent);
// <select> nativo que este widget sustituye (hermano o descendiente de un ancestro cercano, y no visible)
const sustituido = (x) => !visible(x) || x.getBoundingClientRect().width <= 4 || /hidden-accessible|visually-hidden|sr-only/i.test(cls(x));
const nativo = (el) => { for (let n = el, k = 0; n && k < 6 && n.tagName !== "FORM" && n !== document.body; n = n.parentElement, k++) {
    for (const c of [n.previousElementSibling, n.nextElementSibling]) { if (c && c.tagName === "SELECT" && sustituido(c)) return c; }
    const dentro = n.querySelectorAll ? Array.from(n.querySelectorAll("select")) : [];
    if (dentro.length === 1) return sustituido(dentro[0]) ? dentro[0] : null;
    if (dentro.length > 1) return null;
  } return null; };
// contenedor del widget: ancestro cercano con un único input oculto, o el padre
const contenedor = (el) => { for (let n = el.parentElement, k = 0; n && k < 4 && n.tagName !== "FORM" && n !== document.body; n = n.parentElement, k++) {
    if (n.querySelectorAll("input[type=hidden],input[aria-hidden=true]").length === 1 && n.querySelectorAll("[role=combobox]").length <= 1) return n; }
  return el.parentElement || el; };
const oculto = (el) => { if (el.dataset && el.dataset.input) return document.getElementById(el.dataset.input);
  const c = contenedor(el); return c ? c.querySelector("input[type=hidden],input[aria-hidden=true]") : null; };
const SELS = ["[role=option]", "[data-value]", "li", "[class*=option i]", "[class*=item i]", "[class*=result i]", "[class*=choice i]"];
const util = (o, el) => o !== el && !o.contains(el) && !o.matches("input,select,textarea,option,script,style") && texto(o)
  && o.getAttribute("aria-disabled") !== "true" && !/(^|[\s_-])(disabled|placeholder)($|[\s_-])/i.test(cls(o)) && o.getAttribute("data-value") !== "";
const hojas = (xs) => xs.filter((o) => !xs.some((p) => p !== o && o.contains(p)));
const valor = (o) => o.getAttribute("data-value") || texto(o);
// elementos de opción visibles: los que han aparecido desde que se abrió la lista (o, si nada es nuevo, los de la lista ARIA)
const candidatos = (el) => {
  const previas = window.__cauce_previas || new Set();
  const nuevos = Array.from(document.querySelectorAll("body *")).filter((o) => !previas.has(o) && visible(o) && util(o, el));
  for (const s of SELS) { const xs = hojas(nuevos.filter((o) => o.matches(s))); if (xs.length) return xs; }
  // sin marcas: el grupo más numeroso de hermanos nuevos con texto propio
  const porPadre = new Map();
  nuevos.filter((o) => o.children.length === 0 || texto(o).length < 80).forEach((o) => { const p = o.parentElement;
    if (!porPadre.has(p)) porPadre.set(p, []); porPadre.get(p).push(o); });
  let mejor = [];
  porPadre.forEach((xs) => { if (xs.length >= 2 && xs.length > mejor.length) mejor = xs; });
  if (mejor.length) return hojas(mejor);
  const lb = document.getElementById(el.getAttribute("aria-controls") || "") || document.getElementById(el.getAttribute("aria-owns") || "");
  return lb ? hojas(Array.from(lb.querySelectorAll("[role=option]")).filter((o) => visible(o) && util(o, el))) : [];
};
"""

MARCAR_PREVIAS_JS = "(el) => {" + COMUN_JS + r"""
  window.__cauce_previas = new Set(Array.from(document.querySelectorAll("body *")).filter(visible));
  return window.__cauce_previas.size; }"""

OPCIONES_VISIBLES_JS = "(el) => {" + COMUN_JS + r"""
  return candidatos(el).map((o) => ({ value: valor(o), label: texto(o), selected: false })); }"""

BUSCAR_OPCION_JS = "([el, v, etiqueta]) => {" + COMUN_JS + r"""
  const xs = candidatos(el), n = norm(etiqueta);
  return xs.find((o) => o.getAttribute("data-value") === v) || xs.find((o) => texto(o) === etiqueta)
    || xs.find((o) => norm(texto(o)) === n || norm(o.getAttribute("title") || o.getAttribute("aria-label") || "") === n) || null; }"""

# ¿El elemento con el foco admite teclear para filtrar? (buscador de la lista)
FOCO_ESCRIBIBLE_JS = r"""() => { const a = document.activeElement;
  return !!a && a.matches("input:not([readonly]):not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea") && !a.disabled; }"""

# Qué registró el formulario. `opts`: opciones conocidas [{value,label}] (del select nativo o descubiertas al abrir).
LEER_SELECCION_JS = "([el, opts]) => {" + COMUN_JS + r"""
  const res = (v, ok, msg) => ({ value: v, valid: ok, message: msg, pageError: "", ariaInvalid: el.getAttribute("aria-invalid") === "true" });
  const c = contenedor(el), sel = nativo(el);
  const mostrado = clean((el.matches("input") ? el.value + " " : "") + texto(el) + " " + (c ? texto(c) : ""));
  if (sel) {   // el propio formulario guarda el valor en su <select>: es la evidencia más fuerte
    const o = sel.selectedOptions[0];
    if (!o || o.value === "") return res("", false, "el formulario no registró ninguna opción en su lista interna");
    if (mostrado && !norm(mostrado).includes(norm(o.text))) return res(o.value, false, "el control no muestra la opción que registró el formulario");
    return res(o.value, true, "");
  }
  const lb = document.getElementById(el.getAttribute("aria-controls") || "") || document.getElementById(el.getAttribute("aria-owns") || "")
    || (c ? c.querySelector("[role=listbox]") : null);
  const marcadas = lb ? Array.from(lb.querySelectorAll("[role=option][aria-selected=true]")) : [];
  const propio = clean((el.matches("input") ? el.value : "") || texto(el));
  const m = norm(mostrado);
  let o = opts.find((x) => norm(x.label) === norm(propio)) || (marcadas.length === 1 && opts.find((x) => x.value === valor(marcadas[0]) || norm(x.label) === norm(texto(marcadas[0]))))
    || opts.filter((x) => x.label && m.includes(norm(x.label))).sort((a, b) => b.label.length - a.label.length)[0];
  const h = oculto(el);
  if (!o && h && h.value) o = opts.find((x) => x.value === h.value);
  if (!o) return res("", false, opts.length ? "el control no muestra ninguna de sus opciones como elegida" : "no se conocen las opciones del control para comprobar la selección");
  if (h) {
    if (!h.value) return res(o.value, false, "el control muestra la opción pero su valor interno está vacío");
    if (h.value !== o.value && o.value !== o.label) return res(h.value, false, "el valor interno del control no coincide con la opción mostrada");
  }
  if (marcadas.length > 1) return res(o.value, false, "el control marca más de una opción como seleccionada");
  if (marcadas.length === 1 && valor(marcadas[0]) !== o.value && norm(texto(marcadas[0])) !== norm(o.label)
      && norm(marcadas[0].getAttribute("aria-label") || marcadas[0].getAttribute("title") || "") !== norm(o.label))
    return res(o.value, false, "la opción marcada por el control no es la que muestra");
  return res(o.value, true, "");
}"""


def _cache(page: Any) -> dict:
    c = getattr(page, "_cauce_listas", None)
    if c is None:
        c = {}
        setattr(page, "_cauce_listas", c)
    return c


def recordar(page: Any, clave: str, opciones: list[FieldOption]) -> None:
    if opciones:
        _cache(page)[clave] = opciones


def opciones_conocidas(page: Any, f: Any) -> list[FieldOption]:
    return list(f.options) or _cache(page).get(f.key, [])


def leer_seleccion(page: Any, f: Any) -> dict:
    loc = page.locator(f.selector).first
    opts = [{"value": o.value, "label": o.label} for o in opciones_conocidas(page, f)]
    return page.evaluate(LEER_SELECCION_JS, [loc.element_handle(), opts])


def _visibles(loc: Any) -> list[FieldOption]:
    vistos, out = set(), []
    for o in loc.evaluate(OPCIONES_VISIBLES_JS):
        if o["value"] and o["value"] not in vistos:
            vistos.add(o["value"])
            out.append(FieldOption(value=o["value"], label=o["label"], selected=False))
    return out


def clic(page: Any, loc: Any) -> None:
    """Clic normal. Si otro elemento del propio widget tapa el punto (envoltorios), se pulsa en esas coordenadas,
    que es lo que haría una persona con el ratón."""
    try:
        loc.click(timeout=2500)
    except Exception:
        caja = loc.bounding_box()
        if not caja:
            raise
        page.mouse.click(caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2)


def abrir(page: Any, loc: Any, espera_ms: int = 1500) -> list[FieldOption]:
    """Clic normal en el control y espera breve a que aparezcan opciones. Devuelve las visibles."""
    loc.scroll_into_view_if_needed()
    loc.evaluate(MARCAR_PREVIAS_JS)
    clic(page, loc)
    t = 0
    while True:
        page.wait_for_timeout(150)
        t += 150
        ops = _visibles(loc)
        if ops or t >= espera_ms:
            return ops


def cerrar(page: Any, loc: Any) -> None:
    """Cierra la lista sin elegir nada: Escape; si sigue abierta, otro clic en el control; y por último fuera."""
    try:
        for paso in ("escape", "clic", "fuera"):
            if paso != "escape" and not loc.evaluate(OPCIONES_VISIBLES_JS):
                return
            if paso == "escape":            # siempre una vez: aunque no se vean opciones, la lista pudo quedar abierta
                page.keyboard.press("Escape")
            elif paso == "clic":
                clic(page, loc)
            else:
                page.mouse.click(2, 2)
            page.wait_for_timeout(200)
    except Exception:
        pass


def buscar_opcion(page: Any, loc: Any, opt: FieldOption) -> Optional[Any]:
    return page.evaluate_handle(BUSCAR_OPCION_JS, [loc.element_handle(), opt.value, opt.label]).as_element()


def sondear_opciones(page: Any, selector: str) -> list[FieldOption]:
    """Abre la lista con un clic normal, lee las opciones visibles y la vuelve a cerrar. No elige nada."""
    loc = page.locator(selector).first
    try:
        return abrir(page, loc)
    except Exception:
        return []
    finally:
        cerrar(page, loc)


def completar_opciones(page: Any, campos: list) -> None:
    """Para las listas personalizadas cuyas opciones no están en la página hasta abrirlas."""
    for f in campos:
        if f.type == "custom-select" and not f.options and f.visible and f.fillable:
            f.options = sondear_opciones(page, f.selector)
            recordar(page, f.key, f.options)
