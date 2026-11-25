"""Detector de formularios: normaliza la salida cruda del navegador o del
analizador estático en `FormDescriptor` / `FieldDescriptor`.

Responsabilidades:
- Agrupar radios con el mismo `name` en un único campo con opciones.
- Asignar claves estables a cada campo.
- Detectar pares de confirmación (correo/teléfono/contraseña repetidos).
- Traducir señales de CAPTCHA o de "solo humanos" a un `SafetyReport`.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import (
    AnalysisResult, FieldDescriptor, FieldOption, FormDescriptor, SafetyReport,
)

CONFIRM_RE = re.compile(
    r"(confirm|repet|repit|verific|vuelv|again|retype|re-?enter|otra vez|nuevamente)", re.I,
)
# Sufijo "2" (email2, tel_2): solo se acepta como confirmación en tipos sensibles,
# para no confundir "direccion2" (segunda línea de dirección) con una confirmación.
WEAK_CONFIRM_RE = re.compile(r"(_|-)?2$")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _field_from_raw(raw: dict[str, Any], form_key: str) -> FieldDescriptor:
    opts = [FieldOption(**o) for o in raw.get("options", [])]
    return FieldDescriptor(
        key="", selector=raw.get("selector", ""), tag=raw.get("tag", "input"),
        type=raw.get("type", "text"), name=raw.get("name", ""), id=raw.get("id", ""),
        label=raw.get("label", ""), placeholder=raw.get("placeholder", ""),
        aria_label=raw.get("aria_label", ""), autocomplete=raw.get("autocomplete", ""),
        required=bool(raw.get("required")), disabled=bool(raw.get("disabled")),
        readonly=bool(raw.get("readonly")), visible=bool(raw.get("visible", True)),
        pattern=raw.get("pattern", ""), minlength=raw.get("minlength"),
        maxlength=raw.get("maxlength"), min=raw.get("min", ""), max=raw.get("max", ""),
        step=raw.get("step", ""), inputmode=raw.get("inputmode", ""), options=opts,
        form_key=form_key, blocks_paste=bool(raw.get("blocks_paste")),
        widget=raw.get("widget", "") or "", trigger_selector=raw.get("trigger_selector", "") or "",
        value_selector=raw.get("value_selector", "") or "",
    )


def _group_fields(raw_fields: list[dict[str, Any]], form_key: str) -> list[FieldDescriptor]:
    """Convierte la lista cruda en campos, agrupando radios por nombre."""
    out: list[FieldDescriptor] = []
    radio_groups: dict[str, FieldDescriptor] = {}
    hints: dict[int, str] = {}
    # El campo oculto que guarda el valor interno de un combo es parte del combo: no es un campo propio
    internos = {r.get("value_selector") for r in raw_fields if r.get("widget") == "combo" and r.get("value_selector")}
    for raw in raw_fields:
        if raw.get("type") == "hidden" and raw.get("selector") in internos:
            continue
        if raw.get("type") == "radio" and raw.get("name"):
            name = raw["name"]
            opt = FieldOption(value=raw.get("value", "on"), label=raw.get("label", ""),
                              selected=bool(raw.get("checked")))
            if name not in radio_groups:
                fd = _field_from_raw(raw, form_key)
                fd.selector = f'input[type="radio"][name="{name}"]'
                fd.label = raw.get("group_label") or raw.get("label", "")
                fd.id = ""
                fd.options = []
                radio_groups[name] = fd
                out.append(fd)
            group = radio_groups[name]
            group.options.append(opt)
            group.required = group.required or bool(raw.get("required"))
            group.visible = group.visible or bool(raw.get("visible", True))
            continue
        fd = _field_from_raw(raw, form_key)
        if raw.get("confirm_hint"):
            hints[len(out)] = raw["confirm_hint"]
        out.append(fd)

    _assign_keys(out, form_key)
    _detect_confirmations(out, hints)
    return out


AUTO_ID_RE = re.compile(r"^(ext-(comp|gen)-?\d+|ember\d+|:r[0-9a-z]+:|react-select-\d+.*)$", re.I)


def _assign_keys(fields: list[FieldDescriptor], form_key: str) -> None:
    seen: dict[str, int] = {}
    for i, f in enumerate(fields):
        stable_id = "" if AUTO_ID_RE.match(f.id or "") else f.id      # un id autogenerado no es una clave estable
        base = f.name or stable_id or f"campo{i + 1}"
        if f.type == "checkbox" and f.name and sum(1 for x in fields if x.name == f.name) > 1:
            base = f"{f.name}[{f.id or i}]"
        n = seen.get(base, 0)
        seen[base] = n + 1
        f.key = f"{form_key}::{base}" + (f"#{n + 1}" if n else "")


def _text_signature(f: FieldDescriptor) -> str:
    return strip_accents(" ".join([f.label, f.name, f.id, f.placeholder, f.aria_label])).lower()


def _detect_confirmations(fields: list[FieldDescriptor], hints: dict[int, str]) -> None:
    for idx, f in enumerate(fields):
        if f.type not in {"email", "tel", "password", "text", "number"}:
            continue
        # Pista explícita: data-confirm="#email" o data-match="email"
        hint = hints.get(idx, "").lstrip("#")
        if hint:
            for g in fields[:idx]:
                if hint in (g.id, g.name):
                    f.confirms = g.key
                    break
            if f.confirms:
                continue
        sig = _text_signature(f)
        strong = bool(CONFIRM_RE.search(sig))
        weak = f.type in {"email", "tel", "password"} and bool(WEAK_CONFIRM_RE.search(f.name or f.id or ""))
        if not (strong or weak):
            continue
        # Buscar hacia atrás el campo original más cercano del mismo tipo
        for g in reversed(fields[:idx]):
            if g.confirms or g.type != f.type:
                continue
            if f.type == "text" and not _share_concept(f, g):
                continue
            f.confirms = g.key
            break


def _share_concept(a: FieldDescriptor, b: FieldDescriptor) -> bool:
    ta = set(re.findall(r"[a-z]{4,}", _text_signature(a)))
    tb = set(re.findall(r"[a-z]{4,}", _text_signature(b)))
    return bool(ta & tb)


def safety_from_signals(signals: dict[str, Any]) -> SafetyReport:
    reasons = []
    if signals.get("captcha_iframe") or signals.get("captcha_widget") or signals.get("captcha_text"):
        reasons.append("Se detectó un CAPTCHA o verificación anti-bot: requiere intervención humana.")
    if signals.get("meta_no_automation"):
        reasons.append("La página declara que no admite automatización (meta automation).")
    if signals.get("human_only_markers"):
        reasons.append("La página marca campos como de uso exclusivamente humano (data-human-only).")
    return SafetyReport(blocked=bool(reasons), reasons=reasons)


def build_analysis(raw: dict[str, Any], source: str, source_label: str, engine: str) -> AnalysisResult:
    forms: list[FormDescriptor] = []
    for rf in raw.get("forms", []):
        fkey = rf.get("id") or rf.get("name") or f"form{rf.get('index', len(forms)) + 1}"
        fd = FormDescriptor(
            key=fkey, selector=rf.get("selector", "form"), name=rf.get("name", ""),
            id=rf.get("id", ""), action=rf.get("action", ""), method=rf.get("method", "get"),
            submit_buttons=list(rf.get("submit_buttons", [])),
        )
        fd.fields = _group_fields(rf.get("fields", []), fkey)
        forms.append(fd)
    orphans = raw.get("orphan_fields", [])
    if orphans:
        fd = FormDescriptor(key="sin_form", selector="body", name="(campos fuera de <form>)",
                            submit_buttons=list(raw.get("orphan_submits", [])))
        fd.fields = _group_fields(orphans, "sin_form")
        forms.append(fd)

    result = AnalysisResult(source=source, source_label=source_label, forms=forms,
                            safety=safety_from_signals(raw.get("signals", {})), engine=engine)
    if not result.all_fields:
        result.warnings.append("No se detectaron campos. Si la página los genera con JavaScript, "
                               "use el análisis con navegador o aumente la espera.")
    return result
