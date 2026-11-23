"""Diagnóstico de controles: evidencia de CÓMO están hechos los controles de un formulario, para poder ampliar el
detector cuando una página real usa algo que todavía no se reconoce.

Se genera junto con el análisis (solo lectura: no escribe en ningún campo ni pulsa botones de envío o enlaces).
Contiene únicamente estructura de la página (etiquetas, clases, atributos, textos cortos de la propia página): no hay
datos de personas, porque no se ha rellenado nada, ni cookies, ni parámetros de la URL.

Qué recoge:
- campos reconocidos y de qué tipo;
- marcos (iframes) y componentes con Shadow DOM, que el detector todavía no recorre;
- indicios de librerías de interfaz;
- «candidatos»: elementos del formulario que parecen controles y NO se reconocieron (selects ocultos, entradas de
  solo lectura, inputs ocultos con nombre, elementos pulsables con clase o rol de lista), con su esqueleto HTML;
- sondeo: para las listas reconocidas y para los candidatos que parecen listas, qué aparece al hacer un clic normal
  (número y forma de los elementos nuevos) y dónde queda el foco. Después se cierra con Escape.
"""
from __future__ import annotations

from typing import Any

import json
import re

from . import listas as LJ

FORMATO = "cauce.diagnostico_controles.v2"

RECOGER_JS = r"""
(selectoresReconocidos) => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const cls = (e) => (e.getAttribute && e.getAttribute("class")) || "";
  const corto = (s, n) => { s = clean(s); return s.length > n ? s.slice(0, n) + "…" : s; };
  const vis = (e) => { const st = getComputedStyle(e), r = e.getBoundingClientRect();
    return st.display !== "none" && st.visibility !== "hidden" && r.width > 1 && r.height > 1; };
  const DROP = /select|dropdown|combo|listbox|picker|choices|chosen|autocomplete|multiselect|menu|option/i;
  const firma = (e) => e.tagName.toLowerCase() + (cls(e) ? "." + cls(e).trim().split(/\s+/).slice(0, 4).join(".") : "");
  const attrs = (e) => { const o = {}; for (const a of Array.from(e.attributes)) {
      if (/^(role|type|name|tabindex|readonly|disabled|placeholder|for|inputmode|pattern|maxlength|contenteditable)$|^aria-|^data-/.test(a.name)) o[a.name] = corto(a.value, 40); }
    return o; };
  // Esqueleto: etiquetas, clases y atributos; el texto se recorta. Sin valores de campos.
  const esqueleto = (e, prof) => { if (prof > 3) return "…";
    const hijos = Array.from(e.children).slice(0, 8).map((h) => esqueleto(h, prof + 1)).join("");
    const at = Object.entries(attrs(e)).map(([k, v]) => ` ${k}="${v}"`).join("");
    const t = e.children.length ? "" : corto(e.textContent, 30);
    return `<${firma(e)}${at}>${t}${hijos}${e.children.length > 8 ? "…" : ""}</${e.tagName.toLowerCase()}>`; };
  const ruta = (e) => { const p = []; for (let n = e.parentElement, k = 0; n && k < 4 && n !== document.body; n = n.parentElement, k++) p.push(firma(n)); return p; };
  const selector = (e) => { if (e.id && document.querySelectorAll("#" + CSS.escape(e.id)).length === 1) return "#" + CSS.escape(e.id);
    const parts = []; for (let n = e; n && n.nodeType === 1 && n !== document.documentElement; n = n.parentElement) {
      let s = n.tagName.toLowerCase(); const p = n.parentElement;
      if (p) { const same = Array.from(p.children).filter((c) => c.tagName === n.tagName); if (same.length > 1) s += `:nth-of-type(${same.indexOf(n) + 1})`; }
      parts.unshift(s); } return parts.join(" > "); };

  const reconocidos = selectoresReconocidos.map((s) => { try { return document.querySelector(s); } catch (e) { return null; } }).filter(Boolean);
  const yaVisto = (e) => reconocidos.some((r) => r === e || r.contains(e) || e.contains(r));
  // un select escondido junto a una lista ya reconocida es el que esa lista sustituye
  const cerca = (s) => reconocidos.some((r) => { let a = r.parentElement;
    for (let i = 0; a && i < 3 && !a.matches("form,body"); i++, a = a.parentElement) if (a.contains(s)) return true;
    return false; });
  const zonas = Array.from(document.querySelectorAll("form"));
  const raices = zonas.length ? zonas : [document.body];
  const cand = [];
  const anota = (e, motivo, sondeable) => { if (cand.length >= 60 || cand.some((c) => c.el === e) || yaVisto(e)) return;
    cand.push({ el: e, motivo, sondeable }); };
  raices.forEach((z) => {
    z.querySelectorAll("select").forEach((s) => { if (!vis(s) && !cerca(s)) anota(s, "select nativo oculto sin widget reconocido", false); });
    z.querySelectorAll("input[readonly]:not([type=hidden])").forEach((i) => { if (vis(i)) anota(i, "entrada de solo lectura (posible lista o calendario)", true); });
    z.querySelectorAll("input[type=hidden][name]").forEach((h) => { const p = h.parentElement;
      if (p && vis(p) && clean(p.textContent).length < 200 && p.children.length > 1) anota(p, "contenedor con input oculto con nombre", true); });
    z.querySelectorAll("[role=listbox],[role=menu],[role=button][aria-haspopup],[aria-expanded],[contenteditable=true]").forEach((e) => { if (vis(e)) anota(e, "rol ARIA de lista/menú no reconocido", !e.matches("a[href],button[type=submit]")); });
    z.querySelectorAll("*").forEach((e) => {
      if (!vis(e) || e.matches("form,fieldset,label,option,optgroup,script,style,a[href],button,input,select,textarea")) return;
      const st = getComputedStyle(e);
      const pinta = DROP.test(cls(e)) || /-/.test(e.tagName);
      const pulsa = e.hasAttribute("tabindex") || e.hasAttribute("onclick") || st.cursor === "pointer";
      if (pinta && pulsa && clean(e.textContent).length < 160 && !e.querySelector("input:not([type=hidden]),textarea"))
        anota(e, /-/.test(e.tagName) ? "componente web o etiqueta propia pulsable" : "elemento pulsable con clase de lista", true);
    });
  });
  // quitar candidatos contenidos en otro candidato (se queda el más externo)
  const finales = cand.filter((c) => !cand.some((o) => o !== c && o.el.contains(c.el)));

  const marcos = Array.from(document.querySelectorAll("iframe,frame")).map((f) => { let mismo = false, campos = null;
    try { mismo = !!f.contentDocument; campos = f.contentDocument ? f.contentDocument.querySelectorAll("input,select,textarea").length : null; } catch (e) {}
    let host = ""; try { host = new URL(f.src, location.href).host; } catch (e) {}
    return { host, mismo_origen: mismo, campos_dentro: campos, visible: vis(f), titulo: corto(f.title || f.name || "", 40) }; });
  const sombra = Array.from(document.querySelectorAll("*")).filter((e) => e.shadowRoot).map((e) => ({
    etiqueta: firma(e), campos_dentro: e.shadowRoot.querySelectorAll("input,select,textarea,[role=combobox]").length })).slice(0, 30);
  const cuenta = (s) => document.querySelectorAll(s).length;
  const w = window;
  const librerias = {
    jquery: !!w.jQuery, ext_js: !!(w.Ext && (w.Ext.getCmp || w.Ext.Component)), select2: !!(w.jQuery && w.jQuery.fn && w.jQuery.fn.select2) || cuenta("[class*=select2]") > 0,
    chosen: cuenta("[class*=chosen-]") > 0, tom_select: cuenta(".ts-wrapper,.ts-control") > 0, choices: cuenta(".choices") > 0,
    selectize: cuenta(".selectize-control") > 0, bootstrap_select: cuenta(".bootstrap-select") > 0, nice_select: cuenta(".nice-select") > 0,
    react: !!document.querySelector("[data-reactroot]") || Array.from(document.querySelectorAll("body *")).slice(0, 400).some((e) => Object.keys(e).some((k) => k.startsWith("__react"))),
    react_select: cuenta("[class*='-control'][class^='css-'],[id^=react-select]") > 0, mui: cuenta("[class*=Mui]") > 0, ant_design: cuenta("[class^=ant-],[class*=' ant-']") > 0,
    angular: !!document.querySelector("[ng-version]") || cuenta("[class^=mat-],[class*=' mat-'],[class^=ng-],[class*=' ng-']") > 0, angular_material: cuenta("mat-select,[class*=mat-mdc],[class*=mat-select]") > 0,
    primeng_primefaces: cuenta("[class*=p-dropdown],[class*=ui-selectonemenu],[class*=p-select]") > 0,
    vue: !!document.querySelector("[data-v-app]") || Array.from(document.querySelectorAll("body *")).slice(0, 400).some((e) => e.__vue_app__ || e.__vue__),
    element_ui: cuenta("[class^=el-select],[class*=' el-select']") > 0, vuetify: cuenta("[class*=v-select],[class*=v-input]") > 0,
    kendo: cuenta("[class*=k-dropdown],[class*=k-picker]") > 0, devextreme: cuenta("[class*=dx-selectbox],[class*=dx-dropdowneditor]") > 0,
    asp_net_webforms: !!document.querySelector("input[name=__VIEWSTATE]"), jsf: !!document.querySelector("input[name='javax.faces.ViewState'],input[name='jakarta.faces.ViewState']"),
    datepicker: cuenta("[class*=datepicker],[class*=flatpickr],[class*=hasDatepicker],[class*=calendar]") > 0,
  };
  window.__cauce_diag = finales.map((c) => c.el);
  return {
    titulo_documento: corto(document.title, 80), idioma: document.documentElement.lang || "",
    formularios: zonas.length, marcos, shadow_dom: sombra, librerias,
    candidatos: finales.map((c, i) => ({ n: i, motivo: c.motivo, sondeable: c.sondeable, selector: selector(c.el), elemento: firma(c.el),
      atributos: attrs(c.el), ancestros: ruta(c.el), texto: corto(c.el.textContent, 60), esqueleto: esqueleto(c.el, 0).slice(0, 900),
      anterior: c.el.previousElementSibling ? firma(c.el.previousElementSibling) : "",
      anterior_texto: c.el.previousElementSibling ? corto(c.el.previousElementSibling.textContent, 30) : "",
      siguiente: c.el.nextElementSibling ? firma(c.el.nextElementSibling) : "" })),
  };
}
"""


def recoger(sesion: Any, analisis: Any, explicaciones: Any = None, seguridad: Any = None) -> dict:
    """Devuelve el diagnóstico. SOLO LECTURA: no hace clics ni pulsa teclas; las interacciones, si las hubo, las hizo
    la fase activa del análisis y llegan ya descritas en `explicaciones`. No lanza: los fallos van a `errores`."""
    page = sesion.page
    out: dict[str, Any] = {"formato": FORMATO, "errores": [], "nota": (
        "Solo estructura de la página; sin datos de personas, cookies ni parámetros de URL. Los textos son rótulos de la "
        "propia página. Para compartir use la versión anonimizada.")}
    campos = analisis.all_fields
    out["seguridad"] = dict(seguridad or {})
    por_clave = {e["clave"]: e for e in (explicaciones or [])}
    out["explicaciones"] = []
    for f in campos:
        e = dict(por_clave.get(f.key) or {"clave": f.key, "tipo_detectado": f.type, "fase": "pasiva", "pasos": [],
                                         "decision": f.type, "motivo": "sin comprobación (página con intervención humana)"})
        e["tipo_final"] = f.type
        e["opciones"] = len(f.options)          # la cifra es SIEMPRE la del análisis real
        out["explicaciones"].append(e)
    out["campos_reconocidos"] = [{"clave": f.key, "tipo": f.type, "etiqueta": f.label[:60], "visible": f.visible,
                                  "editable": f.fillable, "opciones": len(f.options)} for f in campos]
    listas = [f for f in campos if f.type == "custom-select"]
    out["resumen"] = {"campos": len(campos), "listas": len(listas),
                      "listas_con_opciones": sum(1 for f in listas if f.options),
                      "opciones_en_total": sum(len(f.options) for f in listas),
                      "interacciones": sum(len([p for p in e.get("pasos", []) if p.get("accion") != "error"])
                                           for e in out["explicaciones"])}
    try:
        out.update(page.evaluate(RECOGER_JS, [f.selector for f in campos if f.type != "hidden"]))
    except Exception as exc:
        out["errores"].append(f"recoger: {type(exc).__name__}")
    return out


def resumen_markdown(d: dict) -> str:
    r, seg = d.get("resumen") or {}, d.get("seguridad") or {}
    L = ["# Diagnóstico de controles", "", d.get("nota", ""), "",
         f"- Formularios: {d.get('formularios')} · campos reconocidos: {r.get('campos', len(d.get('campos_reconocidos', [])))} · "
         f"candidatos sin reconocer: {len(d.get('candidatos', []))}",
         f"- Listas: {r.get('listas', 0)} · con opciones: {r.get('listas_con_opciones', 0)} · opciones en total: {r.get('opciones_en_total', 0)}",
         f"- Marcos (iframes): {len(d.get('marcos', []))} · componentes con Shadow DOM: {len(d.get('shadow_dom', []))}",
         "- Librerías detectadas: " + (", ".join(k for k, v in (d.get("librerias") or {}).items() if v) or "ninguna conocida"), "",
         "## Seguridad del análisis", "",
         "- Modo: " + ("pasivo: solo lectura de la página, sin clics ni teclas" if seg.get("sondeo") != "clic" else
                       "con comprobación de clic: solo ratón, sin teclado, con las escrituras al servidor bloqueadas"),
         f"- Interacciones realizadas: {r.get('interacciones', 0)}",
         "- Valores del formulario alterados: " + (", ".join(seg.get("controles_alterados") or []) or "ninguno"),
         f"- Peticiones de escritura bloqueadas: {seg.get('escrituras_bloqueadas', 0)}",
         f"- Controles sin comprobar: {len(seg.get('sin_comprobar') or [])}", ""]
    if d.get("marcos"):
        L.append("## Marcos")
        L += [f"- {m.get('host') or '(sin src)'} · mismo origen: {m['mismo_origen']} · campos dentro: {m['campos_dentro']}" for m in d["marcos"]]
        L.append("")
    if d.get("explicaciones"):
        L += ["## Por qué cada control se reconoció o se descartó", ""]
        for e in d["explicaciones"]:
            pasos = "; ".join(f"{p.get('accion')}: {p.get('opciones', 0)} opciones vistas"
                              + (" (calendario)" if p.get("calendario") else "") for p in e.get("pasos", []) if p.get("accion") != "error")
            sen = e.get("senales") or {}
            L.append(f"- **{e['clave']}** — detectado «{e.get('tipo_detectado')}», queda como «{e.get('tipo_final')}»"
                     + (f" con {e.get('opciones', 0)} opciones" if e.get("tipo_final") == "custom-select" else "")
                     + f". {e.get('motivo', '')}"
                     + (f" · fase {e.get('fase')}" if e.get("fase") else "") + (f" · pasos: {pasos}" if pasos else "")
                     + (f" · indicios: {', '.join(sen.get('fuertes') or []) or 'ninguno'}" if sen else "")
                     + (f" · eventos: {', '.join(sen.get('eventos', [])[:8])}" if sen.get("eventos") else "")
                     + (" · AVISO: la comprobación cambió un valor del formulario" if e.get("valor_alterado_por_el_sondeo") else ""))
        L.append("")
    if d.get("candidatos"):
        L.append("## Candidatos sin reconocer (solo descritos, no se pulsan)")
        for c in d["candidatos"]:
            L += [f"### {c['n']}. {c['elemento']} — {c['motivo']}", f"- antes: {c.get('anterior', '')} · ancestros: {' < '.join(c['ancestros'])}",
                  "```html", c["esqueleto"], "```"]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------------------------------
# Versión para compartir. Principio: lista blanca. En la salida solo pueden quedar palabras de un vocabulario técnico
# conocido o de los textos que genera este programa; cualquier otra palabra (clases, ids, nombres de campo, atributos)
# se sustituye por un seudónimo estable (t1, t2…), y los textos de la página, por su longitud. Después se VERIFICA la
# salida entera con la misma lista blanca: si queda una sola palabra fuera de ella, no se exporta.

VOCAB = set("""
a abbr active addon after alert align all alt anchor ant app area aria arrow article aside auto autocomplete awesomplete
badge bar base before bg block body bold boot bootstrap border bottom bound box boundlist btn bs button by
calendar caption card caret cell center check checkbox checked child choice choices chosen class clear clearfix click
close code col collapse collapsed color cols column combo combobox complete component container content control
controls corner css current custom data date datefield datepicker datetime day days dd default describedby dialog
disabled display div dl down drop dropdown dt dx easy edit editable el elem element em email empty enabled end error
es expanded ext false fa field fieldset first fixed flat flatpickr flex float focus focused font footer for form
formfield frame full glyphicon grid group h1 h2 h3 h4 h5 h6 handle has haspopup head header height helper hidden hide
highlight hint horizontal hover href html i icon id iframe image img in inline inner input inputmode invalid is item
items jq jquery js k kendo key keydown keypress keyup label labelledby large last layer layout left legend lg li line
link list listbox live loading lookup main mark mask mat material maxlength md medium menu menuitem message middle
min mm modal mode month mui multi multiple multiselect name nav new next ng no none normal not number numberfield
of off ok ol on only open opt optgroup option options outer over overlay owns p pane panel parent part password
pattern picker placeholder plain plugin popper popup position pre prev primary pull query radio range rc react
readonly ref region remove required reset resize result results right role root row rows rtl scroll scroller search
secondary section select select2 selectable selected selection selectize selector separator shadow show side single
size slim sm small span spin spinbutton spinner split ss start state static status step strong style sub submit
success suffix suggest suggestion suggestions sup tab tabindex table target tbody td tel text textarea textbox
textfield tfoot th thead theme time title to toggle tom tool toolbar tooltip top tr tree trigger true ts tt twitter
timeout exception playwright type typeahead ui ul under unselectable up url v val valid value vertical view visible w warning webkit week widget
width wrap wrapper x xs xtype y year yy yyyy z zone
boton buscar calendario campo campos contenedor desplegable entrada fecha fila formulario lista listas oculto opcion
opciones selector texto
""".split())

_TEXTOS = {"etiqueta", "texto", "anterior_texto", "titulo", "siguiente_texto"}
_QUITAR = {"url", "host", "titulo_documento"}
_PROPIOS = {"formato", "nota", "motivo", "decision", "modo", "fase", "accion", "tipo", "tipo_detectado", "tipo_final",
            "origen_opciones", "sondeo", "libreria", "version", "fuertes", "jquery"}
_CLAVES_DE_CAMPO = {"clave", "objetivo", "controles_alterados", "sin_comprobar"}
_ATRIBUTOS_SEGUROS = ("role", "type", "tabindex", "readonly", "disabled", "inputmode", "maxlength", "contenteditable",
                      "aria-expanded", "aria-haspopup", "aria-autocomplete", "aria-hidden", "aria-disabled")
_PALABRA = re.compile(r"[A-Z]?[a-zà-ÿ]+|[A-ZÀ-Ý]+(?![a-zà-ÿ])|\d+")
_SEUDONIMO = re.compile(r"^(t|campo)\d+$")


class ExportacionBloqueada(Exception):
    """La versión anonimizada no superó la verificación: no se debe escribir ni compartir."""


def _vocabulario_propio() -> set:
    """Palabras de los textos que genera este programa (motivos, notas): las de su propio código fuente."""
    from pathlib import Path
    out = set(VOCAB)
    for nombre in ("diagnostico_controles.py", "listas.py"):
        try:
            fuente = (Path(__file__).parent / nombre).read_text(encoding="utf-8")
        except OSError:
            continue
        out.update(m.group(0).lower() for m in _PALABRA.finditer(fuente))
    return out


class _Anonimizador:
    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}
        self.campos: dict[str, str] = {}

    def _token(self, m: "re.Match") -> str:
        t = m.group(0)
        pegado = m.start() > 0 and m.string[m.start() - 1].isalnum()      # camelCase o letras+cifras: se separa con «_»
        if t.isdigit():
            return ("_" if pegado else "") + (t if len(t) <= 2 else "0")
        if t.lower() in VOCAB:
            return t
        return ("_" if pegado else "") + self.tokens.setdefault(t.lower(), f"t{len(self.tokens) + 1}")

    def estructural(self, s: str) -> str:
        return _PALABRA.sub(self._token, s)

    def campo(self, s: str) -> str:
        return self.campos.setdefault(s, f"campo{len(self.campos) + 1}")

    def html(self, s: str) -> str:
        s = re.sub(r">([^<>]+)<", lambda m: ">" + ("…" if m.group(1).strip() else "") + "<", s)
        def attr(m: "re.Match") -> str:
            n = m.group(1)
            return f' {self.estructural(n)}="{self.estructural(m.group(2)) if n in _ATRIBUTOS_SEGUROS else "…"}"'
        s = re.sub(r' ([a-zA-Z_:][-a-zA-Z0-9_:.]*)="([^"]*)"', attr, s)
        return re.sub(r"<(/?)([^\s>]+)", lambda m: "<" + m.group(1) + self.estructural(m.group(2)), s)

    def valor(self, d: Any, clave: str = "") -> Any:
        if isinstance(d, dict):
            out: dict[str, Any] = {}
            for k, v in d.items():
                if k in _QUITAR:
                    continue
                if clave in ("atributos", "aria"):         # nombre de atributo -> valor
                    out[self.estructural(k)] = self.estructural(str(v)) if k in _ATRIBUTOS_SEGUROS else "…"
                elif clave == "librerias":
                    out[k] = bool(v)
                else:
                    out[k] = self.valor(v, k)
            return out
        if isinstance(d, list):
            return [self.valor(x, clave) for x in d]
        if isinstance(d, bool) or d is None or isinstance(d, (int, float)):
            return d
        s = str(d)
        if clave in _TEXTOS:
            return f"texto de {len(s)} caracteres" if s else ""
        if clave in _CLAVES_DE_CAMPO:
            return self.campo(s)
        if clave in _PROPIOS:
            return re.sub(r"«[^»]*»", lambda m: "«" + self.estructural(m.group(0)[1:-1]) + "»", s)
        if clave == "esqueleto":
            return self.html(s)
        return self.estructural(s)


def verificar_anonimo(d: Any) -> list[str]:
    """Palabras de la salida que NO están en la lista blanca (vacío = seguro). No depende del diagnóstico original."""
    permitido = _vocabulario_propio()
    texto = json.dumps(d, ensure_ascii=False)
    fuera = []
    for m in _PALABRA.finditer(texto):
        t = m.group(0).lower()
        if t.isdigit():
            if len(t) > 4:
                fuera.append(t)
        elif t not in permitido and not _SEUDONIMO.match(t) and len(t) > 1:
            fuera.append(t)
    if re.search(r"https?:|www\.|[a-z0-9]@[a-z0-9-]+\.[a-z]{2,}", texto, re.I):
        fuera.append("(patrón de URL o correo)")
    return sorted(set(fuera))


def anonimizar(d: dict) -> dict:
    """Copia del diagnóstico segura para compartir. Lanza `ExportacionBloqueada` si la verificación encuentra en la
    salida cualquier palabra ajena a la lista blanca."""
    out = _Anonimizador().valor(d)
    out["anonimizado"] = True
    out["nota"] = ("Versión anonimizada por lista blanca: sin URL, dominios, título, rótulos ni textos de opciones; las "
                   "clases, identificadores y nombres de campo ajenos al vocabulario técnico aparecen como seudónimos "
                   "(t1, t2…, campo1…). Verificada antes de escribirse.")
    fuera = verificar_anonimo(out)
    if fuera:
        raise ExportacionBloqueada(f"{len(fuera)} palabra(s) fuera de la lista blanca")
    return out
