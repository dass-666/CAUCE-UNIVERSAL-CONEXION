"""Sirve la aplicación de Forms con uvicorn real en 127.0.0.1 (como en test_web_app)."""
from __future__ import annotations

import contextlib
import threading
import time

import httpx
import uvicorn

from conftest import _free_port


@contextlib.contextmanager
def servir(app):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(200):
        try:
            httpx.get(base + "/api/status")
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    try:
        yield base
    finally:
        server.should_exit = True
        t.join(10)
