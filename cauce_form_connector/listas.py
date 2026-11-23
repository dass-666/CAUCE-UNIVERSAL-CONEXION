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
  if (el.matches("input")) {   // cuadro de texto que abre una lista: solo vale un input oculto que sea su pareja inmediata
    for (let n = el.parentElement, k = 0; n && k < 2 && n.tagName !== "FORM" && n !== document.body; n = n.parentElement, k++) {
      const vis = Array.from(n.querySelectorAll("input:not([type=hidden]),select,textarea")).filter((x) => x !== el && visible(x));
      if (vis.length) return null;
      const hs = n.querySelectorAll("input[type=hidden]");
      if (hs.length === 1) return hs[0];
      if (hs.length > 1) return null;
    }
    return null;
  }
  const c = contenedor(el); return c ? c.querySelector("input[type=hidden],input[aria-hidden=true]") : null; };
// lo que aparece es un calendario, no una lista de opciones
const esCalendario = (xs) => { if (!xs.length) return false;
  if (xs.some((o) => o.closest("table,[class*=datepicker i],[class*=calendar i],[class*=flatpickr i],[class*=picker__ i],[class*=daterange i]"))) return true;
  const nums = xs.filter((o) => /^\d{1,2}$/.test(texto(o))).length; return xs.length >= 20 && nums / xs.length > 0.6; };
const SELS = ["[role=option]", "[data-value]", "li", "[class*=option i]", "[class*=item i]", "[class*=result i]", "[class*=choice i]", "[class*=suggest i]"];
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

# Indicios de que un cuadro de texto abre una lista (solo lectura de la página; ningún valor escrito por la persona).
SENALES_JS = "(el) => {" + COMUN_JS + r"""
  const LISTA = /autocomplete|typeahead|combo|select|dropdown|suggest|tt-input|awesomplete|es-input|chosen|lookup/i;
  const FECHA = /date|fecha|calend|flatpickr|picker__|hasDatepicker/i;
  const out = { clases: [], aria: {}, datos: [], eventos: [], jquery: null, widgets: [], opciones_config: null, abre_al_escribir_min: null,
    solo_lectura: !!el.readOnly, datalist: !!el.getAttribute("list"), oculto_pareja: !!oculto(el), parece_fecha: false, fuertes: [] };
  for (let n = el, k = 0; n && k < 3 && n.tagName !== "FORM" && n !== document.body; n = n.parentElement, k++) {
    if (k > 0 && Array.from(n.querySelectorAll("input:not([type=hidden]),select,textarea")).some((x) => x !== el && visible(x))) break;
    const c = cls(n); if (c) out.clases.push(n.tagName.toLowerCase() + "." + c.trim().split(/\s+/).slice(0, 5).join("."));
    if (LISTA.test(c)) out.fuertes.push("clase de lista en " + (k ? "su contenedor" : "el campo"));
    if (FECHA.test(c)) out.parece_fecha = true;
  }
  for (const a of ["role", "aria-autocomplete", "aria-haspopup", "aria-expanded", "aria-owns", "aria-controls"]) { const v = el.getAttribute(a); if (v !== null) out.aria[a] = v.slice(0, 30); }
  if (/^(list|both)$/.test(out.aria["aria-autocomplete"] || "")) out.fuertes.push("aria-autocomplete");
  if (/^(listbox|true|menu)$/.test(out.aria["aria-haspopup"] || "")) out.fuertes.push("aria-haspopup");
  out.datos = Array.from(el.attributes).filter((a) => a.name.startsWith("data-")).map((a) => a.name).slice(0, 12);
  if (/dropdown/.test((el.getAttribute("data-toggle") || "") + (el.getAttribute("data-bs-toggle") || "")) || el.getAttribute("data-provide") === "typeahead"
      || el.hasAttribute("data-target") && el.readOnly || el.hasAttribute("data-activates")) out.fuertes.push("atributo data- de lista");
  out.eventos = Array.from(el.attributes).filter((a) => /^on(click|focus|key|input|mouse|change)/.test(a.name)).map((a) => a.name);
  const $ = window.jQuery;
  if ($ && $.fn) {
    out.jquery = String($.fn.jquery || "");
    try { const ev = ($._data && $._data(el, "events")) || {}; out.eventos = out.eventos.concat(Object.keys(ev).map((k) => "jq:" + k)); } catch (e) {}
    let datos = {}; try { datos = $(el).data() || {}; } catch (e) {}
    out.widgets = Object.keys(datos).filter((k) => typeof datos[k] === "object" && datos[k] !== null).slice(0, 10);
    const item = (x) => typeof x === "string" ? { value: x, label: x } : (x && typeof x === "object" && (x.label || x.value))
      ? { value: String(x.label || x.value), label: String(x.label || x.value) } : null;
    const lista = (xs) => { if (!Array.isArray(xs) || !xs.length || xs.length > 300) return null; const r = xs.map(item); return r.every(Boolean) ? r : null; };
    const ui = datos.uiAutocomplete || datos["ui-autocomplete"] || datos.autocomplete || null;
    if (ui && ui.options) {              // jQuery UI Autocomplete o compatible: configuración local del componente
      out.fuertes.push("componente de autocompletar de jQuery");
      const o = ui.options; out.abre_al_escribir_min = typeof o.minLength === "number" ? o.minLength : (typeof o.minChars === "number" ? o.minChars : null);
      out.opciones_config = lista(o.source) || lista(o.lookup);
      out.origen_opciones = Array.isArray(o.source || o.lookup) ? "lista local" : typeof (o.source || o.lookup || o.serviceUrl) === "string" ? "servidor" : typeof (o.source || o.lookup) === "function" ? "función" : "desconocido";
    }
    if (datos.ttTypeahead || datos.typeahead) out.fuertes.push("componente typeahead de jQuery");
  }
  if (window.M && window.M.Autocomplete && window.M.Autocomplete.getInstance && window.M.Autocomplete.getInstance(el)) out.fuertes.push("componente de autocompletar");
  const txt = [el.name, el.id, el.getAttribute("placeholder"), el.getAttribute("data-date-format"), el.getAttribute("data-format")].join(" ");
  if (FECHA.test(txt) || /(dd|mm|yyyy|aaaa)[\/\-.]/i.test(txt)) out.parece_fecha = true;
  return out; }"""

# Qué apareció tras interactuar con el cuadro de texto: opciones, o un calendario (que no es una lista).
APARECIDO_JS = "(el) => {" + COMUN_JS + r"""
  const xs = candidatos(el);
  const previas = window.__cauce_previas || new Set();
  const nuevos = Array.from(document.querySelectorAll("body *")).filter((o) => !previas.has(o) && visible(o));
  if (esCalendario(xs) || nuevos.some((o) => o.matches("table,[class*=datepicker i],[class*=calendar i],[class*=flatpickr i]"))) return { calendario: true, nuevos: nuevos.length, opciones: [] };
  return { calendario: false, nuevos: nuevos.length, opciones: xs.map((o) => ({ value: valor(o), label: texto(o), selected: false })) }; }"""

MARCAR_PREVIAS_JS = "(el) => {" + COMUN_JS + r"""
  window.__cauce_previas = new Set(Array.from(document.querySelectorAll("body *")).filter(visible));
  return window.__cauce_previas.size; }"""

OPCIONES_VISIBLES_JS = "(el) => {" + COMUN_JS + r"""
  return candidatos(el).map((o) => ({ value: valor(o), label: texto(o), selected: false })); }"""

BUSCAR_OPCION_JS = "([el, v, etiqueta]) => {" + COMUN_JS + r"""
  const n = norm(etiqueta);
  const en = (xs) => xs.find((o) => o.getAttribute("data-value") === v) || xs.find((o) => texto(o) === etiqueta)
    || xs.find((o) => norm(texto(o)) === n || norm(o.getAttribute("title") || o.getAttribute("aria-label") || "") === n) || null;
  const hallada = en(candidatos(el));
  if (hallada) return hallada;
  // Una lista filtrada puede dejar una sola opción sin ninguna marca reconocible: vale cualquier elemento NUEVO cuyo
  // texto sea exactamente el buscado (el más interno), nunca uno parecido.
  const previas = window.__cauce_previas || new Set();
  const nuevos = Array.from(document.querySelectorAll("body *")).filter((o) => !previas.has(o) && visible(o) && util(o, el));
  return en(hojas(nuevos.filter((o) => norm(texto(o)) === n))); }"""

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


def esperar_opciones(page: Any, loc: Any, espera_ms: int) -> list[FieldOption]:
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
        if (f.type == "custom-select" and not f.options and f.visible and f.fillable
                and f.key not in getattr(page, "_cauce_listas", {})):
            f.options = sondear_opciones(page, f.selector)
            recordar(page, f.key, f.options)


# ---------------------------------------------------------------------------------------------------------------------
# Cuadros de texto que en realidad abren una lista (autocompletar de jQuery y similares, entradas de solo lectura con
# menú, selects editables). El detector los ve como «input type=text»; aquí se comprueba con interacción ordinaria.

TIPOS_TEXTO = ("text", "search", "")
DATO_LIBRE = {"email", "telefono", "nombre", "apellido", "nombre_completo", "codigo_postal", "direccion", "ciudad",
              "empresa", "cargo", "comentario", "mensaje", "fecha", "fecha_nacimiento"}


def _esperar_aparicion(page: Any, loc: Any, espera_ms: int) -> dict:
    t, r = 0, {"calendario": False, "nuevos": 0, "opciones": []}
    while t < espera_ms:
        page.wait_for_timeout(150)
        t += 150
        r = loc.evaluate(APARECIDO_JS)
        if r["opciones"] or r["calendario"]:
            break
    return r


def _opciones(crudas: list) -> list[FieldOption]:
    vistos, out = set(), []
    for o in crudas or []:
        if o.get("value") and o["value"] not in vistos:
            vistos.add(o["value"])
            out.append(FieldOption(value=o["value"], label=o.get("label") or o["value"], selected=False))
    return out


def sondear_textos(sesion: Any, campos: list) -> list[dict]:
    """Comprueba, campo de texto por campo de texto, si abre una lista. Solo interacción ordinaria: un clic y, si no
    aparece nada, la tecla flecha abajo; después Escape. No escribe nada. Devuelve una explicación por cada campo
    (reconocido o descartado y por qué) y registra en la sesión los que resultan ser listas, para que `inspect()` los
    trate como tales a partir de ahora."""
    from .matcher import field_concept
    page, explicaciones = sesion.page, []
    for f in campos:
        if f.tag != "input" or f.type == "custom-select":
            continue
        e: dict[str, Any] = {"clave": f.key, "tipo_detectado": f.type, "decision": "texto", "pasos": []}
        explicaciones.append(e)
        if f.type not in TIPOS_TEXTO:
            e["motivo"] = f"no se sondea: es un campo de tipo «{f.type}»"
            continue
        if not f.visible or f.disabled:
            e["motivo"] = "no se sondea: no está visible o está deshabilitado"
            continue
        loc = page.locator(f.selector).first
        try:
            sen = loc.evaluate(SENALES_JS)
        except Exception as exc:
            e["motivo"] = f"no se pudo inspeccionar: {type(exc).__name__}"
            continue
        config = sen.pop("opciones_config", None)
        e["senales"] = sen
        concepto = field_concept(f)[0]
        if sen["parece_fecha"] or concepto in ("fecha", "fecha_nacimiento"):
            e["motivo"] = "no se sondea: parece un campo de fecha (un clic abriría el calendario)"
            continue
        if sen["datalist"]:
            e["motivo"] = "usa <datalist> nativo: admite texto libre y se rellena como texto"
            continue
        opciones, modo, calendario = [], "", False
        try:
            antes = loc.input_value()
            loc.scroll_into_view_if_needed(timeout=2000)
            loc.evaluate(MARCAR_PREVIAS_JS)
            clic(page, loc)
            r = _esperar_aparicion(page, loc, 750)
            e["pasos"].append({"accion": "clic", "elementos_nuevos": r["nuevos"], "opciones": len(r["opciones"]), "calendario": r["calendario"]})
            calendario = r["calendario"]
            if r["opciones"]:
                opciones, modo = _opciones(r["opciones"]), "clic"
            elif not calendario:
                page.keyboard.press("ArrowDown")
                r = _esperar_aparicion(page, loc, 450)
                e["pasos"].append({"accion": "flecha abajo", "elementos_nuevos": r["nuevos"], "opciones": len(r["opciones"]), "calendario": r["calendario"]})
                calendario = r["calendario"]
                if r["opciones"]:
                    opciones, modo = _opciones(r["opciones"]), "flecha abajo"
            cerrar(page, loc)
            e["valor_alterado_por_el_sondeo"] = loc.input_value() != antes
        except Exception as exc:
            e["pasos"].append({"accion": "error", "tipo": type(exc).__name__})
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        if calendario:
            e["motivo"] = "al pulsarlo se abre un calendario, no una lista: se trata como fecha"
            continue
        libre = concepto in DATO_LIBRE and not (sen["solo_lectura"] or sen["oculto_pareja"])
        if opciones:
            if libre:
                e["motivo"] = (f"muestra sugerencias, pero es un dato libre reconocido ({concepto}) sin valor interno "
                               "asociado: se rellena como texto")
                continue
            e.update(decision="lista", modo=modo, opciones=len(opciones),
                     motivo=f"al interactuar ({modo}) aparecen {len(opciones)} opciones: es una lista")
        elif sen["fuertes"] and not libre:
            opciones = _opciones(config)
            e.update(decision="lista", modo="escribir", opciones=len(opciones), motivo=(
                "indicios de lista (" + "; ".join(sen["fuertes"]) + ") pero solo muestra opciones al escribir"
                + (": opciones leídas de la configuración local del componente" if opciones else
                   ": las opciones no se conocen hasta teclear; se usará el texto exacto de la tabla de equivalencias")))
        elif sen["solo_lectura"]:
            e["motivo"] = "es de solo lectura y al pulsarlo no apareció ninguna lista: no editable"
            continue
        else:
            e["motivo"] = ("al hacer clic y pulsar flecha abajo no apareció ninguna lista y no tiene indicios de "
                           "autocompletar: campo de texto normal" + (f" (dato libre: {concepto})" if libre else ""))
            continue
        sesion.listas_texto[f.key] = opciones
        promover(f, opciones)
        _cache(page)[f.key] = opciones          # también si está vacía: ya se sondeó, no se repite
    return explicaciones


def promover(f: Any, opciones: list[FieldOption]) -> None:
    """Un cuadro de texto comprobado como lista pasa a tratarse como lista personalizada (misma clave y selector)."""
    f.type = "custom-select"
    f.options = list(opciones)
    f.readonly = False            # una entrada de solo lectura que abre un menú sí se puede elegir
