"""Identidad COMÚN del expediente entre la interfaz (ENVIAR / envío de la persona) y ``preparar`` (Monitor).

Antes cada modalidad usaba su propia huella (fila CSV completa frente a nombres lógicos del perfil), así que un
expediente enviado por una vía no quedaba protegido en la otra. Ahora las dos anotan también ``destino``: formulario
+ un HMAC por cada campo escrito (clave del campo en la página → valor). Portal propio de laboratorio
(127.0.0.1) y datos ficticios. Lo que NO prueba: Windows físico ni portales reales.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from cauce_form_connector import respaldo as R
from cauce_form_connector.operaciones import (CONFIRMADA, INCIERTA, MAX_CERRADAS, NO_ENVIADA, OperacionBloqueada,
                                              RegistroOperaciones, destinos_casan)
from lab_portal.servidor import ServidorPortal

URL = "http://127.0.0.1:8899/tramite"
ESCRITO = {"tramite::expediente": "EXP-0701", "tramite::nombre": "Persona Ficticia 701",
           "tramite::correo": "ficticia701@example.test", "tramite::telefono": "55 1000 0701", "tramite::tipo": "alta"}


def _confirmada(reg, huella, destino):
    op = reg.iniciar(huella, "lab", None, None, destino=destino)
    reg.cerrar(op["id"], CONFIRMADA, "acuse_verificado", folio="LAB-000701")
    return op


# ------------------------------------------------------------------ reglas de la identidad (sin navegador)
def test_destino_no_guarda_valores_ni_nombres_y_es_estable(tmp_path):
    reg = RegistroOperaciones(tmp_path)
    d = reg.destino(URL, ESCRITO)
    assert d == reg.destino(URL + "?x=1#y", dict(reversed(list(ESCRITO.items()))))   # sin consulta ni orden
    assert d == reg.destino("http://127.0.0.1:8899/tramite", ESCRITO)
    assert reg.destino("http://127.0.0.1:80/tramite", ESCRITO) == reg.destino("http://127.0.0.1/tramite", ESCRITO)
    for v in [*ESCRITO.values(), *ESCRITO.keys(), "tramite", "8899"]:
        assert v.lower() not in d.lower()
    assert reg.destino(URL, {"tramite::nombre": ""}) is None                   # nada escrito → sin identidad
    assert RegistroOperaciones(tmp_path).destino(URL, ESCRITO) == d             # misma clave tras reiniciar


def test_casan_si_uno_contiene_al_otro_y_no_si_cambia_un_valor_o_el_formulario(tmp_path):
    reg = RegistroOperaciones(tmp_path)
    d = reg.destino(URL, ESCRITO)
    menos = reg.destino(URL, {k: v for k, v in ESCRITO.items() if k != "tramite::telefono"})
    assert destinos_casan(d, menos) and destinos_casan(menos, d)              # una modalidad escribió campos de más
    assert not destinos_casan(d, reg.destino(URL, {**ESCRITO, "tramite::expediente": "EXP-0702"}))
    assert not destinos_casan(d, reg.destino("http://127.0.0.1:8899/otro", ESCRITO))
    assert not destinos_casan(d, None) and not destinos_casan(None, None)
    otra_clave = RegistroOperaciones(tmp_path / "otra").destino(URL, ESCRITO)
    assert not destinos_casan(d, otra_clave)                                   # la identidad depende de huella.key


def test_bloquea_por_destino_aunque_la_huella_de_la_modalidad_sea_otra(tmp_path):
    reg = RegistroOperaciones(tmp_path)
    d = reg.destino(URL, ESCRITO)
    _confirmada(reg, "h_" + "1" * 24, d)                                       # huella de la interfaz
    with pytest.raises(OperacionBloqueada) as e:
        reg.comprobar("h_" + "2" * 24, d)                                      # huella de preparar, misma identidad
    assert e.value.codigo == "expediente_confirmada"
    reg.comprobar("h_" + "2" * 24, reg.destino(URL, {**ESCRITO, "tramite::expediente": "EXP-0702"}))  # otro: pasa
    reg.registrar_envio_humano("h_" + "3" * 24, "lab", destino=reg.destino(URL, {"tramite::expediente": "EXP-0703"}))
    with pytest.raises(OperacionBloqueada) as e:
        reg.comprobar("h_" + "4" * 24, reg.destino(URL, {**ESCRITO, "tramite::expediente": "EXP-0703"}))
    assert e.value.codigo == "expediente_incierta"


def test_la_identidad_sobrevive_al_archivo_de_las_cerradas_y_al_reinicio(tmp_path):
    reg = RegistroOperaciones(tmp_path)
    d = reg.destino(URL, ESCRITO)
    _confirmada(reg, "h_" + "a" * 24, d)
    for i in range(MAX_CERRADAS + 1):                                          # 501 cerradas posteriores
        op = reg.iniciar("h_" + f"{i:024x}", "lab", None, None)
        reg.cerrar(op["id"], NO_ENVIADA, "prueba")
    assert all(o.get("destino") != d for o in reg.lista(10_000))               # ya salió de la lista…
    assert d in reg.archivadas                                                  # …pero quedó archivada
    for r in (reg, RegistroOperaciones(tmp_path)):                              # y tras reiniciar
        with pytest.raises(OperacionBloqueada) as e:
            r.comprobar("h_" + "b" * 24, d)
        assert e.value.codigo == "expediente_confirmada"


@pytest.fixture()
def estado(tmp_path, monkeypatch):
    from cauce_form_connector.settings import Settings
    st = Settings()
    st.data_dir = str(tmp_path / "forms")
    st.profiles_dir = str(tmp_path / "forms" / "perfiles")
    monkeypatch.setenv("CAUCE_FORMS_DATA_DIR", st.data_dir)
    return st


def test_la_identidad_sobrevive_a_respaldar_y_restaurar(estado, tmp_path):
    reg = RegistroOperaciones(estado.data_dir)
    d = reg.destino(URL, ESCRITO)
    _confirmada(reg, "h_" + "c" * 24, d)
    for i in range(MAX_CERRADAS + 1):
        op = reg.iniciar("h_" + f"{i:024x}", "lab", None, None)
        reg.cerrar(op["id"], NO_ENVIADA, "prueba")
    reg.registrar_envio_humano("h_" + "d" * 24, "lab", destino=reg.destino(URL, {"tramite::expediente": "EXP-0799"}))
    zipf = R.respaldar(estado, tmp_path / "r")["respaldo"]
    del reg
    import shutil
    shutil.rmtree(estado.data_dir)                                              # se pierde la carpeta entera
    R.restaurar(estado, zipf)
    r2 = RegistroOperaciones(estado.data_dir)
    with pytest.raises(OperacionBloqueada) as e:                               # primero, la marca GENERAL
        r2.comprobar("h_" + "e" * 24, d)
    assert e.value.codigo == "revision_general_pendiente"
    r2.revisar("GENERAL", "restauración comprobada en el laboratorio", "GENERAL")
    with pytest.raises(OperacionBloqueada) as e:                               # archivada, con otra huella
        r2.comprobar("h_" + "e" * 24, d)
    assert e.value.codigo == "expediente_confirmada"
    with pytest.raises(OperacionBloqueada) as e:                               # incierta, por la otra modalidad
        r2.comprobar("h_" + "f" * 24, r2.destino(URL, {"tramite::expediente": "EXP-0799", "tramite::tipo": "alta"}))
    assert e.value.codigo == "expediente_incierta"


# ------------------------------------------------------------------ de extremo a extremo con Chromium y el portal propio
@pytest.fixture()
def portal():
    s = ServidorPortal().iniciar()
    yield s
    s.detener()


def _fila(n):
    return {"expediente": f"EXP-{n:04d}", "nombre": f"Persona Ficticia {n}", "correo": f"ficticia{n}@example.test",
            "telefono": f"55 1000 {n:04d}", "tipo": "alta"}


def _perfil_preparar(st, url):
    """Perfil aprobado para ``preparar`` sobre el MISMO formulario del portal, con nombres lógicos distintos de las
    columnas del CSV (como en la realidad: Monitor y la hoja de la persona no tienen por qué llamarlos igual)."""
    from integ_helpers import aprobar
    campos = {"num_exp": "tramite::expediente", "titular": "tramite::nombre", "mail": "tramite::correo",
              "tel": "tramite::telefono", "clase": "tramite::tipo"}
    return aprobar(st, url, campos=campos)


def _valores(n, sin=()):
    f = _fila(n)
    v = {"num_exp": f["expediente"], "titular": f["nombre"], "mail": f["correo"], "tel": f["telefono"],
         "clase": f["tipo"]}
    return {k: x for k, x in v.items() if k not in sin}


@pytest.fixture()
def puesto(portal, tmp_path, monkeypatch):
    """Interfaz web real y `preparar` compartiendo el MISMO registro de operaciones del puesto."""
    import cauce_form_connector.integracion as I
    from integ_helpers import settings_para
    from servidor_app import servir
    from cauce_form_connector.url_policy import UrlPolicy
    from cauce_form_connector.web import app as web
    monkeypatch.setattr(I, "GRACIA_ACTIVACION_S", 0.5)
    st = settings_para(tmp_path)
    monkeypatch.setenv("CAUCE_FORMS_DATA_DIR", st.data_dir)
    monkeypatch.setenv("CAUCE_NE_SESSION_TOKEN_FILE", "")
    info = _perfil_preparar(st, portal.url + "/tramite")
    app = web.create_app()
    ws = app.state.ws
    with servir(app) as base:
        s = ws.browser()
        pol = UrlPolicy(allow_file=False)

        def rellenar_ui(n):
            import httpx
            ws.rows = [_fila(n)]
            ws.worker.run(lambda: s.open_url(pol.check(portal.url + "/tramite", False), pol.guard(False)))
            campos = ws.worker.run(lambda: s.inspect().all_fields)
            r = httpx.post(base + "/api/fill", json={"mapping": {f.key: f.name for f in campos if f.name in ws.rows[0]},
                                                     "row_index": 0, "reload_first": False, "delay_ms": 0}, timeout=60)
            assert r.status_code == 200, r.text
            return base
        yield ws, s, base, st, info, rellenar_ui
        ws.close()


def _preparar(st, info, valores, tmp_path, **kw):
    import cauce_form_connector.integracion as I
    from integ_helpers import solicitud
    return I.ejecutar(json.dumps(solicitud(info, valores=valores, espera=kw.pop("espera", 0))), settings=st,
                      state_dir=tmp_path / "estado-monitor", **kw)


@pytest.mark.browser
def test_enviado_con_enviar_en_la_interfaz_preparar_no_lo_vuelve_a_preparar(puesto, portal, tmp_path):
    import httpx
    ws, s, base, st, info, rellenar_ui = puesto
    rellenar_ui(701)
    r = httpx.post(base + "/api/submit", json={"phrase": "ENVIAR"}, timeout=60)
    assert r.status_code == 200 and r.json()["operacion"]["estado"] == CONFIRMADA, r.text
    # Monitor pide el MISMO expediente con sus propios nombres lógicos: ni se abre el navegador
    resp, _ = _preparar(st, info, _valores(701), tmp_path, fabrica_sesion=lambda *_: pytest.fail("abrió el navegador"))
    assert resp["estado"] == "detenido_intervencion_humana", resp
    # y aunque Monitor pida solo una parte de los campos (contenida en lo enviado), tampoco
    resp, _ = _preparar(st, info, _valores(701, sin=("tel", "mail")), tmp_path,
                        fabrica_sesion=lambda *_: pytest.fail("abrió el navegador"))
    assert resp["estado"] == "detenido_intervencion_humana", resp
    # otro expediente ficticio sí se prepara
    from integ_helpers import fabrica_espia
    resp, _ = _preparar(st, info, _valores(702), tmp_path, fabrica_sesion=fabrica_espia)
    assert resp["estado"] == "revision_caducada", resp
    assert portal.portal.estadisticas()["recibidos"] == 1


@pytest.mark.browser
def test_enviado_por_la_persona_en_preparar_la_interfaz_no_lo_vuelve_a_enviar(puesto, portal, tmp_path):
    import httpx
    from integ_helpers import fabrica_espia
    ws, s, base, st, info, rellenar_ui = puesto

    def la_persona_envia(sp):
        sp.page.wait_for_timeout(600)
        sp.page.evaluate("document.querySelector('form').requestSubmit()")
        sp.page.wait_for_timeout(1500)
        sp.page.close()
    resp, _ = _preparar(st, info, _valores(703), tmp_path, espera=20, fabrica_sesion=fabrica_espia,
                        gancho_revision=la_persona_envia)
    assert resp["envio"]["envio_humano_observado"] is True, resp
    assert portal.portal.estadisticas()["recibidos"] == 1
    inc = [o for o in RegistroOperaciones(st.data_dir).lista() if o["estado"] == INCIERTA]
    assert len(inc) == 1 and inc[0]["destino"]
    # la persona, en la interfaz y con su CSV, intenta enviar el mismo expediente: 409 y no sale nada
    rellenar_ui(703)
    r = httpx.post(base + "/api/submit", json={"phrase": "ENVIAR"}, timeout=60)
    assert r.status_code == 409 and "desconocido" in r.text, r.text
    time.sleep(0.5)
    assert portal.portal.estadisticas()["recibidos"] == 1
    # tras reiniciar el puesto (otro proceso abre el registro), sigue bloqueado por cualquiera de las dos vías
    r2 = RegistroOperaciones(st.data_dir)
    with pytest.raises(OperacionBloqueada):
        r2.comprobar(r2.huella(_fila(703)), ws.destino_rellenado)
    # otro expediente en la interfaz sí sale
    rellenar_ui(704)
    r = httpx.post(base + "/api/submit", json={"phrase": "ENVIAR"}, timeout=60)
    assert r.status_code == 200, r.text
    assert portal.portal.estadisticas()["recibidos"] == 2


@pytest.mark.browser
def test_envio_directo_de_la_persona_en_la_interfaz_bloquea_preparar(puesto, portal, tmp_path):
    ws, s, base, st, info, rellenar_ui = puesto
    rellenar_ui(705)
    ws.worker.run(lambda: s.page.evaluate("setTimeout(() => document.querySelector('form').requestSubmit(), 100)"))
    time.sleep(2.5)
    assert portal.portal.estadisticas()["recibidos"] == 1
    resp, _ = _preparar(st, info, _valores(705), tmp_path, fabrica_sesion=lambda *_: pytest.fail("abrió el navegador"))
    assert resp["estado"] == "detenido_intervencion_humana", resp


@pytest.mark.browser
def test_el_registro_no_contiene_datos_personales(puesto, portal, tmp_path):
    import httpx
    ws, s, base, st, info, rellenar_ui = puesto
    rellenar_ui(706)
    assert httpx.post(base + "/api/submit", json={"phrase": "ENVIAR"}, timeout=60).status_code == 200
    _preparar(st, info, _valores(707), tmp_path, fabrica_sesion=__import__("integ_helpers").fabrica_espia)
    texto = (Path(st.data_dir) / "operaciones.json").read_text("utf-8").lower()
    for n in (706, 707):
        for v in _fila(n).values():
            if v != "alta":
                assert v.lower() not in texto, v
    for nombre in ("expediente", "titular", "num_exp", "correo", "tramite::"):
        assert nombre not in texto, nombre
