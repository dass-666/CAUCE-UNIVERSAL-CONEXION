"""Interfaz local: API y flujo completo en la página, con un servidor real."""
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from conftest import DATA, _free_port

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    import os
    tmp = tmp_path_factory.mktemp("app")
    os.environ["CAUCE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
    os.environ["CAUCE_PROFILES_DIR"] = str(tmp / "profiles")
    from cauce_form_connector.web.app import create_app
    port = _free_port()
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/api/status")
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield {"base": base, "app": app, "tmp": tmp}
    server.should_exit = True
    t.join(5)


def test_api_full_flow(live):
    c = httpx.Client(base_url=live["base"], timeout=60)
    assert "03_validaciones.html" in c.get("/api/lab").json()
    r = c.post("/api/data", files={"file": ("p.xlsx", (DATA / "personas_ficticias.xlsx").read_bytes())})
    assert r.status_code == 200 and r.json()["rows"] == 2
    r = c.post("/api/analyze", json={"mode": "lab", "lab": "03_validaciones.html"})
    assert r.status_code == 200, r.text
    props = {p["field_key"]: p for p in r.json()["proposals"]}
    assert props["alta::email_confirmacion"]["column"] == "Correo electrónico"
    mapping = {k: p["column"] for k, p in props.items()}
    r = c.post("/api/fill", json={"mapping": mapping, "row_index": 0, "delay_ms": 0})
    rep = r.json()["report"]
    assert rep["ok"] and rep["stopped_before_submit"] and all(x["match"] for x in rep["confirmations"])
    # Guardar y recuperar la configuración
    assert c.post("/api/profiles", json={"name": "lab3", "mapping": mapping}).status_code == 200
    prof = c.get("/api/profiles/lab3").json()
    assert prof["same_structure"] and prof["mapping"] == mapping
    # Captura de pantalla disponible para la revisión
    assert c.get("/api/screenshot").headers["content-type"] == "image/png"
    # El envío exige la frase exacta
    assert c.post("/api/submit", json={"phrase": "vale"}).status_code == 403
    assert c.post("/api/submit", json={"phrase": "ENVIAR"}).json()["submitted"]


def test_api_rejects_unauthorized_urls(live):
    c = httpx.Client(base_url=live["base"], timeout=30)
    r = c.post("/api/analyze", json={"mode": "url", "url": "https://www.ejemplo-tercero.com/form", "authorized": True})
    assert r.status_code == 403 and "lista de permitidos" in r.json()["detail"]
    r = c.post("/api/analyze", json={"mode": "lab", "lab": "../../secreto.html"})
    assert r.status_code == 404


def test_api_static_html_and_bad_data(live):
    c = httpx.Client(base_url=live["base"], timeout=30)
    r = c.post("/api/analyze", json={"mode": "html", "engine": "static", "html": "<form><input name=email type=email></form>"})
    assert r.status_code == 200 and r.json()["analysis"]["engine"] == "static"
    r = c.post("/api/data", files={"file": ("x.pdf", b"%PDF")})
    assert r.status_code == 400


def test_audit_log_contains_no_personal_data(live, rows):
    text = Path(live["tmp"] / "audit.jsonl").read_text(encoding="utf-8")
    assert '"event": "field_filled"' in text
    for row in rows:
        for v in row.values():
            if len(v) >= 4 and v.lower() not in {"true", "false"}:
                assert v not in text, f"dato personal en auditoría: {v[:6]}…"


def test_ui_end_to_end(live, tmp_path):
    """Maneja la propia interfaz con Playwright, como lo haría una persona."""
    from playwright.sync_api import sync_playwright
    import os
    with sync_playwright() as p:
        kw = {"executable_path": os.environ["CAUCE_BROWSER_PATH"]} if os.environ.get("CAUCE_BROWSER_PATH") else {}
        b = p.chromium.launch(**kw)
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(live["base"] + "/")
        pg.select_option("#lab-sel", "02_dinamico.html")
        pg.set_input_files("#data-file", str(DATA / "personas_ficticias.csv"))
        pg.wait_for_selector("#datos-msg.ok")
        pg.click("#btn-analizar")
        pg.wait_for_selector("#analisis-msg.ok")
        pg.wait_for_selector("#map-tabla select")
        pg.select_option("#row-sel", "1")    # fila de empresa
        pg.fill("#delay", "0")
        pg.click("#btn-fill")
        pg.wait_for_selector("#fill-msg.ok, #fill-msg.aviso, #fill-msg.error", timeout=60000)
        assert "Detenido antes del envío" in pg.text_content("#fill-msg")
        assert "Tostadores Ficticios" in pg.text_content("#resultados")
        assert "apareció al rellenar" in pg.text_content("#resultados")
        out = Path(os.environ.get("CAUCE_EVIDENCE_DIR", tmp_path))
        out.mkdir(parents=True, exist_ok=True)
        pg.wait_for_selector("#captura img")
        pg.wait_for_function("document.querySelector('#captura img').complete")
        pg.screenshot(path=str(out / "ui_revision.png"), full_page=True)
        b.close()


def test_cross_origin_and_foreign_host_rejected(live):
    c = httpx.Client(base_url=live["base"], timeout=10)
    r = c.post("/api/submit", json={"phrase": "ENVIAR"}, headers={"Origin": "https://sitio-malicioso.example"})
    assert r.status_code == 403
    r = c.get("/api/status", headers={"Host": "atacante.example"})
    assert r.status_code == 403
    assert c.get("/api/status").status_code == 200


def test_ui_escapes_labels_from_imported_html(live):
    """Una etiqueta maliciosa en el HTML importado no se ejecuta en la interfaz."""
    import os
    from playwright.sync_api import sync_playwright
    base = live["base"]
    evil = '<form><label for=x>&lt;img src=z onerror="window.top.__xss=1"&gt;</label><input id=x name=x></form>'
    with sync_playwright() as p:
        kw = {"executable_path": os.environ["CAUCE_BROWSER_PATH"]} if os.environ.get("CAUCE_BROWSER_PATH") else {}
        b = p.chromium.launch(**kw)
        pg = b.new_page()
        pg.goto(base + "/")
        pg.click('[data-tab="html"]')
        pg.fill("#html-in", evil)
        pg.click("#btn-analizar")
        pg.wait_for_selector("#analisis-msg.ok")
        assert "<img src=z" in pg.text_content("#campos")
        assert pg.evaluate("window.__xss") is None
        b.close()


def test_api_combos_catalogo_equivalencias_y_perfil(live):
    """Combos editables en la interfaz: pendiente → catálogo → equivalencia aprobada → perfil reutilizable."""
    c = httpx.Client(base_url=live["base"], timeout=90)
    csv = "pais,estado\nMé,Jal\n".encode("utf-8")
    assert c.post("/api/data", files={"file": ("c.csv", csv)}).status_code == 200
    r = c.post("/api/analyze", json={"mode": "lab", "lab": "05_combos_extjs.html"})
    assert r.status_code == 200, r.text
    mapping = {"ext_form::pais": "pais", "ext_form::estado": "estado"}
    rep = c.post("/api/fill", json={"mapping": mapping, "row_index": 0, "delay_ms": 0}).json()["report"]
    por = {x["field_key"]: x for x in rep["results"]}
    assert not rep["ok"] and {por[k]["status"] for k in mapping} == {"pendiente_revision"}
    cat = c.post("/api/catalog").json()["catalog"]
    assert "México" in cat["ext_form::pais"] and "Jalisco" in cat["ext_form::estado"]
    eq = {"ext_form::pais": {"Mé": "México"}, "ext_form::estado": {"Jal": "Jalisco"}}
    rep = c.post("/api/fill", json={"mapping": mapping, "row_index": 0, "delay_ms": 0, "equivalences": eq}).json()["report"]
    por = {x["field_key"]: x for x in rep["results"]}
    assert all(por[k]["status"] == "ok" and por[k]["equivalence_used"] for k in mapping)
    # La equivalencia aprobada se guarda y se recupera con el perfil
    assert c.post("/api/profiles", json={"name": "combos", "mapping": mapping, "equivalences": eq}).status_code == 200
    prof = c.get("/api/profiles/combos").json()
    assert prof["same_structure"] and prof["equivalences"] == eq
