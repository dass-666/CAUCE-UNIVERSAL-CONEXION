"""Firma de estructura v2 (corrige H4).

La firma v1 era solo `clave:tipo`: cambiar la etiqueta de «Correo» a «Documento» no la alteraba. La v2 incluye,
por campo: clave, etiqueta normalizada, etiquetas auxiliares (aria-label, placeholder), tipo y etiqueta HTML,
obligatoriedad, patrón, longitudes mínima/máxima, límites, autocompletar y las opciones (valor + etiqueta
normalizada, sin el estado «seleccionada», que cambia al rellenar). No incluye visibilidad, selector ni valores.

Formato: `v2:<16 hex>` (primeros 16 caracteres de SHA-256 de un JSON canónico). Es la forma que exige el esquema
`cauce.conector.v1` (`^v2:[0-9a-f]{16}$`); Monitor solo la compara, no la calcula.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable

from .models import FieldDescriptor

SIGNATURE_V2_RE = re.compile(r"^v2:[0-9a-f]{16}$")


def normalize_label(text: str) -> str:
    """Minúsculas, sin acentos, espacios colapsados, sin signos de obligatoriedad (`*`, `:`)."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[*:¿?¡!]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def field_fingerprint(f: FieldDescriptor | dict[str, Any]) -> dict[str, Any]:
    d = f.to_dict() if isinstance(f, FieldDescriptor) else f
    # Listas personalizadas: sus opciones pueden no existir en la página hasta abrirlas, así que no entran en la firma
    # (la opción se busca y se verifica al rellenar; si ya no existe, el campo queda como incidencia).
    opts = [] if d.get("type") == "custom-select" else sorted(
        (str(o.get("value", "")), normalize_label(str(o.get("label", "")))) for o in d.get("options", []))
    return {
        "key": d.get("key", ""), "tag": d.get("tag", ""), "type": d.get("type", ""),
        "label": normalize_label(d.get("label", "")), "aria": normalize_label(d.get("aria_label", "")),
        "placeholder": normalize_label(d.get("placeholder", "")),
        "required": bool(d.get("required")), "pattern": d.get("pattern", "") or "",
        "minlength": d.get("minlength"), "maxlength": d.get("maxlength"),
        "min": d.get("min", "") or "", "max": d.get("max", "") or "", "step": d.get("step", "") or "",
        "autocomplete": d.get("autocomplete", "") or "", "options": opts,
        "confirms": d.get("confirms", "") or "",
    }


def structure_signature_v2(fields: Iterable[FieldDescriptor | dict[str, Any]]) -> str:
    prints = sorted((field_fingerprint(f) for f in fields), key=lambda x: x["key"])
    raw = json.dumps(prints, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "v2:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


_SAFE_KEY = re.compile(r"[^A-Za-z0-9_:.-]")


def safe_key(key: str) -> str:
    """Clave apta para la respuesta del contrato (`^[A-Za-z0-9_:.-]{1,60}$`)."""
    return _SAFE_KEY.sub("_", key)[:60] or "_"


def diff_structure(saved: list[dict[str, Any]], current: list[FieldDescriptor]) -> dict[str, Any]:
    """Qué cambió entre los campos aprobados y los actuales, con los nombres de categoría del contrato.
    Nunca incluye etiquetas ni valores: solo claves y el tipo de cambio."""
    old = {f["key"]: field_fingerprint(f) for f in saved}
    new = {f.key: field_fingerprint(f) for f in current}
    mod: list[dict[str, str]] = []
    cats = (("etiqueta", ("label", "aria", "placeholder")), ("tipo", ("type", "tag")),
            ("requerido", ("required",)), ("patron", ("pattern",)),
            ("longitud", ("minlength", "maxlength", "min", "max", "step")),
            ("opciones", ("options",)), ("autocompletar", ("autocomplete",)))
    for k in sorted(old.keys() & new.keys()):
        for cat, props in cats:
            if any(old[k][p] != new[k][p] for p in props):
                mod.append({"campo": safe_key(k), "cambio": cat})
    return {"faltan": [safe_key(k) for k in sorted(old.keys() - new.keys())][:64],
            "nuevos": [safe_key(k) for k in sorted(new.keys() - old.keys())][:64],
            "modificados": mod[:64]}
