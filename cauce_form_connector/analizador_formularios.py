"""Analizador de formularios web externos -> informe técnico + perfil de formulario BORRADOR para revisión.

Reutiliza el motor existente (no lo duplica):
- `UrlPolicy` decide si la URL está autorizada (lista de dominios + declaración de autorización).
- `BrowserSession.inspect()` (detector.js + form_detector) detecta formularios, campos, selectores,
  opciones, radios agrupados y pares de confirmación.
- `html_analyzer.analyze_html` sobre el HTML tal como llegó del servidor permite saber qué campos
  creó JavaScript después de cargar.
- `esquema_monitor` (modelo plano del Expediente de Monitor) decide qué campo de Monitor pide cada control, con
  reglas explícitas: no divide nombres, no traduce códigos sin tabla aprobada y no propone CURP/RFC/consentimientos.
- `matcher.field_concept` y `sequential_typer.normalize_date` se reutilizan para reconocer conceptos y fechas.

Qué NO hace (por diseño): no escribe en la página, no hace clic, no envía, no inicia sesión, no resuelve
CAPTCHA y no guarda ni muestra valores del expediente (solo si hay dato y si encaja). Abre la página una
sola vez. El perfil que genera es un BORRADOR: `autorrellenar` solo lo usará después de que una persona
lo apruebe con `--aprobar-perfil rev_xxx`.
"""
from __future__ import annotations

import html as _html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from .form_detector import strip_accents
from .html_analyzer import analyze_html
from . import esquema_monitor as M
from .matcher import field_concept
from .models import AnalysisResult, FieldDescriptor
from .sequential_typer import normalize_date
from .settings import Settings, get_settings
from .signature import structure_signature_v2
from .url_policy import UrlPolicy, safe_url_label

FORMATO = "cauce.analisis_formulario.v1"

# Lee, sin modificar nada, pistas de validación y de comportamiento dinámico de cada campo y de la página.
EXTRA_JS = """
(sels) => {
  const clean = (s) => (s || "").replace(/\\s+/g, " ").trim().slice(0, 160);
  const campos = sels.map((s) => {
    let el = null;
    try { el = document.querySelector(s); } catch (_) {}
    if (!el) return null;
    const ids = (el.getAttribute("aria-describedby") || "").split(/\\s+/).filter(Boolean);
    const mensajes = ids.map((i) => { const n = document.getElementById(i); return n ? clean(n.textContent) : ""; })
                        .filter(Boolean);
    const dyn = Object.keys(el.dataset || {}).filter((k) => /valid|error|mask|format|rule|match|confirm/i.test(k));
    let oculto_por = "";
    if (getComputedStyle(el).display !== "none") {
      for (let p = el.parentElement; p; p = p.parentElement) {
        if (getComputedStyle(p).display === "none" || p.hidden) { oculto_por = p.id ? "#" + p.id : p.tagName.toLowerCase(); break; }
      }
    }
    return { mensajes, title: clean(el.getAttribute("title")), aria_invalid: el.getAttribute("aria-invalid") || "",
             accept: el.getAttribute("accept") || "", data_validacion: dyn, oculto_por };
  });
  const pagina = {
    regiones_aria_live: document.querySelectorAll("[aria-live]").length,
    alertas_role_alert: document.querySelectorAll("[role=alert]").length,
    iframes: document.querySelectorAll("iframe").length,
    campos_password: document.querySelectorAll("input[type=password]").length,
    scripts: document.querySelectorAll("script").length,
  };
  return { campos, pagina };
}
"""

DATE_TEXT_RE = re.compile(r"(dd|aaaa|yyyy|mm)[/\-.]", re.I)


class AnalisisNoPermitido(RuntimeError):
    pass


# --------------------------------------------------------------------------- utilidades
def slug(texto: str, largo: int = 36) -> str:
    t = strip_accents(texto or "").lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return (t[:largo].strip("_")) or "formulario"


def _hay_dato(v: Any) -> bool:
    return v is not None and not (isinstance(v, str) and not v.strip())


def categoria(f: FieldDescriptor) -> str:
    t = f.type
    if t == "hidden":
        return "oculto_tecnico"
    if t in ("submit", "button", "reset", "image"):
        return "boton"
    if t == "file":
        return "archivo"
    if t == "password":
        return "contrasena"
    if f.confirms:
        return "confirmacion"
    if t in ("select", "select-multiple", "radio", "custom-select"):
        return "lista"
    if t == "checkbox":
        return "casilla"
    if t in ("date", "datetime-local", "month", "week", "time"):
        return "fecha"
    if t == "number" or t == "range":
        return "numerico"
    texto = " ".join([f.label, f.placeholder, f.name, f.id])
    if DATE_TEXT_RE.search(texto) or (field_concept(f)[0] in ("fecha", "fecha_nacimiento")):
        return "fecha_texto"
    if f.inputmode in ("numeric", "decimal") or re.fullmatch(r"\\d\{\d+(,\d+)?\}|\[0-9\]\{\d+(,\d+)?\}", f.pattern or ""):
        return "numerico_texto"
    return "texto"


def _formato_fecha_texto(f: FieldDescriptor) -> str:
    m = re.search(r"(dd|mm|aaaa|yyyy)([/\-.])(dd|mm|aaaa|yyyy)\2(dd|mm|aaaa|yyyy)", f.placeholder + " " + f.label, re.I)
    return m.group(0).lower() if m else ""


def _iso(valor: str) -> str:
    """Fecha en AAAA-MM-DD con la misma regla que usa el motor al rellenar; "" si no es una fecha reconocible."""
    try:
        return normalize_date(valor)
    except Exception:
        return ""


def _nombre_logico(f: FieldDescriptor, usados: set[str]) -> str:
    base = slug(field_concept(f)[0] or f.name or f.id or f.label or "campo", 32)
    if not re.match(r"[a-z]", base):
        base = "campo_" + base
    nombre, n = base[:40], 2
    while nombre in usados:
        nombre, n = f"{base[:36]}_{n}", n + 1
    usados.add(nombre)
    return nombre


# --------------------------------------------------------------------------- análisis (navegador)
def analizar_url(url: str, *, autorizado: bool, settings: Optional[Settings] = None,
                 expediente: Optional[dict] = None, nombre: str = "") -> dict:
    """Abre la URL autorizada UNA vez, sin ventana, la inspecciona y devuelve el informe completo.
    Lanza `UrlNotAllowed` si la política no la permite."""
    from .page_inspector import BrowserSession

    settings = settings or get_settings()
    policy = UrlPolicy.load(settings.allowed_hosts_file)
    url_ok = policy.check(url, autorizado)
    with BrowserSession(settings, headless=True) as s:
        s.open_url(url_ok, policy.guard(autorizado))
        analisis = s.inspect()
        extra = s.page.evaluate(EXTRA_JS, [f.selector for f in analisis.all_fields])
        extra["pagina"]["mutaciones_al_cargar"] = s.last_settle_changes
        extra["pagina"]["navegaciones_bloqueadas"] = s.blocked_requests
        inicial = s.initial_html
        if inicial is None and urlsplit(url_ok).scheme == "file":
            from urllib.request import url2pathname
            try:
                inicial = Path(url2pathname(urlsplit(url_ok).path)).read_text(encoding="utf-8")
            except OSError:
                inicial = None
    estatico = analyze_html(inicial) if inicial is not None else None
    return construir_informe(analisis, extra, url_ok, expediente=expediente, estatico=estatico, nombre=nombre)


# --------------------------------------------------------------------------- informe (puro, sin navegador)
def construir_informe(analisis: AnalysisResult, extra: dict, url: str, *, expediente: Optional[dict] = None,
                      estatico: Optional[AnalysisResult] = None, nombre: str = "",
                      ahora: Optional[datetime] = None) -> dict:
    ahora = ahora or datetime.now(timezone.utc)
    url_limpia = url.split("?")[0].split("#")[0]
    etiqueta = safe_url_label(url)
    p = urlsplit(url_limpia)
    base_slug = slug((p.hostname or "") + "_" + (p.path or "")) if p.scheme != "file" else slug(Path(p.path).stem)
    plano = {k: v for k, v in (expediente or {}).items() if k in M.ESQUEMA} if isinstance(expediente, dict) else {}
    rutas = list(M.CAMPOS)
    campos = analisis.all_fields
    extras = extra.get("campos") or [None] * len(campos)
    pagina = dict(extra.get("pagina") or {})

    en_estatico = None
    if estatico is not None:
        en_estatico = {(g.name, g.id) for g in estatico.all_fields}

    usados: set[str] = set()
    logicos: dict[str, str] = {}
    filas: list[dict] = []
    for f, ex in zip(campos, extras):
        ex = ex or {}
        cat = categoria(f)
        fila: dict[str, Any] = {
            "clave": f.key, "formulario": f.form_key, "selector": f.selector,
            "etiqueta": "" if f.type == "hidden" else f.label,
            "name": f.name, "id": f.id, "tag": f.tag, "tipo": f.type, "categoria": cat,
            "obligatorio": f.required, "visible": f.visible, "editable": f.fillable,
            "validaciones": {k: v for k, v in {
                "pattern": f.pattern, "minlength": f.minlength, "maxlength": f.maxlength, "min": f.min,
                "max": f.max, "step": f.step, "inputmode": f.inputmode, "bloquea_pegado": f.blocks_paste or None,
                "title": ex.get("title"), "mensajes": ex.get("mensajes") or None, "accept": ex.get("accept"),
                "atributos_data": ex.get("data_validacion") or None,
                "formato_fecha": _formato_fecha_texto(f) if cat == "fecha_texto" else "",
            }.items() if v not in (None, "", [])},
            "opciones": [{"valor": o.value, "etiqueta": o.label} for o in f.options[:200]],
            "confirma_a": f.confirms,
            "dinamico": {
                "creado_por_javascript": (en_estatico is not None and (f.name, f.id) not in en_estatico
                                          and f.type != "radio") or None,
                "oculto_condicional": (not f.visible and f.type != "hidden") or None,
                "oculto_por": ex.get("oculto_por") or None,
            },
        }
        fila["dinamico"] = {k: v for k, v in fila["dinamico"].items() if v}
        if (f.fillable or cat == "archivo") and cat not in ("oculto_tecnico", "boton"):
            logicos[f.key] = _nombre_logico(f, usados) if not f.confirms else ""
        filas.append(fila)

    por_clave = {f.key: f for f in campos}
    filas_por_clave = {r["clave"]: r for r in filas}
    equivalencias: list[dict] = []
    for r in filas:
        f = por_clave[r["clave"]]
        if f.confirms:
            continue
        rec = M.reconocer(f, campos)
        _decidir(r, f, rec, plano, analisis, expediente is not None)
        if rec.get("requiere_tabla") and r["decision"] == "revisar":
            spec = M.ESQUEMA[rec["campo"]]
            equivalencias.append({"campo_monitor": rec["campo"], "clave_campo": f.key, "etiqueta": f.label,
                                  "codigos_monitor": spec["codigos"] or "catálogo no disponible",
                                  "opciones_formulario": [{"valor": o.value, "etiqueta": o.label} for o in f.options if o.value],
                                  "tabla": {c: None for c in (spec["codigos"] or [])},
                                  "aprobada_por": None})
    for r in filas:  # las confirmaciones siguen al campo original
        f = por_clave[r["clave"]]
        if not f.confirms:
            continue
        orig = filas_por_clave.get(f.confirms, {})
        logicos[f.key] = (logicos.get(f.confirms) or "campo")[:27] + "_confirmacion"
        r["propuesta"] = dict(orig.get("propuesta") or {})
        r["decision"] = orig.get("decision", "humano")
        r["motivo"] = f"confirmación de «{orig.get('etiqueta') or f.confirms}»; se rellena con el mismo dato" \
            if r["decision"] == "automatico" else f"confirmación: sigue a «{orig.get('etiqueta') or f.confirms}» ({orig.get('decision')})"
    for r in filas:
        r["nombre_logico"] = logicos.get(r["clave"], "")

    # ------------------------------------------------------------- perfil borrador (formato de config/formularios)
    auto = [r for r in filas if r["decision"] == "automatico" and r["nombre_logico"]]
    revisar = [r for r in filas if r["decision"] == "revisar" and r["nombre_logico"]]
    perfil = {
        "version": 1,
        "formulario_id": ("frm_" + base_slug)[:44],
        "nombre": nombre or f"Borrador generado para {etiqueta}",
        "url": url_limpia,
        "perfil": ("perfil_" + base_slug)[:40],
        "campos_formulario": {r["nombre_logico"]: r["clave"] for r in auto},
        "campos_expediente": {r["nombre_logico"]: r["propuesta"]["ruta_expediente"] for r in auto},
        "_borrador": {
            "estado": "pendiente_revision_humana",
            "generado_en": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generado_por": FORMATO,
            "firma_estructura_analizada": structure_signature_v2(campos),
            "equivalencias_pendientes": equivalencias,
            "pendientes_revision": [{"nombre_logico": r["nombre_logico"], "clave_campo": r["clave"],
                                     "etiqueta": r["etiqueta"],
                                     "ruta_expediente_propuesta": (r.get("propuesta") or {}).get("ruta_expediente"),
                                     "motivo": r["motivo"]} for r in revisar],
            "nota": "Borrador NO aprobado. Revise cada correspondencia y llene las tablas de equivalencias en "
                    "vista_previa.html; para aprobarlo (perfil y tablas juntos) use `autorrellenar --formulario <este "
                    "archivo> --expediente <exp> --aprobar-perfil rev_xxx`.",
        },
    }

    conteo: dict[str, int] = {}
    for r in filas:
        conteo[r["decision"]] = conteo.get(r["decision"], 0) + 1
    return {
        "formato": FORMATO,
        "generado_en": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "url": etiqueta,
        "motor": analisis.engine,
        "solo_lectura": True,
        "envio_realizado": False,
        "bloqueo_humano": analisis.safety.to_dict(),
        "advertencias": list(analisis.warnings) + _advertencias(pagina, estatico, expediente)
                        + (M.validar_expediente(expediente) if expediente is not None else []),
        "pagina": pagina,
        "formularios": [{"clave": fm.key, "selector": fm.selector, "metodo": fm.method,
                         "botones_envio": fm.submit_buttons, "n_campos": len(fm.fields)} for fm in analisis.forms],
        "resumen": {"campos": len(filas), "automatico": conteo.get("automatico", 0),
                    "revisar": conteo.get("revisar", 0), "humano": conteo.get("humano", 0),
                    "ignorado": conteo.get("ignorado", 0)},
        "expediente": {"rutas_disponibles": rutas,
                       "esquema": {k: {"tipo": v["tipo"], "codigos": v.get("codigos")} for k, v in M.ESQUEMA.items()},
                       "modelo": "Expediente de Cauce Monitor (plano, 15 campos)",
                       "sintetico": True,
                       "nota": "Correspondencias contra el modelo real del Expediente de Monitor (campos planos), "
                               "comprobadas con un expediente SINTÉTICO. Solo se informa si hay dato y si encaja, nunca "
                               "su valor. Los códigos (trámite, operación, región, sucursal, modo) necesitan una tabla de "
                               "equivalencias aprobada."},
        "campos": filas,
        "perfil_borrador": perfil,
    }


def _decidir(r: dict, f: FieldDescriptor, rec: dict, plano: dict, analisis: AnalysisResult,
             hay_expediente: bool) -> None:
    """Decisión final de un campo: parte de lo que permite el modelo de Monitor (`rec`) y comprueba el dato sintético."""
    cat = r["categoria"]
    campo = rec.get("campo")
    r["propuesta"] = None
    if campo:
        r["propuesta"] = {"ruta_expediente": campo, "motivo": rec["motivo"], "evidencia": rec.get("evidencia", ""),
                          "dato_en_expediente": _hay_dato(plano.get(campo)) if hay_expediente else None}

    def fija(decision: str, motivo: str) -> None:
        r["decision"], r["motivo"] = decision, motivo

    if cat == "archivo" and not (f.disabled or f.readonly):
        return fija("humano", "adjuntar archivos lo hace la persona")
    if cat in ("oculto_tecnico", "boton") or not f.fillable:
        return fija("ignorado", "no editable por la persona (oculto, botón, deshabilitado o solo lectura)")
    if cat == "contrasena":
        return fija("humano", "contraseñas: nunca se rellenan automáticamente")
    if analisis.safety.blocked:
        return fija("humano", "la página exige intervención humana (CAPTCHA o marca de uso humano)")
    if rec["decision_maxima"] != "automatico":
        return fija(rec["decision_maxima"], rec["motivo"])
    p = r["propuesta"]
    if not hay_expediente:
        return fija("revisar", "sin expediente sintético para comprobar el formato del dato")
    if r["dinamico"].get("oculto_condicional"):
        return fija("revisar", "solo aparece bajo una condición; confirmar cuándo se rellena")
    if not p["dato_en_expediente"]:
        return fija("revisar", f"el expediente sintético no trae «{campo}»")
    valor = plano.get(campo)
    sval = str(valor)
    if cat == "lista":
        return fija("revisar", "lista: requiere tabla de equivalencias aprobada")
    if cat == "fecha":
        if f.type != "date":
            return fija("revisar", f"control de tipo {f.type}: confirmar el formato")
        if not _iso(sval):
            return fija("revisar", "el dato no es una fecha AAAA-MM-DD")
    elif cat == "fecha_texto":
        fmt = r["validaciones"].get("formato_fecha")
        return fija("revisar", f"fecha en texto con formato «{fmt or 'desconocido'}» y Monitor usa AAAA-MM-DD: el motor "
                               "escribe el dato tal cual; hace falta conversión aprobada")
    elif cat in ("numerico", "numerico_texto"):
        if not re.fullmatch(r"-?\d+([.,]\d+)?", re.sub(r"[\s\-()]", "", sval)):
            return fija("revisar", "el campo es numérico y el dato no lo es")
    if f.pattern:
        try:
            if not re.fullmatch(f.pattern, sval):
                return fija("revisar", "el dato sintético de Monitor no cumple el patrón del campo (pattern)")
        except re.error:
            pass
    if f.maxlength is not None and len(sval) > f.maxlength:
        return fija("revisar", "el dato sintético de Monitor supera la longitud máxima del campo")
    if f.minlength is not None and len(sval) < f.minlength:
        return fija("revisar", "el dato sintético de Monitor no llega a la longitud mínima del campo")
    fija("automatico", f"{rec['motivo']} → «{campo}» de Monitor ({rec.get('evidencia') or 'regla explícita'})")


def _advertencias(pagina: dict, estatico: Optional[AnalysisResult], expediente: Optional[dict]) -> list[str]:
    out = []
    if estatico is None:
        out.append("No se pudo leer el HTML original del servidor: no se distingue qué campos creó JavaScript.")
    if pagina.get("campos_password"):
        out.append("La página tiene campos de contraseña: puede requerir inicio de sesión. El analizador no inicia "
                   "sesión ni lo intenta.")
    if pagina.get("iframes"):
        out.append("Hay iframes: los campos dentro de ellos no se analizan.")
    if pagina.get("mutaciones_al_cargar"):
        out.append(f"La página cambió {pagina['mutaciones_al_cargar']} veces mientras cargaba (contenido dinámico): "
                   "revise que la lista de campos esté completa.")
    if not expediente:
        out.append("No se indicó expediente: no se pudo comparar con datos de Monitor.")
    return out


# --------------------------------------------------------------------------- salidas
DECISION_TXT = {"automatico": "Automático", "revisar": "Revisar", "humano": "Humano", "ignorado": "Ignorado"}


def informe_markdown(inf: dict) -> str:
    r = inf["resumen"]
    L = [f"# Análisis de formulario: {inf['url']}", "",
         f"Generado: {inf['generado_en']} · motor: {inf['motor']} · solo lectura: sí · envío realizado: no", "",
         "## Resumen", "",
         f"- Campos detectados: **{r['campos']}**",
         f"- Se pueden rellenar automáticamente: **{r['automatico']}**",
         f"- Requieren revisión de la correspondencia: **{r['revisar']}**",
         f"- Requieren intervención humana: **{r['humano']}**",
         f"- Ignorados (ocultos/técnicos): **{r['ignorado']}**", ""]
    if inf["bloqueo_humano"]["blocked"]:
        L += ["## ⚠ La página exige intervención humana", ""] + [f"- {x}" for x in inf["bloqueo_humano"]["reasons"]] + [""]
    if inf["advertencias"]:
        L += ["## Advertencias", ""] + [f"- {a}" for a in inf["advertencias"]] + [""]
    L += ["## Campos", "", "| Campo | Selector | Tipo | Oblig. | Validaciones | Dato del expediente | Decisión | Motivo |",
          "|---|---|---|---|---|---|---|---|"]
    for c in inf["campos"]:
        v = c["validaciones"]
        val = ", ".join(f"{k}={'; '.join(x) if isinstance(x, list) else x}" for k, x in v.items())
        if c["opciones"]:
            val = (val + ", " if val else "") + f"{len(c['opciones'])} opciones"
        din = ", ".join(k.replace("_", " ") for k in c["dinamico"] if k != "oculto_por")
        if din:
            val = (val + ", " if val else "") + din
        prop = c.get("propuesta") or {}
        dato = prop.get("ruta_expediente") or "—"
        esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
        L.append(f"| {esc(c['etiqueta'] or c['name'] or c['clave'])} | `{esc(c['selector'])}` | {c['categoria']} ({c['tipo']}) | "
                 f"{'sí' if c['obligatorio'] else 'no'} | {esc(val) or '—'} | {esc(dato)} | "
                 f"{DECISION_TXT.get(c['decision'], c['decision'])} | {esc(c['motivo'])} |")
    listas = [c for c in inf["campos"] if c["opciones"]]
    if listas:
        L += ["", "## Listas y opciones", ""]
        for c in listas:
            ops = ", ".join(f"{o['etiqueta']} (`{o['valor']}`)" for o in c["opciones"][:40])
            L.append(f"- **{c['etiqueta'] or c['clave']}**: {ops}")
    L += ["", "## Expediente", "", inf["expediente"]["nota"], "",
          "## Perfil borrador", "",
          f"`{inf['perfil_borrador']['perfil']}` con {len(inf['perfil_borrador']['campos_formulario'])} campos automáticos y "
          f"{len(inf['perfil_borrador']['_borrador']['pendientes_revision'])} pendientes de revisión. No está aprobado.", ""]
    return "\n".join(L) + "\n"


def vista_previa_html(inf: dict) -> str:
    """Página autónoma (sin red) para revisar el borrador y descargar el perfil revisado."""
    datos = json.dumps(inf, ensure_ascii=False).replace("</", "<\\/")
    titulo = _html.escape(inf["url"])
    return PLANTILLA_HTML.replace("__TITULO__", titulo).replace("__DATOS__", datos)


def guardar(inf: dict, carpeta: str | Path) -> dict[str, str]:
    d = Path(carpeta)
    d.mkdir(parents=True, exist_ok=True)
    rutas = {"informe_json": d / "informe.json", "informe_md": d / "informe.md",
             "perfil": d / "perfil_borrador.json", "vista_previa": d / "vista_previa.html"}
    b = lambda s: s.encode("utf-8")
    rutas["informe_json"].write_bytes(b(json.dumps(inf, ensure_ascii=False, indent=2) + "\n"))
    rutas["informe_md"].write_bytes(b(informe_markdown(inf)))
    rutas["perfil"].write_bytes(b(json.dumps(inf["perfil_borrador"], ensure_ascii=False, indent=2) + "\n"))
    rutas["vista_previa"].write_bytes(b(vista_previa_html(inf)))
    return {k: str(v) for k, v in rutas.items()}


PLANTILLA_HTML = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>Revisión de perfil</title>
<style>
:root{--bg:#fafaf9;--fg:#1c1917;--mut:#57534e;--line:#e7e5e4;--card:#fff;--auto:#166534;--rev:#a16207;--hum:#b91c1c;--ign:#78716c}
@media (prefers-color-scheme:dark){:root{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--line:#44403c;--card:#292524;--auto:#4ade80;--rev:#facc15;--hum:#f87171;--ign:#a8a29e}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0 0 4px} .mut{color:var(--mut)}
.res{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
.res div{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px}
.res b{font-size:18px;display:block}
.aviso{border-left:4px solid var(--hum);background:var(--card);padding:8px 12px;margin:8px 0}
.tabla{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;min-width:900px} th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:12px;color:var(--mut);position:sticky;top:0;background:var(--card)}
code{font-size:12px;word-break:break-all} select,input[type=text]{font:inherit;max-width:220px}
.d-automatico{color:var(--auto);font-weight:600}.d-revisar{color:var(--rev);font-weight:600}.d-humano{color:var(--hum);font-weight:600}.d-ignorado{color:var(--ign)}
.barra{position:sticky;bottom:0;background:var(--bg);padding:12px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{font:inherit;padding:8px 14px;border-radius:6px;border:1px solid var(--line);background:var(--fg);color:var(--bg);cursor:pointer}
tr.eq td{background:var(--bg);font-size:13px} tr.eq div{margin:4px 0}
pre{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);padding:8px;border-radius:8px;max-height:300px;overflow:auto}
</style></head><body><main>
<h1>Revisión del perfil de autorrellenado</h1>
<div class="mut">Formulario: <b>__TITULO__</b> · análisis de solo lectura, no se envió nada · los valores del expediente no se muestran</div>
<div id="res" class="res"></div><div id="avisos"></div>
<p class="mut">Marque «Incluir» solo en los campos que el sistema puede rellenar con el dato indicado. Lo que no marque lo completa la persona en el formulario.
Al terminar, descargue el perfil revisado. Seguirá siendo un borrador hasta aprobarlo con <code>--aprobar-perfil rev_xxx</code>.</p>
<div class="tabla"><table><thead><tr><th>Incluir</th><th>Campo</th><th>Selector</th><th>Tipo</th><th>Validaciones</th><th>Dato del expediente</th><th>Propuesta</th><th>Motivo</th></tr></thead><tbody id="filas"></tbody></table></div>
<div class="barra"><button id="descargar">Descargar perfil revisado</button><span id="cuenta" class="mut"></span></div>
<details><summary>Perfil resultante (JSON)</summary><pre id="json"></pre></details>
</main>
<script>
const INF = __DATOS__;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const R = INF.resumen;
$("#res").innerHTML = [["Campos", R.campos], ["Automático", R.automatico], ["Revisar", R.revisar], ["Humano", R.humano], ["Ignorados", R.ignorado]]
  .map(([k, v]) => `<div><b>${v}</b>${k}</div>`).join("");
$("#avisos").innerHTML = (INF.bloqueo_humano.blocked ? INF.bloqueo_humano.reasons : []).concat(INF.advertencias, [INF.expediente.nota])
  .map((a) => `<div class="aviso">${esc(a)}</div>`).join("");
const rutas = INF.expediente.rutas_disponibles;
const filas = INF.campos.filter((c) => c.nombre_logico);
const EQ = Object.fromEntries((INF.perfil_borrador._borrador.equivalencias_pendientes || []).map((e) => [e.clave_campo, e]));
const ESQ = INF.expediente.esquema;
const FORMATOS = ["AAAA-MM-DD", "DD/MM/AAAA", "DD-MM-AAAA", "DD.MM.AAAA", "MM/DD/AAAA", "AAAA/MM/DD"];
$("#filas").innerHTML = filas.map((c, i) => {
  const p = c.propuesta || {};
  const opts = ['<option value="">— sin dato —</option>'].concat(rutas.map((r) => `<option ${r === p.ruta_expediente ? "selected" : ""}>${esc(r)}</option>`),
    ['<option value="@manual">captura manual (se escribe al rellenar, un solo uso)</option>']).join("");
  const fmtDet = String(c.validaciones.formato_fecha || "").toUpperCase().replace(/YYYY/g, "AAAA");
  const fmt = c.categoria === "fecha_texto" ? `<br><label class="mut">formato: <select data-fmt="${i}">` +
    FORMATOS.map((f) => `<option ${f === (fmtDet || "AAAA-MM-DD") ? "selected" : ""}>${f}</option>`).join("") + `</select></label>` : "";
  const val = Object.entries(c.validaciones).map(([k, v]) => `${esc(k)}=${esc(Array.isArray(v) ? v.join("; ") : v)}`)
    .concat(c.opciones.length ? [`${c.opciones.length} opciones`] : [], Object.keys(c.dinamico).filter((k) => k !== "oculto_por").map((k) => k.replace(/_/g, " "))).join("<br>");
  const bloqueado = c.decision === "humano" && ["archivo", "contrasena"].includes(c.categoria) || INF.bloqueo_humano.blocked;
  const eq = EQ[c.clave];
  let tabla = "";
  if (eq) {
    const opsF = ['<option value="">— sin fila —</option>'].concat(eq.opciones_formulario.map((o) => `<option value="${esc(o.valor)}">${esc(o.etiqueta)} (${esc(o.valor)})</option>`)).join("");
    const cods = Array.isArray(eq.codigos_monitor) ? eq.codigos_monitor : ["", "", ""];
    tabla = `<tr class="eq"><td></td><td colspan="7"><b>Tabla de equivalencias</b> (${esc(eq.campo_monitor)} de Monitor → opción del formulario). Solo las filas llenas se usan; un código sin fila lo completa la persona.<br>` +
      cods.map((cd, k) => `<div>${Array.isArray(eq.codigos_monitor) ? `<code>${esc(cd)}</code>` : `<input type="text" placeholder="código de Monitor" data-eqc="${i}-${k}">`} → <select data-eqv="${i}-${k}" data-cod="${esc(cd)}">${opsF}</select></div>`).join("") + `</td></tr>`;
  }
  return `<tr><td><input type="checkbox" data-i="${i}" ${c.decision === "automatico" ? "checked" : ""} ${bloqueado ? "disabled" : ""}></td>
  <td><b>${esc(c.etiqueta || c.name || c.clave)}</b>${c.obligatorio ? " *" : ""}<br><span class="mut">${esc(c.nombre_logico)}</span></td>
  <td><code>${esc(c.selector)}</code></td><td>${esc(c.categoria)}<br><span class="mut">${esc(c.tipo)}</span></td><td>${val || "—"}</td>
  <td><select data-r="${i}" ${bloqueado ? "disabled" : ""}>${opts}</select>${fmt}</td>
  <td class="d-${esc(c.decision)}">${esc(c.decision)}</td><td>${esc(c.motivo)}</td></tr>` + tabla;
}).join("");
function perfil() {
  const base = JSON.parse(JSON.stringify(INF.perfil_borrador));
  base.campos_formulario = {}; base.campos_expediente = {}; base.equivalencias = {}; base.formatos_fecha = {}; base.etiquetas = {};
  const incl = [];
  filas.forEach((c, i) => {
    const on = document.querySelector(`[data-i="${i}"]`).checked;
    const ruta = document.querySelector(`[data-r="${i}"]`).value;
    if (!on || !ruta) return;
    if (ruta === "@manual") base.etiquetas[c.nombre_logico] = c.etiqueta || c.name || c.clave;
    const fsel = document.querySelector(`[data-fmt="${i}"]`);
    if (fsel && ESQ[ruta] && ESQ[ruta].tipo === "fecha" && fsel.value !== "AAAA-MM-DD") base.formatos_fecha[c.nombre_logico] = fsel.value;
    if (ESQ[ruta] && ESQ[ruta].tipo === "codigo") {
      // Un código de Monitor solo entra con tabla: se toman las filas llenas
      const valores = {};
      document.querySelectorAll(`[data-eqv^="${i}-"]`).forEach((sel) => {
        const k = sel.dataset.eqv.split("-")[1];
        const inp = document.querySelector(`[data-eqc="${i}-${k}"]`);
        const cod = (inp ? inp.value : sel.dataset.cod).trim();
        if (cod && sel.value) valores[cod] = sel.value;
      });
      if (!Object.keys(valores).length) return;
      base.equivalencias[c.nombre_logico] = {campo_monitor: ruta, valores};
    }
    base.campos_formulario[c.nombre_logico] = c.clave; base.campos_expediente[c.nombre_logico] = ruta; incl.push(c.nombre_logico);
  });
  base._borrador.estado = "revisado_pendiente_aprobacion";
  base._borrador.revisado_en_vista_previa = new Date().toISOString();
  base._borrador.pendientes_revision = [];
  base._borrador.equivalencias_pendientes = [];
  if (!Object.keys(base.equivalencias).length) delete base.equivalencias;
  if (!Object.keys(base.formatos_fecha).length) delete base.formatos_fecha;
  if (!Object.keys(base.etiquetas).length) delete base.etiquetas;
  $("#cuenta").textContent = `${incl.length} campos incluidos`;
  return base;
}
function refrescar() { $("#json").textContent = JSON.stringify(perfil(), null, 2); }
document.addEventListener("change", refrescar); document.addEventListener("input", refrescar); refrescar();
$("#descargar").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(perfil(), null, 2) + "\n"], {type: "application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = INF.perfil_borrador.perfil + ".json"; a.click();
});
</script></body></html>
"""
