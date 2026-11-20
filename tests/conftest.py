"""Fixtures compartidas.

- `lab_url(name)`: sirve lab_forms/ por HTTP local en un puerto libre.
- `session`: navegador Chromium sin ventana, con un script que REGISTRA los
  eventos de teclado de cada campo (window.__keylog). Ese registro es la
  evidencia de que la escritura fue secuencial y no una asignación directa.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import socket
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab_forms"
DATA = ROOT / "sample_data"

os.environ.setdefault("CAUCE_HEADLESS", "1")
os.environ.setdefault("CAUCE_TYPING_DELAY_MS", "0")
os.environ.setdefault("CAUCE_SETTLE_QUIET_MS", "300")
# En contenedores con Chromium preinstalado se puede indicar su ruta
if not os.environ.get("CAUCE_BROWSER_PATH") and Path("/opt/pw-browsers/chromium").exists():
    os.environ["CAUCE_BROWSER_PATH"] = "/opt/pw-browsers/chromium"

from cauce_form_connector.instrumentation import KEYLOG_JS  # noqa: E402


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # silencio en la salida de pytest
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def lab_server():
    port = _free_port()
    handler = functools.partial(_Quiet, directory=str(LAB))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


@pytest.fixture(scope="session")
def lab_url(lab_server):
    return lambda name: f"{lab_server}/{name}"


@pytest.fixture(scope="session")
def rows():
    from cauce_form_connector.data_adapter import load_json
    return load_json((DATA / "personas_ficticias.json").read_bytes())


@pytest.fixture(scope="module")
def session():
    from cauce_form_connector.page_inspector import BrowserSession
    s = BrowserSession()
    s.add_init_script(KEYLOG_JS)
    s.start()
    yield s
    s.close()


def keylog(session) -> dict:
    return session.page.evaluate("window.__keylog || {}")


def value_sets(session) -> dict:
    return session.page.evaluate("window.__valueSets || {}")


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)
