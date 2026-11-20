"""Revisión de seguridad v0.1: aislamiento del HTML importado, restricciones de URL,
API local frente a otros sitios y tratamiento temporal de datos personales."""
import functools
import http.server
import threading
from pathlib import Path

import httpx
import pytest

from cauce_form_connector.url_policy import UrlNotAllowed, UrlPolicy
from conftest import _free_port

pytestmark = pytest.mark.browser


@pytest.fixture()
def spy_server():
    """Servidor local que registra cualquier petición que le llegue."""
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"secreto-local")

        do_POST = do_GET

        def log_message(self, *a):
            pass

    port = _free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port, hits
    srv.shutdown()


def test_imported_html_cannot_reach_local_services(session, spy_server):
    port, hits = spy_server
    base = f"http://127.0.0.1:{port}"
    html = f"""<form><input name=a></form>
    <img src="{base}/img"><link rel=stylesheet href="{base}/css"><iframe src="{base}/iframe"></iframe>
    <script>
      window.r = {{}};
      fetch('{base}/fetch').then(r => r.text()).then(t => r.fetch = t).catch(() => r.fetch = 'bloqueado');
      fetch('file:///etc/hosts').then(r => r.text()).then(t => r.file = t).catch(() => r.file = 'bloqueado');
      const ws = new WebSocket('ws://127.0.0.1:{port}/ws'); ws.onopen = () => r.ws = 'abierto';
      navigator.sendBeacon('{base}/beacon', 'x');
      const p = window.open('{base}/popup');
    </script>"""
    session.open_html(html)
    session.page.wait_for_timeout(600)
    r = session.page.evaluate("window.r || {}")
    pages = len(session._context.pages)
    assert hits == [], f"el HTML importado llegó a un servicio local: {hits}"
    assert r.get("fetch") == "bloqueado" and r.get("file") == "bloqueado" and "ws" not in r
    assert pages == 1, "las ventanas emergentes deben cerrarse"
    assert session.blocked_requests >= 1


def test_imported_html_cannot_navigate_away(session, spy_server):
    port, hits = spy_server
    session.open_html(f"<form><input name=a></form><script>location.href = 'http://127.0.0.1:{port}/fuga?d=' + 1</script>")
    session.page.wait_for_timeout(500)
    assert hits == []


def test_authorized_page_cannot_redirect_to_unauthorized_host(session, tmp_path):
    (tmp_path / "f.html").write_text(
        "<form><input name=a></form><script>setTimeout(() => location.href = 'http://10.255.255.1/interno', 100)</script>",
        encoding="utf-8")
    port = _free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port),
                                          functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{port}/f.html"
        session.open_url(url, UrlPolicy().guard(False))
        session.page.wait_for_timeout(800)
        # La navegación se aborta antes de salir del equipo; el navegador muestra su página de error
        assert "10.255.255.1" not in session.page.url
        assert session.blocked_requests >= 1
    finally:
        srv.shutdown()


def test_file_urls_limited_to_html():
    p = UrlPolicy()
    assert p.check("file:///C:/lab/form.HTML", False)
    for bad in ("file:///etc/passwd", "file:///C:/Users/x/Documents/datos.xlsx"):
        with pytest.raises(UrlNotAllowed):
            p.check(bad, False)
    guard = p.guard(False)
    assert guard("http://127.0.0.1/x") and not guard("https://tercero.example/x") and not guard("file:///etc/passwd")


@pytest.fixture(scope="module")
def app_server(tmp_path_factory):
    import os
    import time
    import uvicorn
    tmp = tmp_path_factory.mktemp("sec")
    os.environ["CAUCE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
    os.environ["CAUCE_PROFILES_DIR"] = str(tmp / "profiles")
    from cauce_form_connector.web.app import create_app
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/api/status")
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield base, tmp
    server.should_exit = True
    t.join(5)


def test_cross_site_fetch_metadata_rejected(app_server):
    base, _ = app_server
    r = httpx.post(base + "/api/close", json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = httpx.post(base + "/api/data", files={"file": ("a.csv", b"a\n1\n")}, headers={"Sec-Fetch-Site": "same-site"})
    assert r.status_code == 403
    assert httpx.get(base + "/api/status", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_screenshot_not_persisted_and_close_discards_data(app_server):
    base, tmp = app_server
    c = httpx.Client(base_url=base, timeout=60)
    data = Path(__file__).resolve().parents[1] / "sample_data" / "personas_ficticias.json"
    assert c.post("/api/data", files={"file": ("p.json", data.read_bytes())}).status_code == 200
    assert c.post("/api/analyze", json={"mode": "lab", "lab": "01_tradicional.html"}).status_code == 200
    before = set(Path(".").rglob("*.png"))
    r = c.get("/api/screenshot")
    assert r.headers["content-type"] == "image/png" and r.headers["cache-control"] == "no-store"
    assert set(Path(".").rglob("*.png")) == before, "la captura no debe guardarse en disco"
    assert c.get("/api/status").json()["rows"] == 2
    assert c.post("/api/close", json={}).status_code == 200
    assert c.get("/api/status").json() == {**c.get("/api/status").json(), "rows": 0, "loaded": False}
