"""Configuraciones reutilizables ("perfiles") de formularios reconocidos.

Un perfil guarda la estructura de campos detectada (sin valores) y la
correspondencia campo -> columna validada por una persona. Al volver a abrir
el mismo formulario, se carga el perfil y se reaplica la correspondencia.

Formato: JSON legible (ver `docs/CONTRATO_CAUCE_MONITOR.md`).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import AnalysisResult, FieldDescriptor

PROFILE_VERSION = 1


def structure_signature(fields: list[FieldDescriptor]) -> str:
    """Huella de la estructura (claves + tipos). Sirve para detectar si el formulario cambió."""
    sig = "|".join(f"{f.key}:{f.type}" for f in sorted(fields, key=lambda x: x.key))
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def _safe_name(name: str) -> str:
    n = re.sub(r"[^\w\-]+", "_", name.strip())[:80].strip("_")
    if not n:
        raise ValueError("Nombre de perfil vacío o no válido.")
    return n


class ProfileStore:
    def __init__(self, directory: str | Path = "profiles"):
        self.dir = Path(directory)

    def save(self, name: str, analysis: AnalysisResult, mapping: dict[str, Optional[str]]) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        fields = analysis.all_fields
        profile = {
            "version": PROFILE_VERSION,
            "name": name,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": analysis.source,
            "source_label": analysis.source_label,
            "structure_signature": structure_signature(fields),
            "fields": [{k: v for k, v in f.to_dict().items()} for f in fields],
            "mapping": {k: v for k, v in mapping.items() if k in {f.key for f in fields}},
        }
        path = self.dir / f"{_safe_name(name)}.json"
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, name: str) -> dict[str, Any]:
        path = self.dir / f"{_safe_name(name)}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != PROFILE_VERSION:
            raise ValueError(f"Versión de perfil no compatible: {data.get('version')}")
        return data

    def list(self) -> list[dict[str, Any]]:
        if not self.dir.exists():
            return []
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({"name": d.get("name", p.stem), "source_label": d.get("source_label", ""),
                            "saved_at": d.get("saved_at", ""), "fields": len(d.get("fields", []))})
            except (json.JSONDecodeError, OSError):
                continue
        return out

    @staticmethod
    def reconcile(profile: dict[str, Any], analysis: AnalysisResult) -> dict[str, Any]:
        """Compara el perfil con el formulario actual y devuelve la correspondencia aplicable."""
        current = {f.key for f in analysis.all_fields}
        saved = set(profile.get("mapping", {}))
        return {
            "mapping": {k: v for k, v in profile["mapping"].items() if k in current},
            "missing_fields": sorted(saved - current),       # estaban en el perfil y ya no existen
            "new_fields": sorted(current - {f["key"] for f in profile.get("fields", [])}),
            "same_structure": structure_signature(analysis.all_fields) == profile.get("structure_signature"),
        }
