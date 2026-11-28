"""Bloque C/F · envío protegido con Chromium REAL contra el portal FICTICIO propio (127.0.0.1, datos ficticios).

Cada prueba comprueba desde FUERA de Forms (estadísticas del portal) que no hubo duplicados ni reenvíos.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from cauce_form_connector.engine import fill_form
from cauce_form_connector.envio import enviar_protegido, verificar_operacion
from cauce_form_connector.operaciones import (CONFIRMADA, INCIERTA, NO_ENVIADA, RECHAZADA, OperacionBloqueada,
                                              RegistroOperaciones)
from cauce_form_connector.page_inspector import BrowserSession
from cauce_form_connector.url_policy import UrlPolicy
from lab_portal.servidor import ServidorPortal

pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[1]


def fila(n: int) -> dict:
    return {"expediente": f"EXP-{n:04d}", "nombre": f"Persona Ficticia {n}", "correo": f"ficticia{n}@example.test",
            "telefono": f"55 1000 {n:04d}", "tipo": "alta"}


@pytest.fixture()
def portal():
    s = ServidorPortal().iniciar()
    yield s
    s.detener()


@pytest.fixture(scope="module")
def nav():
    s = BrowserSession()
    s.start()
    yield s
    s.close()


def rellenar(s: BrowserSession, url: str, row: dict) -> None:
    pol = UrlPolicy(allow_file=False)
    s.open_url(pol.check(url, False), pol.guard(False))
    campos = s.inspect().all_fields
    rep = fill_form(s, {f.key: f.name for f in campos if f.name in row}, row)
    assert rep.ok, [(r.field_key, r.status, r.detail) for r in rep.results]


def enviar(s, reg, row, **kw):
    return enviar_protegido(s, "ENVIAR", reg, reg.huella(row), **kw)


def test_normal_confirma_solo_con_acuse_verificado(nav, portal, tmp_path):
    reg = RegistroOperaciones(tmp_path)
    rellenar(nav, portal.url + "/tramite", fila(1))
    out = enviar(nav, reg, fila(1))
    assert out["confirmado_recepcion"] is True and out["operacion"]["estado"] == CONFIRMADA
    assert out["operacion"]["folio"].startswith("LAB-")
    st = portal.portal.estadisticas()
    assert st["recibidos"] == 1 and st["duplicados"] == 0 and st["peticiones_post"] == 1


def test_aprobacion_de_un_solo_uso_y_expediente_confirmado_no_se_reenvia(nav, portal, tmp_path):
    reg = RegistroOperaciones(tmp_path)
    rellenar(nav, portal.url + "/tramite", fila(2))
    enviar(nav, reg, fila(2))
    with pytest.raises(PermissionError):
        enviar(nav, reg, fila(2))                     # sin volver a rellenar: aprobación consumida
    rellenar(nav, portal.url + "/tramite", fila(2))
    with pytest.raises(OperacionBloqueada):
        enviar(nav, reg, fila(2))                     # aunque se rellene otra vez: ya consta como recibido
    assert portal.portal.estadisticas()["peticiones_post"] == 1


def test_chromium_reintenta_por_su_cuenta_un_post_cortado_y_el_token_evita_el_duplicado(nav, portal, tmp_path):
    """HALLAZGO: si la conexión (reutilizada) se corta antes de la respuesta, Chromium REPITE el POST por su cuenta.
    Forms no puede impedirlo: lo que evita un trámite duplicado es el token de un solo uso del portal propio."""
    reg = RegistroOperaciones(tmp_path)
    portal.portal.fallos.append({"tipo": "cortar_antes"})
    rellenar(nav, portal.url + "/tramite", fila(13))
    out = enviar(nav, reg, fila(13), acuse_espera_s=3)
    st = portal.portal.estadisticas()
    assert st["recibidos"] <= 1 and st["duplicados"] == 0
    if st["peticiones_post"] == 2:                         # el navegador reintentó: el portal lo recibió una vez
        assert out["operacion"]["estado"] == CONFIRMADA and st["recibidos"] == 1
    else:
        assert out["operacion"]["estado"] == INCIERTA and st["recibidos"] == 0


def test_corte_antes_de_registrar_queda_incierta_y_no_se_puede_repetir(nav, portal, tmp_path):
    reg = RegistroOperaciones(tmp_path)
    for _ in range(3):                                     # también se corta el reintento del propio navegador
        portal.portal.fallos.append({"tipo": "cortar_antes"})
    rellenar(nav, portal.url + "/tramite", fila(3))
    out = enviar(nav, reg, fila(3), acuse_espera_s=3)
    op = out["operacion"]
    assert op["estado"] == INCIERTA and out["confirmado_recepcion"] is False
    assert op["envio_observado"] is True                  # se vio el envío… y aun así NO cuenta como recepción
    v = verificar_operacion(reg, op["id"])                # el portal dice «no consta»: sigue incierta
    assert v["cambio"] is False and v["operacion"]["estado"] == INCIERTA
    rellenar(nav, portal.url + "/tramite", fila(3))
    with pytest.raises(OperacionBloqueada) as e:
        enviar(nav, reg, fila(3))
    assert e.value.codigo == "expediente_incierta"
    assert RegistroOperaciones(tmp_path).get(op["id"])["estado"] == INCIERTA      # un reinicio no la quita
    st = portal.portal.estadisticas()
    assert st["recibidos"] == 0 and 1 <= st["peticiones_post"] <= 3       # Forms pulsó una vez (ver hallazgo)


def test_corte_despues_de_registrar_se_confirma_por_la_api_del_portal_sin_duplicar(nav, portal, tmp_path):
    """El portal registra y corta la conexión (también los reintentos del navegador): la página muestra un error de red,
    pero la API de acuse del portal PROPIO confirma la recepción. Sin esa API, quedaría incierta."""
    reg = RegistroOperaciones(tmp_path)
    for _ in range(3):
        portal.portal.fallos.append({"tipo": "cortar_despues"})
    rellenar(nav, portal.url + "/tramite", fila(4))
    out = enviar(nav, reg, fila(4), acuse_espera_s=2)
    op = out["operacion"]
    assert op["estado"] == CONFIRMADA and op["folio"].startswith("LAB-00001")
    st = portal.portal.estadisticas()
    assert st["recibidos"] == 1 and st["duplicados"] == 0
    assert st["reintentos_mismo_token"] == st["peticiones_post"] - 1   # reintentos del navegador, no de Forms


def test_corte_despues_con_api_de_acuse_caida_queda_incierta_y_se_confirma_despues(nav, portal, tmp_path):
    reg = RegistroOperaciones(tmp_path)
    for _ in range(3):
        portal.portal.fallos.append({"tipo": "cortar_despues"})
    portal.portal.acuse_retardo_s = 7                      # la verificación agota su plazo (5 s)
    rellenar(nav, portal.url + "/tramite", fila(14))
    out = enviar(nav, reg, fila(14), acuse_espera_s=1)
    assert out["operacion"]["estado"] == INCIERTA
    assert out["operacion"]["motivo"].startswith("verificacion_fallida")
    portal.portal.acuse_retardo_s = 0
    v = verificar_operacion(reg, out["operacion"]["id"])
    assert v["operacion"]["estado"] == CONFIRMADA
    assert portal.portal.estadisticas()["recibidos"] == 1


def test_error_500_del_portal_es_rechazo_explicito_sin_reintento(nav, portal, tmp_path):
    reg = RegistroOperaciones(tmp_path)
    portal.portal.fallos.append({"tipo": "error_500"})
    rellenar(nav, portal.url + "/tramite", fila(5))
    out = enviar(nav, reg, fila(5), acuse_espera_s=3)
    assert out["operacion"]["estado"] == RECHAZADA
    assert portal.portal.estadisticas()["peticiones_post"] == 1      # nada se reintentó solo


def test_limite_de_capacidad_429_se_respeta(nav, portal, tmp_path):
    reg = RegistroOperaciones(tmp_path)
    portal.portal.capacidad = 1
    rellenar(nav, portal.url + "/tramite", fila(6))
    assert enviar(nav, reg, fila(6))["operacion"]["estado"] == CONFIRMADA
    rellenar(nav, portal.url + "/tramite", fila(7))
    out = enviar(nav, reg, fila(7), acuse_espera_s=3)
    assert out["operacion"]["estado"] == RECHAZADA and out["operacion"]["motivo"] == "portal_rechazo_capacidad"
    st = portal.portal.estadisticas()
    assert st["recibidos"] == 1 and st["rechazos"] == {"capacidad": 1} and st["peticiones_post"] == 2


def test_portal_lento_queda_incierta_y_luego_se_confirma(nav, portal, tmp_path):
    reg = RegistroOperaciones(tmp_path)
    portal.portal.fallos.append({"tipo": "lento", "segundos": 14})
    rellenar(nav, portal.url + "/tramite", fila(8))
    t0 = time.monotonic()
    out = enviar(nav, reg, fila(8), acuse_espera_s=1)
    assert out["operacion"]["estado"] == INCIERTA and out["operacion"]["motivo"] == "portal_sin_constancia"
    assert time.monotonic() - t0 < 12
    time.sleep(14 - (time.monotonic() - t0) + 1)
    v = verificar_operacion(reg, out["operacion"]["id"])
    assert v["operacion"]["estado"] == CONFIRMADA
    assert portal.portal.estadisticas()["duplicados"] == 0


def test_navegador_cerrado_durante_el_envio_queda_incierta(portal, tmp_path):
    from cauce_form_connector.page_inspector import BrowserWorker
    reg = RegistroOperaciones(tmp_path)
    w = BrowserWorker()                                    # otro hilo: la API síncrona admite un Playwright por hilo

    def caso():
        s = BrowserSession()
        s.start()
        try:
            portal.portal.fallos.append({"tipo": "lento", "segundos": 4})
            rellenar(s, portal.url + "/tramite", fila(9))
            pagina = s.page
            espera = pagina.wait_for_timeout

            def esperar_y_cerrar(ms):
                pagina.wait_for_timeout = espera
                pagina.close()                             # «la persona cerró la ventana» justo tras pulsar
                return None
            pagina.wait_for_timeout = esperar_y_cerrar
            out = enviar(s, reg, fila(9), acuse_espera_s=2)
            return out
        finally:
            s.close()
    out = w.run(caso)
    w.shutdown()
    assert out["operacion"]["estado"] == INCIERTA, out["operacion"]
    assert out["operacion"]["motivo"] in ("navegador_cerrado_durante_envio",) or \
        out["operacion"]["motivo"].startswith("interrumpida_")
    assert out["confirmado_recepcion"] is False
    assert portal.portal.estadisticas()["duplicados"] == 0


def test_validacion_del_navegador_bloquea_el_envio_no_enviada(nav, portal, tmp_path):
    reg = RegistroOperaciones(tmp_path)
    row = dict(fila(10), expediente="MAL")               # el patrón EXP-#### lo rechaza el propio navegador
    pol = UrlPolicy(allow_file=False)
    nav.open_url(pol.check(portal.url + "/tramite", False), pol.guard(False))
    campos = nav.inspect().all_fields
    fill_form(nav, {f.key: f.name for f in campos if f.name in row}, row)
    out = enviar(nav, reg, row, acuse_espera_s=2)
    assert out["operacion"]["estado"] == NO_ENVIADA
    assert portal.portal.estadisticas()["peticiones_post"] == 0


def test_portal_ajeno_sin_acuse_nunca_se_da_por_recibido(nav, lab_url, tmp_path, rows):
    """Formulario de laboratorio clásico (sin API de acuse): un envío observado NO es una recepción."""
    from cauce_form_connector.matcher import match_fields
    reg = RegistroOperaciones(tmp_path)
    pol = UrlPolicy(allow_file=False)
    nav.open_url(pol.check(lab_url("01_tradicional.html"), False), pol.guard(False))
    campos = nav.inspect().all_fields
    m = {p.field_key: p.column for p in match_fields(campos, list(rows[0])) if p.column and p.status == "auto"}
    fill_form(nav, m, rows[0])
    out = enviar(nav, reg, rows[0], acuse_espera_s=1)
    assert out["envio_observado"] is True and out["confirmado_recepcion"] is False
    assert out["operacion"]["estado"] == INCIERTA
    assert out["operacion"]["motivo"] == "envio_observado_sin_acuse_verificable"
    assert out["operacion"]["verificable"] is False


def test_doble_clic_en_la_interfaz_envia_una_sola_vez(portal, tmp_path, monkeypatch):
    """Dos peticiones /api/submit a la vez (doble clic): una envía, la otra se rechaza. El portal recibe UNA."""
    import httpx
    from servidor_app import servir
    from cauce_form_connector.web import app as web
    monkeypatch.setenv("CAUCE_FORMS_DATA_DIR", str(tmp_path / "forms"))
    monkeypatch.setenv("CAUCE_NE_SESSION_TOKEN_FILE", "")
    portal.portal.fallos.append({"tipo": "lento", "segundos": 2})
    app = web.create_app()
    ws = app.state.ws
    with servir(app) as base:
        s = ws.browser()
        ws.rows = [fila(11)]
        ws.worker.run(lambda: rellenar(s, portal.url + "/tramite", fila(11)))
        ws.fila_rellenada = 0
        res = []

        def pulsar():
            res.append(httpx.post(base + "/api/submit", json={"phrase": "ENVIAR"}, timeout=60))
        ts = [threading.Thread(target=pulsar) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        codigos = sorted(r.status_code for r in res)
        assert codigos[0] == 200 and codigos[1] in (403, 409), [r.json() for r in res]
        ok = next(r for r in res if r.status_code == 200).json()
        assert ok["operacion"]["estado"] == CONFIRMADA
        st = portal.portal.estadisticas()
        assert st["peticiones_post"] == 1 and st["recibidos"] == 1
        d = httpx.get(base + "/api/diagnostico").json()
        assert d["motor"] == "no_configurado" and d["conectividad"] == "sin_diagnostico"
        ws.close()


HIJO = r'''
import json, sys, time
sys.path.insert(0, sys.argv[3])
from cauce_form_connector.engine import fill_form
from cauce_form_connector.envio import enviar_protegido
from cauce_form_connector.operaciones import RegistroOperaciones
from cauce_form_connector.page_inspector import BrowserSession
from cauce_form_connector.url_policy import UrlPolicy
row = {"expediente": "EXP-0012", "nombre": "Persona Ficticia 12", "correo": "f12@example.test",
       "telefono": "55 1000 0012", "tipo": "alta"}
s = BrowserSession(); s.start()
pol = UrlPolicy(allow_file=False)
s.open_url(pol.check(sys.argv[1], False), pol.guard(False))
fill_form(s, {f.key: f.name for f in s.inspect().all_fields if f.name in row}, row)
reg = RegistroOperaciones(sys.argv[2])
print("LISTO", reg.huella(row), flush=True)
enviar_protegido(s, "ENVIAR", reg, reg.huella(row), acuse_espera_s=60)
print("NO_DEBERIA_LLEGAR", flush=True)
'''


def test_forms_muere_durante_el_envio_y_al_volver_queda_incierta(portal, tmp_path):
    """Forms (proceso real) recibe SIGKILL con el envío en curso. El proceso nuevo ve «incierta» y bloquea repetir."""
    portal.portal.fallos.append({"tipo": "lento", "segundos": 5})
    datos = tmp_path / "forms"
    p = subprocess.Popen([sys.executable, "-c", HIJO, portal.url + "/tramite", str(datos), str(ROOT)],
                         stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    linea = p.stdout.readline().split()
    assert linea[0] == "LISTO", linea
    huella = linea[1]
    t0 = time.time()
    while time.time() - t0 < 20 and portal.portal.estadisticas()["peticiones_post"] == 0:
        time.sleep(0.1)
    p.kill()                                              # el envío está en el aire
    p.wait(10)
    reg = RegistroOperaciones(datos)                      # «Forms vuelve a arrancar»
    ops = reg.lista()
    assert len(ops) == 1 and ops[0]["estado"] == INCIERTA
    assert ops[0]["motivo"] == "forms_reiniciado_durante_operacion"
    with pytest.raises(OperacionBloqueada):
        reg.comprobar(huella)
    time.sleep(5.5)                                       # el portal termina de registrarlo
    v = verificar_operacion(reg, ops[0]["id"])
    assert v["operacion"]["estado"] == CONFIRMADA
    st = portal.portal.estadisticas()
    assert st["recibidos"] == 1 and st["peticiones_post"] == 1 and st["duplicados"] == 0
