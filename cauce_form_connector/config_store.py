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
from .signature import structure_signature_v2

PROFILE_VERSION = 1                 # perfiles de la interfaz (correspondencia campo -> columna)
INTEGRATION_PROFILE_VERSION = 2     # perfiles aprobados para el modo `cauce.conector.v1` (ver `save_integration`)
READABLE_VERSIONS = (1, 2)


def structure_signature(fields: list[FieldDescriptor]) -> str:
    """Firma v1 (claves + tipos). OBSOLETA: no detecta cambios de etiqueta ni de restricciones (H4).
    Se conserva solo para leer perfiles anteriores; use `signature.structure_signature_v2`."""
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
            "structure_signature_v2": structure_signature_v2(fields),
            "fields": [{k: v for k, v in f.to_dict().items()} for f in fields],
            "mapping": {k: v for k, v in mapping.items() if k in {f.key for f in fields}},
        }
        path = self.dir / f"{_safe_name(name)}.json"
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, name: str) -> dict[str, Any]:
        path = self.dir / f"{_safe_name(name)}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") not in READABLE_VERSIONS:
            raise ValueError(f"Versión de perfil no compatible: {data.get('version')}")
        return data

    def path_of(self, name: str) -> Path:
        return self.dir / f"{_safe_name(name)}.json"

    @staticmethod
    def sha256_of_file(path: str | Path) -> str:
        """Huella SHA-256 (hex, 64) de los bytes EXACTOS del archivo del perfil: es la que fija Monitor."""
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def save_integration(self, name: str, analysis: AnalysisResult, campos: dict[str, str], *,
                         formulario_id: str, url: str, aprobado_por_ref: str,
                         equivalencias: Optional[dict[str, Any]] = None) -> tuple[Path, str]:
        """Guarda un perfil APROBADO para el modo de integración. `campos` = {nombre lógico: clave del campo}.
        Lo escribe una persona (o un proceso que ella ejecuta) tras revisar el formulario real: nada se
        completa con sugerencias. Devuelve (ruta, sha256 del archivo)."""
        fields = analysis.all_fields
        keys = {f.key for f in fields}
        desconocidos = sorted(set(campos.values()) - keys)
        if desconocidos or not campos or len(set(campos.values())) != len(campos):
            raise ValueError("Los campos aprobados deben existir en el formulario y no repetirse.")
        self.dir.mkdir(parents=True, exist_ok=True)
        profile = {
            "version": INTEGRATION_PROFILE_VERSION,
            "name": name,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": analysis.source, "source_label": analysis.source_label,
            "structure_signature_v2": structure_signature_v2(fields),
            "fields": [f.to_dict() for f in fields],
            "mapping": {},
            "integracion": {"aprobado": True, "formulario_id": formulario_id, "url": url,
                            "aprobado_por_ref": aprobado_por_ref, "campos": dict(campos)},
        }
        if equivalencias:
            # Tablas código de Monitor -> valor de opción, aprobadas junto con el perfil: la huella SHA-256 las protege
            profile["integracion"]["equivalencias"] = equivalencias
        path = self.path_of(name)
        # Bytes explícitos (LF, UTF-8): la huella no debe depender del sistema operativo (en Windows write_text() usa CRLF)
        path.write_bytes((json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        return path, self.sha256_of_file(path)

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
            # v2 si el perfil la tiene; si no (perfil anterior) se compara con la v1 y se avisa
            "same_structure": (structure_signature_v2(analysis.all_fields) == profile["structure_signature_v2"]
                               if profile.get("structure_signature_v2")
                               else structure_signature(analysis.all_fields) == profile.get("structure_signature")),
            "legacy_signature": not profile.get("structure_signature_v2"),
        }
