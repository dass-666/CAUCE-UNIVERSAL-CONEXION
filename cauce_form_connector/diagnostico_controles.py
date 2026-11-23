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

from . import listas as LJ

FORMATO = "cauce.diagnostico_controles.v1"

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
    jquery: !!w.jQuery, select2: !!(w.jQuery && w.jQuery.fn && w.jQuery.fn.select2) || cuenta("[class*=select2]") > 0,
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
      anterior: c.el.previousElementSibling ? firma(c.el.previousElementSibling) + " «" + corto(c.el.previousElementSibling.textContent, 30) + "»" : "",
      siguiente: c.el.nextElementSibling ? firma(c.el.nextElementSibling) : "" })),
  };
}
"""

# Tras un clic: qué elementos nuevos se ven y dónde está el foco (solo forma y textos cortos de la página)
NUEVOS_JS = "(el) => {" + LJ.COMUN_JS + r"""
  const corto = (s, n) => { s = clean(s); return s.length > n ? s.slice(0, n) + "…" : s; };
  const firma = (e) => e.tagName.toLowerCase() + (cls(e) ? "." + cls(e).trim().split(/\s+/).slice(0, 4).join(".") : "");
  const previas = window.__cauce_previas || new Set();
  const nuevos = Array.from(document.querySelectorAll("body *")).filter((o) => !previas.has(o) && visible(o));
  const hojasN = nuevos.filter((o) => !Array.from(o.children).some((h) => nuevos.includes(h)) && clean(o.textContent));
  const a = document.activeElement;
  return { nuevos_visibles: nuevos.length,
    dentro_del_control: nuevos.filter((o) => el.contains(o) || (el.parentElement && el.parentElement.contains(o))).length,
    muestra: hojasN.slice(0, 12).map((o) => ({ elemento: firma(o), padre: o.parentElement ? firma(o.parentElement) : "",
      rol: o.getAttribute("role") || "", data: Array.from(o.attributes).filter((x) => x.name.startsWith("data-")).map((x) => x.name).slice(0, 5),
      texto: corto(o.textContent, 40) })),
    opciones_reconocidas: candidatos(el).length,
    foco: a && a !== document.body ? firma(a) + (a.readOnly ? " [solo lectura]" : "") : "" };
}"""


def recoger(sesion: Any, analisis: Any) -> dict:
    """Devuelve el diagnóstico. No lanza: si algo falla, lo anota en `errores`."""
    page = sesion.page
    out: dict[str, Any] = {"formato": FORMATO, "errores": [], "nota": (
        "Solo estructura de la página; sin datos de personas, cookies ni parámetros de URL. Los textos son rótulos y "
        "opciones de la propia página.")}
    campos = analisis.all_fields
    out["campos_reconocidos"] = [{"clave": f.key, "tipo": f.type, "etiqueta": f.label[:60], "visible": f.visible,
                                  "editable": f.fillable, "opciones": len(f.options)} for f in campos]
    try:
        out.update(page.evaluate(RECOGER_JS, [f.selector for f in campos if f.type != "hidden"]))
    except Exception as exc:
        out["errores"].append(f"recoger: {type(exc).__name__}")
        return out
    if analisis.safety.blocked:
        out["errores"].append("página con intervención humana requerida: no se hacen clics de sondeo")
        return out
    sondeos = []
    objetivos = [("lista reconocida", f.selector, f.key) for f in campos
                 if f.type == "custom-select" and f.visible and f.fillable][:15]
    objetivos += [("candidato", c["selector"], f"candidato {c['n']}") for c in out.get("candidatos", []) if c["sondeable"]][:15]
    for clase, selector, nombre in objetivos:
        r: dict[str, Any] = {"objetivo": nombre, "clase": clase}
        try:
            loc = page.locator(selector).first
            loc.scroll_into_view_if_needed(timeout=2000)
            loc.evaluate(LJ.MARCAR_PREVIAS_JS)
            LJ.clic(page, loc)
            page.wait_for_timeout(700)
            r.update(loc.evaluate(NUEVOS_JS))
        except Exception as exc:
            r["error"] = type(exc).__name__
        finally:
            try:
                LJ.cerrar(page, page.locator(selector).first)
            except Exception:
                pass
        sondeos.append(r)
    out["sondeos"] = sondeos
    return out


def resumen_markdown(d: dict) -> str:
    L = ["# Diagnóstico de controles", "", d.get("nota", ""), "",
         f"- Formularios: {d.get('formularios')} · campos reconocidos: {len(d.get('campos_reconocidos', []))} · "
         f"candidatos sin reconocer: {len(d.get('candidatos', []))}",
         f"- Marcos (iframes): {len(d.get('marcos', []))} · componentes con Shadow DOM: {len(d.get('shadow_dom', []))}",
         "- Librerías detectadas: " + (", ".join(k for k, v in (d.get("librerias") or {}).items() if v) or "ninguna conocida"), ""]
    if d.get("marcos"):
        L.append("## Marcos")
        L += [f"- {m['host'] or '(sin src)'} · mismo origen: {m['mismo_origen']} · campos dentro: {m['campos_dentro']}" for m in d["marcos"]]
        L.append("")
    if d.get("candidatos"):
        L.append("## Candidatos sin reconocer")
        for c in d["candidatos"]:
            L += [f"### {c['n']}. {c['elemento']} — {c['motivo']}", f"- antes: {c['anterior']} · ancestros: {' < '.join(c['ancestros'])}",
                  "```html", c["esqueleto"], "```"]
    if d.get("sondeos"):
        L.append("## Sondeos (qué aparece al hacer clic)")
        for s in d["sondeos"]:
            L.append(f"- {s['objetivo']} ({s['clase']}): " + (f"error {s['error']}" if s.get("error") else
                     f"{s.get('nuevos_visibles')} elementos nuevos, {s.get('opciones_reconocidas')} reconocidos como opciones; "
                     f"foco en {s.get('foco') or '—'}"))
    return "\n".join(L) + "\n"
