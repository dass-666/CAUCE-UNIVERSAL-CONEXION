"""Configuración por variables de entorno (todas opcionales)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "si", "sí", "yes")


@dataclass
class Settings:
    # Con ventana visible (False) el usuario ve el navegador y puede revisar y enviar él mismo.
    headless: bool = field(default_factory=lambda: _bool("CAUCE_HEADLESS", False))
    # Ruta a un Chromium concreto; vacío = el que instala `playwright install chromium`.
    browser_path: str = field(default_factory=lambda: os.environ.get("CAUCE_BROWSER_PATH", ""))
    # Pausa fija entre pulsaciones. Es FIJA a propósito: no se "humaniza" con
    # pausas aleatorias porque eso sería una técnica de evasión anti-bot.
    typing_delay_ms: int = field(default_factory=lambda: int(os.environ.get("CAUCE_TYPING_DELAY_MS", "35")))
    # Espera máxima a que el DOM deje de cambiar tras cargar o interactuar.
    settle_quiet_ms: int = field(default_factory=lambda: int(os.environ.get("CAUCE_SETTLE_QUIET_MS", "400")))
    settle_max_ms: int = field(default_factory=lambda: int(os.environ.get("CAUCE_SETTLE_MAX_MS", "5000")))
    nav_timeout_ms: int = field(default_factory=lambda: int(os.environ.get("CAUCE_NAV_TIMEOUT_MS", "20000")))
    audit_path: str = field(default_factory=lambda: os.environ.get("CAUCE_AUDIT_PATH", "logs/audit.jsonl"))
    profiles_dir: str = field(default_factory=lambda: os.environ.get("CAUCE_PROFILES_DIR", "profiles"))
    allowed_hosts_file: str = field(default_factory=lambda: os.environ.get("CAUCE_ALLOWED_HOSTS_FILE", "config/allowed_hosts.json"))
    # Carpeta de datos propios de Forms: identidad del puesto, registro de operaciones y su clave de huella.
    data_dir: str = field(default_factory=lambda: os.environ.get("CAUCE_FORMS_DATA_DIR", "state"))
    # --- Network Engine (OPCIONAL). Sin archivo de credencial, Forms funciona sin diagnóstico de red. ---
    # Credencial de ámbito «session» creada por el administrador: cauce token create --name forms-puesto-1
    # --scope session --out ARCHIVO (DPAPI en Windows). Nunca la credencial admin ni la «review» de Monitor.
    ne_token_file: str = field(default_factory=lambda: os.environ.get("CAUCE_NE_SESSION_TOKEN_FILE", ""))
    ne_port: int = field(default_factory=lambda: int(os.environ.get("CAUCE_NE_PORT", "8780")))
    ne_connection_id: str = field(default_factory=lambda: os.environ.get("CAUCE_NE_CONNECTION_ID", ""))
    ne_heartbeat_s: float = field(default_factory=lambda: float(os.environ.get("CAUCE_NE_LATIDO_S", "7")))
    # Etiqueta legible y estable del puesto (opcional). Sin ella se genera una al azar: forms-xxxxxxxx.
    puesto: str = field(default_factory=lambda: os.environ.get("CAUCE_FORMS_PUESTO", ""))


def get_settings() -> Settings:
    return Settings()
