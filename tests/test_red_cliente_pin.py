"""El cliente copiado sigue fijado a Network Engine RC3 (y la copia no ganó capacidades administrativas)."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from ne_proceso import NE_REPO

ROOT = Path(__file__).resolve().parents[1]
PIN = (ROOT / "cauce_form_connector" / "red" / "PIN.txt").read_text("utf-8")
CLIENTE = (ROOT / "cauce_form_connector" / "red" / "cliente_ne.py").read_text("utf-8")


def test_pin_declara_rc3():
    assert "commit: f93dd58eeddc1cc3032711bb0b6e378708063d41" in PIN


@pytest.mark.skipif(not NE_REPO, reason="CAUCE_NE_REPO no definido")
def test_los_archivos_de_origen_coinciden_con_el_pin():
    for ruta, sha in re.findall(r"^sha256 (\S+): ([0-9a-f]{64})$", PIN, re.M):
        # Windows (autocrlf) convierte a CRLF al descargar: la huella fijada es la del contenido con LF
        real = hashlib.sha256((Path(NE_REPO) / ruta).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        assert real == sha, f"{ruta} cambió en Network Engine: revisar la copia de Forms antes de actualizar el PIN"


@pytest.mark.skipif(not NE_REPO, reason="CAUCE_NE_REPO no definido")
def test_puerto_y_cuerpos_iguales_al_original():
    orig = (Path(NE_REPO) / "cauce" / "client.py").read_text("utf-8")
    assert re.search(r"^DEFAULT_API_PORT = (\d+)", orig, re.M).group(1) == \
        re.search(r"^DEFAULT_API_PORT = (\d+)", CLIENTE, re.M).group(1)
    for fragmento in ('body = {"label": label, "authorized_by_user": True, "heartbeat_timeout_s": heartbeat_timeout_s}',
                      'body["error_code"] = error_code or "other"', 'body["operation"] = operation',
                      '"/sessions/{self._sid()}/heartbeat"', 'ProxyHandler({})'):
        assert fragmento in orig and fragmento in CLIENTE, fragmento


def test_la_copia_no_tiene_rutas_administrativas():
    codigo = CLIENTE.split('"""', 2)[2]                    # sin el docstring del módulo
    for prohibido in ("/tokens", "/routes", "/tasks", "/rotation", "/shutdown", "/resolve", "/sessions/pending",
                      "/status/connectivity", "/selection", "/stop-all", "def write_secret", "CryptProtectData("):
        assert prohibido not in codigo, prohibido
