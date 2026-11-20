"""Política de URLs autorizadas.

Por defecto solo se permiten direcciones locales (localhost / 127.0.0.1 / ::1)
y archivos locales. Cualquier otro dominio debe añadirse explícitamente a la
lista de permitidos y, además, el usuario debe declarar que tiene autorización
para automatizar esa página. Esto no sustituye una revisión legal: sirve para
que la herramienta no se use por accidente contra sitios de terceros.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UrlNotAllowed(ValueError):
    pass


@dataclass
class UrlPolicy:
    allowed_hosts: set[str] = field(default_factory=set)
    allow_local: bool = True
    allow_file: bool = True

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "UrlPolicy":
        """Carga `allowed_hosts.json` ({"allowed_hosts": ["intranet.ejemplo.com"]})."""
        p = Path(path or os.environ.get("CAUCE_ALLOWED_HOSTS_FILE", "config/allowed_hosts.json"))
        hosts: set[str] = set()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            hosts = {h.strip().lower() for h in data.get("allowed_hosts", []) if h.strip()}
        return cls(allowed_hosts=hosts)

    def check(self, url: str, user_declares_authorization: bool) -> str:
        """Devuelve la URL si está permitida; lanza `UrlNotAllowed` si no."""
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        if scheme == "file":
            if not self.allow_file:
                raise UrlNotAllowed("Las URLs file:// están desactivadas.")
            # Solo documentos HTML: evita usar el navegador para abrir otros archivos locales
            if not parts.path.lower().endswith((".html", ".htm")):
                raise UrlNotAllowed("Solo se pueden abrir archivos locales .html o .htm.")
            return url
        if scheme not in ("http", "https"):
            raise UrlNotAllowed(f"Esquema no admitido: {scheme or '(vacío)'}. Use http, https o file.")
        host = (parts.hostname or "").lower()
        if not host:
            raise UrlNotAllowed("La URL no tiene dominio.")
        if host in LOCAL_HOSTS and self.allow_local:
            return url
        if not user_declares_authorization:
            raise UrlNotAllowed("Debe declarar que tiene autorización para automatizar esta página.")
        if not any(host == h or host.endswith("." + h) for h in self.allowed_hosts):
            raise UrlNotAllowed(
                f"El dominio '{host}' no está en la lista de permitidos (config/allowed_hosts.json)."
            )
        return url


    def guard(self, user_declares_authorization: bool) -> "Callable[[str], bool]":
        """Función para `BrowserSession.open_url`: ¿se permite navegar a esta URL?
        Bloquea redirecciones o enlaces de una página autorizada hacia destinos no autorizados."""
        def _ok(url: str) -> bool:
            if url.startswith(("about:", "data:", "blob:")):
                return True
            try:
                self.check(url, user_declares_authorization)
                return True
            except UrlNotAllowed:
                return False
        return _ok


def safe_url_label(url: str) -> str:
    """Etiqueta para auditoría: esquema + host + ruta, sin query ni fragmento (pueden llevar tokens)."""
    p = urlsplit(url)
    if p.scheme == "file":
        return "file://" + Path(p.path).name
    return f"{p.scheme}://{p.hostname or ''}{p.path or '/'}"
