"""Registro de auditoría sin información personal sensible.

Reglas:
- NUNCA se registran valores escritos ni datos de la fila de origen.
- De cada campo solo se guarda: clave técnica, tipo, método, estado y número de
  caracteres enviados.
- Las URLs se guardan sin query ni fragmento.
- Cualquier valor de texto libre se pasa por `redact()` como defensa adicional
  (correos, teléfonos y números largos se sustituyen).

Formato: JSON Lines (una línea JSON por evento), fácil de ingerir por otros sistemas.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
LONG_NUMBER_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")

# Claves permitidas en un evento. Cualquier otra se descarta.
ALLOWED_KEYS = {
    "event", "run_id", "ts", "source", "source_label", "engine", "forms", "fields", "blocked",
    "reasons", "field_key", "type", "method", "status", "chars", "column", "confidence",
    "mapping_status", "ok", "duration_ms", "count", "profile", "detail", "warnings",
    "rows", "columns", "format", "stopped_before_submit", "submit_buttons", "confirmations_ok",
    "submit_observed", "solicitud_id", "codigo", "estado", "options",
}


def redact(text: str) -> str:
    text = EMAIL_RE.sub("[correo]", text)
    text = LONG_NUMBER_RE.sub("[número]", text)
    return text


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)[:300]
    if isinstance(value, list):
        return [_sanitize(v) for v in value][:200]
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    return value


class AuditLog:
    def __init__(self, path: str | Path | None = "logs/audit.jsonl"):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.run_id = uuid.uuid4().hex[:12]
        self.events: list[dict[str, Any]] = []   # copia en memoria para la UI/pruebas

    def new_run(self) -> str:
        self.run_id = uuid.uuid4().hex[:12]
        return self.run_id

    def log(self, event: str, **data: Any) -> dict[str, Any]:
        entry = {"event": event, "run_id": self.run_id,
                 "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        for k, v in data.items():
            if k == "source_label":
                entry[k] = str(v)[:300]   # ya viene saneada por safe_url_label / html_fingerprint
            elif k in ALLOWED_KEYS:
                entry[k] = _sanitize(v)
        with self._lock:
            self.events.append(entry)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
