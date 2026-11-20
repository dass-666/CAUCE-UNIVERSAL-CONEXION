"""Motor de correspondencia campo <-> columna de datos.

Funciona sin IA ni servicios externos, con tres señales en orden de fuerza:
1. Igualdad exacta (normalizada) entre `name`/`id` del campo y la columna.
2. Concepto común (p. ej. "correo", "e-mail", autocomplete=email -> EMAIL),
   usando un diccionario de sinónimos en español e inglés.
3. Similitud de texto (difflib) entre la columna y etiqueta/name/placeholder.

Cada propuesta lleva confianza (0-1), motivo legible y estado:
- auto: confianza alta, se puede usar tal cual (igual se muestra para revisión).
- revisar: confianza media o ambigüedad entre dos columnas.
- sin_datos: no hay columna razonable.

Un `AmbiguityResolver` opcional (por ejemplo, un modelo de IA local o de pago)
puede sugerir columnas para los casos "revisar"; su sugerencia nunca se marca
como "auto": siempre requiere confirmación humana.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional, Protocol

from .form_detector import strip_accents
from .models import FieldDescriptor, Mapping

AUTO_THRESHOLD = 0.80
REVIEW_THRESHOLD = 0.50
AMBIGUITY_MARGIN = 0.05

# (concepto, patrones). El orden importa: lo más específico primero.
CONCEPTS: list[tuple[str, list[str]]] = [
    ("empresa", [r"empresa", r"compan", r"razon social", r"organiza", r"\borg\b", r"company"]),
    ("cargo", [r"cargo", r"puesto", r"job ?title", r"\btitle\b", r"position"]),
    ("email", [r"e-?mail", r"correo", r"\bmail\b"]),
    ("telefono", [r"tel[eé]?f", r"\btel\b", r"phone", r"movil", r"celular", r"whatsapp", r"\bcel\b"]),
    ("nombre_completo", [r"nombre completo", r"full ?name", r"nombre y apellido"]),
    ("apellido2", [r"segundo apellido", r"apellido materno", r"apellido ?2"]),
    ("apellido", [r"apellido", r"last ?name", r"surname", r"family ?name"]),
    ("nombre", [r"nombre", r"first ?name", r"given ?name", r"\bname\b"]),
    ("documento", [r"\bdni\b", r"\bnif\b", r"\bnie\b", r"cedula", r"\bcurp\b", r"\brfc\b", r"documento",
                   r"pasaporte", r"identifica", r"\bid ?number\b"]),
    ("fecha_nacimiento", [r"nacimiento", r"birth", r"\bdob\b", r"cumplea"]),
    ("fecha", [r"fecha", r"\bdate\b"]),
    ("codigo_postal", [r"postal", r"\bcp\b", r"\bzip\b", r"postcode"]),
    ("ciudad", [r"ciudad", r"municipio", r"localidad", r"\bcity\b", r"poblacion"]),
    ("provincia", [r"provincia", r"estado", r"region", r"\bstate\b"]),
    ("pais", [r"pais", r"country", r"nacionalidad"]),
    ("direccion", [r"direcc", r"domicilio", r"\bcalle\b", r"address", r"street"]),
    ("genero", [r"genero", r"sexo", r"gender"]),
    ("tipo_solicitante", [r"tipo de solicitante", r"tipo solicitante", r"tipo_solicitante",
                          r"solicitante", r"tipo de cliente"]),
    ("mensaje", [r"mensaje", r"comentario", r"observacion", r"consulta", r"message", r"comment"]),
    ("acepta_terminos", [r"acept", r"termino", r"condicion", r"privacidad", r"terms", r"consent"]),
    ("boletin", [r"boletin", r"newsletter", r"suscrib", r"novedades"]),
    ("password", [r"contrasen", r"password", r"clave"]),
    ("web", [r"\bweb\b", r"sitio", r"\burl\b", r"website"]),
    ("edad", [r"\bedad\b", r"\bage\b"]),
]

# Valores estándar de autocomplete (WHATWG) -> concepto
AUTOCOMPLETE_CONCEPTS = {
    "email": "email", "tel": "telefono", "tel-national": "telefono", "given-name": "nombre",
    "family-name": "apellido", "name": "nombre_completo", "organization": "empresa",
    "organization-title": "cargo", "postal-code": "codigo_postal", "address-level2": "ciudad",
    "address-level1": "provincia", "country": "pais", "country-name": "pais",
    "street-address": "direccion", "address-line1": "direccion", "bday": "fecha_nacimiento",
    "sex": "genero", "url": "web", "new-password": "password", "current-password": "password",
}


class AmbiguityResolver(Protocol):
    """Interfaz opcional para resolver casos ambiguos (p. ej. con IA)."""

    def suggest(self, field: FieldDescriptor, columns: list[str]) -> Optional[str]: ...


def normalize(text: str) -> str:
    t = strip_accents(text or "").lower()
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", t)
    t = re.sub(r"[_\-.\[\]:*()¿?/]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _split_camel(text: str) -> str:
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")


def concept_of_text(text: str) -> Optional[str]:
    t = normalize(_split_camel(text))
    if not t:
        return None
    for concept, pats in CONCEPTS:
        if any(re.search(p, t) for p in pats):
            return concept
    return None


def field_concept(f: FieldDescriptor) -> tuple[Optional[str], str]:
    token = (f.autocomplete or "").split()[-1] if f.autocomplete else ""
    if token in AUTOCOMPLETE_CONCEPTS:
        return AUTOCOMPLETE_CONCEPTS[token], f"autocomplete={token}"
    if f.type == "email":
        return "email", "type=email"
    if f.type == "tel":
        return "telefono", "type=tel"
    for source, text in (("etiqueta", f.label), ("name", f.name), ("id", f.id),
                         ("placeholder", f.placeholder), ("aria-label", f.aria_label)):
        c = concept_of_text(text)
        if c:
            # "fecha" genérica en un campo date con etiqueta de nacimiento ya se cubre arriba
            return c, f"{source} «{text}»"
    return None, ""


def _similarity(column: str, f: FieldDescriptor) -> float:
    col = normalize(_split_camel(column))
    best = 0.0
    for text in (f.label, f.name, f.id, f.placeholder, f.aria_label):
        t = normalize(_split_camel(text))
        if not t:
            continue
        best = max(best, SequenceMatcher(None, col, t).ratio())
        ct, tt = set(col.split()), set(t.split())
        if ct and tt:
            best = max(best, len(ct & tt) / len(ct | tt))
            # Columna contenida en la etiqueta ("Notas" en "Notas internas"): parecido parcial
            best = max(best, 0.8 * len(ct & tt) / len(ct))
    return best


def score(f: FieldDescriptor, column: str) -> tuple[float, str]:
    ncol = normalize(_split_camel(column))
    for attr in ("name", "id"):
        v = getattr(f, attr)
        if v and normalize(_split_camel(v)) == ncol:
            return 0.97, f"{attr} del campo igual a la columna"
    fc, why = field_concept(f)
    cc = concept_of_text(column)
    if fc and cc and fc == cc:
        conf = 0.95 if why.startswith("autocomplete") else 0.90
        return conf, f"mismo concepto «{fc}» ({why})"
    sim = _similarity(column, f)
    if fc and cc and fc != cc:
        sim *= 0.5   # conceptos distintos detectados: penaliza
    return round(sim * 0.85, 3), f"similitud de texto {sim:.0%}"


def match_fields(fields: list[FieldDescriptor], columns: list[str],
                 resolver: Optional[AmbiguityResolver] = None) -> list[Mapping]:
    candidates = [f for f in fields if f.fillable and not f.confirms]
    scored: dict[str, list[tuple[float, str, str]]] = {}
    for f in candidates:
        scored[f.key] = sorted(((*score(f, c), c) for c in columns), key=lambda x: -x[0])

    # Asignación voraz: cada columna se usa una sola vez (salvo confirmaciones)
    pairs = sorted(((s, why, c, f.key) for f in candidates for (s, why, c) in scored[f.key]),
                   key=lambda x: -x[0])
    used_cols: set[str] = set()
    assigned: dict[str, Mapping] = {}
    for s, why, col, fkey in pairs:
        if fkey in assigned or col in used_cols or s < REVIEW_THRESHOLD:
            continue
        ranking = scored[fkey]
        status = "auto" if s >= AUTO_THRESHOLD else "revisar"
        others = [x for x in ranking if x[2] != col and x[2] not in used_cols]
        if others and s - others[0][0] < AMBIGUITY_MARGIN:
            status = "revisar"
            why += f"; ambiguo con «{others[0][2]}»"
        assigned[fkey] = Mapping(fkey, col, round(s, 3), why, status)
        used_cols.add(col)

    by_key = {f.key: f for f in fields}
    out: list[Mapping] = []
    for f in fields:
        if not f.fillable:
            continue
        if f.confirms:
            orig = assigned.get(f.confirms)
            out.append(Mapping(f.key, orig.column if orig else None, orig.confidence if orig else 0.0,
                               f"confirmación de «{by_key[f.confirms].label or f.confirms}»",
                               orig.status if orig else "sin_datos"))
            continue
        m = assigned.get(f.key)
        if m is None:
            m = Mapping(f.key, None, 0.0, "sin columna parecida", "sin_datos")
            if resolver is not None:
                free = [c for c in columns if c not in used_cols]
                sug = resolver.suggest(f, free) if free else None
                if sug in free:
                    m = Mapping(f.key, sug, 0.5, "sugerencia del resolvedor opcional (IA)", "revisar")
                    used_cols.add(sug)
        out.append(m)
    return out


def apply_manual(mappings: list[Mapping], overrides: dict[str, Optional[str]]) -> list[Mapping]:
    """Aplica correcciones humanas {field_key: columna | None}."""
    out = []
    for m in mappings:
        if m.field_key in overrides:
            col = overrides[m.field_key] or None
            out.append(Mapping(m.field_key, col, 1.0 if col else 0.0, "corregido manualmente",
                               "manual" if col else "ignorado"))
        else:
            out.append(m)
    return out
