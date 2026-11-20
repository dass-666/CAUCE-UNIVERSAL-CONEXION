"""Analizador HTML estático (sin navegador).

Lee el HTML tal cual llega, sin ejecutar JavaScript. Es rápido y no necesita
Chromium, pero NO ve campos creados por scripts: para eso está
`page_inspector.py`. Produce la misma estructura cruda que `detector.js`
para reutilizar `form_detector.build_analysis`.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from .form_detector import build_analysis
from .models import AnalysisResult

CONTROLS = ["input", "select", "textarea"]


def html_fingerprint(html: str) -> str:
    """Huella corta del HTML: identifica la fuente en auditoría sin guardar su contenido."""
    return "html:" + hashlib.sha256(html.encode("utf-8", "ignore")).hexdigest()[:16]


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _int(v: Any) -> int | None:
    try:
        n = int(str(v))
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def _css_escape(s: str) -> str:
    return re.sub(r"([^\w-])", r"\\\1", s)


class _Ctx:
    def __init__(self, soup: BeautifulSoup):
        self.soup = soup
        self.ids: dict[str, int] = {}
        self.names: dict[tuple[str, str], int] = {}
        for el in soup.find_all(True):
            if el.get("id"):
                self.ids[el["id"]] = self.ids.get(el["id"], 0) + 1
        for el in soup.find_all(CONTROLS):
            if el.get("name"):
                k = (el.name, el["name"])
                self.names[k] = self.names.get(k, 0) + 1

    def css_path(self, el: Tag) -> str:
        parts = []
        node: Any = el
        while isinstance(node, Tag) and node.name not in ("[document]", "html"):
            if node.get("id") and self.ids.get(node["id"]) == 1:
                parts.insert(0, "#" + _css_escape(node["id"]))
                break
            sel = node.name
            parent = node.parent
            if isinstance(parent, Tag):
                same = [c for c in parent.find_all(node.name, recursive=False)]
                if len(same) > 1:
                    sel += f":nth-of-type({same.index(node) + 1})"
            parts.insert(0, sel)
            node = node.parent
        return " > ".join(parts)

    def selector(self, el: Tag) -> str:
        if el.get("id") and self.ids.get(el["id"]) == 1:
            return "#" + _css_escape(el["id"])
        if el.get("name") and self.names.get((el.name, el["name"])) == 1:
            return f'{el.name}[name="{el["name"]}"]'
        return self.css_path(el)

    def label(self, el: Tag) -> str:
        if el.get("id"):
            lab = self.soup.find("label", attrs={"for": el["id"]})
            if lab:
                return _clean(lab.get_text(" "))
        wrap = el.find_parent("label")
        if wrap:
            texts = [t for t in wrap.find_all(string=True) if t.parent.name not in CONTROLS]
            t = _clean(" ".join(texts))
            if t:
                return t
        lb = el.get("aria-labelledby")
        if lb:
            t = " ".join(_clean(self.soup.find(id=i).get_text(" ")) for i in lb.split() if self.soup.find(id=i))
            if t.strip():
                return t.strip()
        prev = el.find_previous_sibling()
        while prev is not None:
            if prev.name in CONTROLS + ["button"]:
                break
            t = _clean(prev.get_text(" "))
            if t and len(t) < 120:
                return t
            prev = prev.find_previous_sibling()
        return ""


def _describe(el: Tag, ctx: _Ctx) -> dict[str, Any]:
    tag = el.name
    typ = (el.get("type") or "text").lower() if tag == "input" else tag
    if tag == "select":
        typ = "select-multiple" if el.has_attr("multiple") else "select"
    d: dict[str, Any] = {
        "tag": tag, "type": typ, "name": el.get("name", ""), "id": el.get("id", ""),
        "selector": ctx.selector(el), "label": ctx.label(el),
        "placeholder": el.get("placeholder", ""), "aria_label": el.get("aria-label", ""),
        "autocomplete": (el.get("autocomplete") or "").lower(),
        "required": el.has_attr("required") or el.get("aria-required") == "true",
        "disabled": el.has_attr("disabled"), "readonly": el.has_attr("readonly"),
        # Sin renderizar no se puede saber la visibilidad real; se asume visible salvo hidden/estilo inline
        "visible": typ != "hidden" and "display:none" not in (el.get("style") or "").replace(" ", ""),
        "pattern": el.get("pattern", ""), "minlength": _int(el.get("minlength")),
        "maxlength": _int(el.get("maxlength")), "min": el.get("min", ""), "max": el.get("max", ""),
        "step": el.get("step", ""), "inputmode": el.get("inputmode", ""),
        "blocks_paste": el.has_attr("onpaste") or el.has_attr("data-no-paste"),
        "confirm_hint": el.get("data-confirm") or el.get("data-match") or "",
        "value": el.get("value", "on") if typ in ("checkbox", "radio") else "",
        "checked": el.has_attr("checked"), "options": [],
    }
    if tag == "select":
        d["options"] = [{"value": o.get("value", _clean(o.get_text())), "label": _clean(o.get_text()),
                         "selected": o.has_attr("selected")} for o in el.find_all("option")]
    if typ == "radio":
        fs = el.find_parent("fieldset")
        lg = fs.find("legend") if fs else None
        d["group_label"] = _clean(lg.get_text(" ")) if lg else ""
    return d


def analyze_html(html: str) -> AnalysisResult:
    soup = BeautifulSoup(html, "lxml")
    ctx = _Ctx(soup)
    raw: dict[str, Any] = {"forms": [], "orphan_fields": [], "signals": {}}
    for i, form in enumerate(soup.find_all("form")):
        fields = [_describe(el, ctx) for el in form.find_all(CONTROLS)]
        submits = []
        for b in form.find_all(["button", "input"]):
            t = (b.get("type") or ("submit" if b.name == "button" else "")).lower()
            if t in ("submit", "image"):
                submits.append(_clean(b.get_text(" ")) or b.get("value", "Enviar"))
        raw["forms"].append({
            "index": i, "selector": ("#" + form["id"]) if form.get("id") else ctx.css_path(form),
            "name": form.get("name", ""), "id": form.get("id", ""), "action": form.get("action", ""),
            "method": (form.get("method") or "get").lower(), "fields": fields, "submit_buttons": submits,
        })
    raw["orphan_fields"] = [_describe(el, ctx) for el in soup.find_all(CONTROLS) if not el.find_parent("form")]

    low = html.lower()
    meta = soup.find("meta", attrs={"name": re.compile("^(automation|robots-automation)$")})
    raw["signals"] = {
        "captcha_iframe": bool(re.search(r"<iframe[^>]+(recaptcha|hcaptcha|turnstile|captcha)", low)),
        "captcha_widget": bool(soup.select(".g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey]")),
        "captcha_text": bool(re.search(r"no soy un robot|i'm not a robot|verify you are human", low)),
        "meta_no_automation": bool(meta and re.search("none|deny|human", meta.get("content", ""), re.I)),
        "human_only_markers": len(soup.select("[data-human-only]")),
    }
    result = build_analysis(raw, "html", html_fingerprint(html), engine="static")
    if soup.find("script"):
        result.warnings.append("El HTML contiene <script>: el análisis estático no ejecuta JavaScript; "
                               "pueden existir campos dinámicos no listados. Use el análisis con navegador.")
    return result
