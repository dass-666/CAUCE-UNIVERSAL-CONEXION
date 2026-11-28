"""Hallazgos A2, A3, A6 y A7 de la auditoría E10 (SOLICITUDES_RONALDO_E10.md), comprobados uno por uno.

A1 y A5 están en tests/test_red_ne_e10.py (contra el motor E10 real). Portal propio de laboratorio y datos ficticios.
Lo que NO prueba: Windows físico (A6 y A7 se ejecutan también en la CI de Windows, que es una máquina virtual).
"""
from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from cauce_form_connector.operaciones import (CONFIRMADA, INCIERTA, MAX_CERRADAS, OperacionBloqueada,
                                              RegistroOperaciones)

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ A2 · el límite de 500 no olvida lo recibido
def test_a2_tras_501_confirmadas_la_primera_sigue_bloqueando(tmp_path):
    """La prueba que propone la auditoría, con el límite real (sin reducirlo)."""
    reg = RegistroOperaciones(tmp_path)
    huellas = ["h_" + f"{i:024x}" for i in range(MAX_CERRADAS + 1)]
    for i, h in enumerate(huellas):
        op = reg.iniciar(h, "lab", None, None)
        reg.cerrar(op["id"], CONFIRMADA, "acuse_verificado", folio=f"LAB-{i:06d}")
    assert len(reg.lista(10_000)) == MAX_CERRADAS                      # el detalle se recorta…
    for r in (reg, RegistroOperaciones(tmp_path)):                     # …pero el expediente 0 sigue bloqueado
        with pytest.raises(OperacionBloqueada) as e:
            r.comprobar(huellas[0])
        assert e.value.codigo == "expediente_confirmada"


# ------------------------------------------------------------------ A3 · caminos de envío fuera del registro
def test_a3_solo_las_pruebas_importan_el_envio_sin_registro():
    """``engine.submit_with_human_confirmation`` (sin registro de operaciones) no lo usa ningún módulo del producto."""
    usan = []
    for py in (ROOT / "cauce_form_connector").rglob("*.py"):
        arbol = ast.parse(py.read_text("utf-8"))
        for n in ast.walk(arbol):
            nombres = []
            if isinstance(n, ast.ImportFrom):
                nombres = [a.name for a in n.names]
            elif isinstance(n, ast.Attribute):
                nombres = [n.attr]
            elif isinstance(n, ast.Name):
                nombres = [n.id]
            if "submit_with_human_confirmation" in nombres:
                usan.append(py.relative_to(ROOT).as_posix())
    assert usan == [], usan


@pytest.mark.browser
def test_a3_pagina_que_pide_enviar_desde_el_navegador_y_la_persona_envia_queda_incierta(tmp_path, monkeypatch):
    """Tras «envíe desde el navegador» (aparece un CAPTCHA después del relleno), la persona envía a mano: queda
    INCIERTA y ese expediente no se vuelve a enviar hasta que un operador diga qué pasó."""
    import httpx
    from lab_portal.servidor import ServidorPortal
    from servidor_app import servir
    from cauce_form_connector.url_policy import UrlPolicy
    from cauce_form_connector.web import app as web
    portal = ServidorPortal().iniciar()
    monkeypatch.setenv("CAUCE_FORMS_DATA_DIR", str(tmp_path / "forms"))
    monkeypatch.setenv("CAUCE_NE_SESSION_TOKEN_FILE", "")
    app = web.create_app()
    ws = app.state.ws
    try:
        with servir(app) as base:
            s = ws.browser()
            fila = {"expediente": "EXP-0801", "nombre": "Persona Ficticia 801", "correo": "ficticia801@example.test",
                    "telefono": "55 1000 0801", "tipo": "alta"}
            ws.rows = [fila]
            pol = UrlPolicy(allow_file=False)
            ws.worker.run(lambda: s.open_url(pol.check(portal.url + "/tramite", False), pol.guard(False)))
            campos = ws.worker.run(lambda: s.inspect().all_fields)
            r = httpx.post(base + "/api/fill", json={"mapping": {f.key: f.name for f in campos if f.name in fila},
                                                     "row_index": 0, "reload_first": False, "delay_ms": 0}, timeout=60)
            assert r.status_code == 200, r.text
            ws.worker.run(lambda: s.page.evaluate(
                "document.body.insertAdjacentHTML('beforeend', '<div class=\"g-recaptcha\" data-sitekey=\"lab\"></div>')"))
            r = httpx.post(base + "/api/submit", json={"phrase": "ENVIAR"}, timeout=60)
            assert r.status_code == 403 and "envíe desde el navegador" in r.text, r.text
            assert portal.portal.estadisticas()["peticiones_post"] == 0
            ws.worker.run(lambda: s.page.evaluate("setTimeout(() => document.querySelector('form').requestSubmit(), 100)"))
            time.sleep(2.5)
            assert portal.portal.estadisticas()["recibidos"] == 1
            ops = ws.registro.lista()
            assert len(ops) == 1 and ops[0]["estado"] == INCIERTA and ops[0]["envio_observado"] is True, ops
            with pytest.raises(OperacionBloqueada):
                ws.registro.comprobar(ws.registro.huella(fila), ws.destino_rellenado)
            ws.close()
    finally:
        portal.detener()


# ------------------------------------------------------------------ A6 · la espera del candado tiene tope
def test_a6_si_otro_proceso_retiene_el_candado_no_se_cuelga_y_no_se_envia(tmp_path, monkeypatch):
    import cauce_form_connector.operaciones as O
    reg = RegistroOperaciones(tmp_path)
    codigo = textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        from cauce_form_connector.operaciones import _candado_exclusivo
        fh = None
        while fh is None:
            fh = _candado_exclusivo(Path({str(tmp_path / 'operaciones.lock')!r}))
        print("retenido", flush=True)
        time.sleep(30)
    """)
    p = subprocess.Popen([sys.executable, "-c", codigo], cwd=str(ROOT), stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "retenido"
        monkeypatch.setattr(O, "ESPERA_CANDADO_S", 1.0)
        t0 = time.monotonic()
        with pytest.raises(OSError) as e:
            reg.iniciar("h_" + "6" * 24, "lab", None, None)
        assert not isinstance(e.value, OperacionBloqueada)
        assert "operaciones.lock" in str(e.value) and time.monotonic() - t0 < 10
    finally:
        p.kill()
        p.wait(10)
    op = reg.iniciar("h_" + "6" * 24, "lab", None, None)                # suelto el candado, funciona de nuevo
    assert op["estado"] == "en_curso"


# ------------------------------------------------------------------ A7 · la jornada termina procesos también en Windows
def test_a7_la_jornada_termina_el_arbol_de_procesos_en_esta_plataforma(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import jornada_laboratorio as J
    finally:
        sys.path.pop(0)
    import socket
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    hijo = f"import http.server as h; h.ThreadingHTTPServer(('127.0.0.1', {port}), h.BaseHTTPRequestHandler).serve_forever()"
    pr = J.Proc("prueba", [sys.executable, "-c", hijo], tmp_path, None, tmp_path / "p.log", port)
    pr.iniciar()
    pr.parar()
    assert pr.p.poll() is not None
    pr.iniciar()
    pr.matar()
    assert pr.p.poll() is not None
