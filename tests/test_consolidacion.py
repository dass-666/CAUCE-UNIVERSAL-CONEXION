"""Consolidación final (bloques B, C y D): envíos de la persona fuera de ENVIAR, revisiones, varias instancias,
reinicios durante escrituras y respaldo/restauración. Solo portal PROPIO de laboratorio y datos ficticios."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from cauce_form_connector import respaldo as R
from cauce_form_connector.operaciones import (CONFIRMADA, INCIERTA, REVISADA, OperacionBloqueada,
                                              RegistroOperaciones)
from lab_portal.servidor import ServidorPortal

ROOT = Path(__file__).resolve().parents[1]
H1, H2 = "h_" + "1" * 24, "h_" + "2" * 24


def fila(n: int) -> dict:
    return {"expediente": f"EXP-{n:04d}", "nombre": f"Persona Ficticia {n}", "correo": f"ficticia{n}@example.test",
            "telefono": f"55 1000 {n:04d}", "tipo": "alta"}


# ================================================================== C · revisiones que NO desbloquean en silencio
def test_revisar_sin_resultado_quita_la_alerta_pero_sigue_bloqueando(tmp_path):
    reg = RegistroOperaciones(tmp_path)
    op = reg.registrar_envio_humano(H1, "lab")
    reg.revisar(op["id"], "revisado en el portal", op["id"])              # sin resultado = sin_determinar
    assert reg.get(op["id"])["estado"] == REVISADA and not reg.pide_revision()
    with pytest.raises(OperacionBloqueada) as e:
        reg.comprobar(H1)
    assert e.value.codigo == "expediente_revisada"
    # se puede volver a revisar para dejar el resultado; solo «no_recibido» desbloquea
    reg.revisar(op["id"], "el portal no lo tiene", op["id"], resultado="no_recibido")
    reg.comprobar(H1)


def test_revisar_como_recibido_bloquea_para_siempre(tmp_path):
    reg = RegistroOperaciones(tmp_path)
    op = reg.registrar_envio_humano(H1, "lab")
    reg.revisar(op["id"], "folio visto en el portal", op["id"], resultado="recibido")
    with pytest.raises(OperacionBloqueada):
        reg.comprobar(H1)
    with pytest.raises(ValueError):                                          # ya tiene resultado: no se cambia
        reg.revisar(op["id"], "cambio de opinión", op["id"], resultado="no_recibido")


def test_resolver_la_marca_en_network_engine_no_desbloquea_el_expediente(tmp_path):
    reg = RegistroOperaciones(tmp_path)
    op = reg.registrar_envio_humano(H1, "lab")
    reg.marca_vista_en("bs_" + "a" * 12)
    assert reg.resuelta_por_operador_ne("bs_" + "a" * 12) == [op["id"]]
    assert not reg.pide_revision()
    with pytest.raises(OperacionBloqueada):
        reg.comprobar(H1)


def test_revision_antigua_sin_resultado_sigue_bloqueando(tmp_path):
    """Registros de 0.2.0rc1: una «revisada» sin campo resultado se trata como sin_determinar (conservador)."""
    reg = RegistroOperaciones(tmp_path)
    op = reg.registrar_envio_humano(H1, "lab")
    reg.revisar(op["id"], "revision antigua", op["id"])
    for o in reg.ops:
        o["revision"].pop("resultado")
    reg._guardar()
    with pytest.raises(OperacionBloqueada):
        RegistroOperaciones(tmp_path).comprobar(H1)


def test_revisada_que_bloquea_tambien_se_archiva(tmp_path, monkeypatch):
    import cauce_form_connector.operaciones as O
    monkeypatch.setattr(O, "MAX_CERRADAS", 1)
    reg = RegistroOperaciones(tmp_path)
    a = reg.registrar_envio_humano(H1, "lab")
    reg.revisar(a["id"], "llegó al portal", a["id"], resultado="recibido")
    b = reg.registrar_envio_humano(H2, "lab")
    reg.revisar(b["id"], "llegó al portal", b["id"], resultado="recibido")      # a sale de la lista
    assert reg.get(a["id"]) is None and H1 in reg.archivadas
    with pytest.raises(OperacionBloqueada):
        RegistroOperaciones(tmp_path).comprobar(H1)


def test_orden_de_operador_exige_resultado(tmp_path, monkeypatch, capsys):
    from cauce_form_connector import cli
    monkeypatch.setenv("CAUCE_FORMS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAUCE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    op = RegistroOperaciones(tmp_path).registrar_envio_humano(H1, "lab")
    assert cli.main(["operaciones", "revisar", op["id"], "--nota", "revisado", "--confirmar", op["id"]]) == 1
    assert cli.main(["operaciones", "revisar", op["id"], "--nota", "revisado", "--confirmar", op["id"],
                     "--resultado", "no_recibido"]) == 0
    RegistroOperaciones(tmp_path).comprobar(H1)


# ================================================================== C · varias instancias
def test_segunda_interfaz_con_el_mismo_registro_no_envia(tmp_path):
    a = RegistroOperaciones(tmp_path, dueno=True)
    b = RegistroOperaciones(tmp_path, dueno=True)
    assert not a.otro_forms_en_marcha and b.otro_forms_en_marcha
    with pytest.raises(OperacionBloqueada) as e:
        b.iniciar(H1, "lab", None, None)
    assert e.value.codigo == "otro_forms_en_marcha"
    a.iniciar(H2, "lab", None, None)
    a.soltar()                                         # la primera se cierra: la segunda ya puede
    assert b.iniciar(H1, "lab", None, None)["estado"] == "en_curso"
    b.soltar()


# ================================================================== C · reinicios durante escrituras
ESCRITOR = r'''
import sys
sys.path.insert(0, sys.argv[2])
from cauce_form_connector.operaciones import RegistroOperaciones, CONFIRMADA
reg = RegistroOperaciones(sys.argv[1])
n = 0
while True:
    h = "h_%024x" % (int(sys.argv[3]) * 100000 + n)
    o = reg.iniciar(h, "lab", None, None)
    reg.cerrar(o["id"], CONFIRMADA, "acuse_verificado", folio="LAB-%06d" % n)
    print(h, flush=True)        # solo se imprime cuando la confirmada ya está en disco (fsync)
    n += 1
'''


@pytest.mark.skipif(sys.platform == "win32", reason="kill -9 con señales POSIX")
def test_matar_forms_mientras_escribe_nunca_pierde_una_confirmada(tmp_path):
    """Se mata el proceso (SIGKILL) en mitad de escrituras, 12 veces. Toda confirmada que llegó a disco sigue
    bloqueando, el registro siempre se puede abrir y nunca queda aceptado un archivo a medias."""
    guardadas: list[str] = []
    for ronda in range(12):
        p = subprocess.Popen([sys.executable, "-c", ESCRITOR, str(tmp_path), str(ROOT), str(ronda)],
                             stdout=subprocess.PIPE, text=True)
        time.sleep(0.15 + 0.05 * (ronda % 4))
        p.send_signal(signal.SIGKILL)
        out, _ = p.communicate()
        guardadas += out.split()
        reg = RegistroOperaciones(tmp_path)
        assert reg.estado_carga in ("ok", "respaldo"), reg.estado_carga
        if reg.general is not None:                    # se recuperó del .bak: lo revisa un operador
            reg.revisar("GENERAL", "prueba de reinicios", "GENERAL")
        for h in guardadas:
            with pytest.raises(OperacionBloqueada):
                reg.comprobar(h)
        assert not list(tmp_path.glob("*.tmp")) or reg.estado_carga in ("ok", "respaldo")
    assert len(guardadas) > 12


# ================================================================== D · respaldo y restauración
@pytest.fixture()
def estado(tmp_path, monkeypatch):
    from cauce_form_connector.settings import Settings
    s = Settings()
    s.data_dir = str(tmp_path / "state")
    s.profiles_dir = str(tmp_path / "profiles")
    s.allowed_hosts_file = str(tmp_path / "config" / "allowed_hosts.json")
    monkeypatch.setenv("CAUCE_STATE_DIR", str(tmp_path / "state"))
    Path(s.profiles_dir).mkdir(parents=True)
    (Path(s.profiles_dir) / "perfil_lab.json").write_text('{"perfil": "lab"}')
    Path(s.allowed_hosts_file).parent.mkdir(parents=True)
    Path(s.allowed_hosts_file).write_text('{"hosts": ["127.0.0.1"]}')
    (tmp_path / "state" / "solicitudes_usadas").mkdir(parents=True)
    (tmp_path / "state" / "solicitudes_usadas" / ("sol_" + "a" * 16)).write_text("2026-11-28T00:00:00+00:00")
    from cauce_form_connector.red.identidad import Puesto
    Puesto(s.data_dir)
    return s


def _confirmada(reg, h, n=1):
    o = reg.iniciar(h, "lab", None, None)
    return reg.cerrar(o["id"], CONFIRMADA, "acuse_verificado", folio=f"LAB-{n:06d}")


def test_respaldo_contiene_lo_necesario_sin_secretos_y_se_verifica(estado, tmp_path):
    reg = RegistroOperaciones(estado.data_dir)
    _confirmada(reg, H1)
    r = R.respaldar(estado, tmp_path / "respaldos")
    with zipfile.ZipFile(r["respaldo"]) as z:
        nombres = set(z.namelist())
    assert {"estado/operaciones.json", "estado/huella.key", "estado/puesto.json", "config/allowed_hosts.json",
            "perfiles/perfil_lab.json", "solicitudes_usadas/sol_" + "a" * 16, R.MANIFIESTO} <= nombres
    assert not any("token" in n or "credencial" in n for n in nombres)
    if sys.platform != "win32":
        assert os.stat(r["respaldo"]).st_mode & 0o077 == 0
    man, _ = R.verificar(r["respaldo"])
    assert man["operaciones"] == 1 and man["clave_id"] == reg.clave_id


@pytest.mark.parametrize("dano", ["archivo", "manifiesto", "sobra", "falta_clave"])
def test_respaldo_alterado_no_se_restaura(estado, tmp_path, dano):
    RegistroOperaciones(estado.data_dir)
    r = R.respaldar(estado, tmp_path / "respaldos")["respaldo"]
    malo = tmp_path / "malo.zip"
    with zipfile.ZipFile(r) as zin, zipfile.ZipFile(malo, "w") as zout:
        for n in zin.namelist():
            b = zin.read(n)
            if dano == "archivo" and n == "estado/operaciones.json":
                b = b.replace(b'"operaciones": []', b'"operaciones": [] ')
            if dano == "manifiesto" and n == R.MANIFIESTO:
                b = b.replace(b'"operaciones": 0', b'"operaciones": 1')
            if dano == "falta_clave" and n == "estado/huella.key":
                continue
            zout.writestr(n, b)
        if dano == "sobra":
            zout.writestr("estado/extra.txt", b"x")
    antes = (Path(estado.data_dir) / "operaciones.json").read_bytes()
    with pytest.raises(R.RespaldoInvalido):
        R.restaurar(estado, malo)
    assert (Path(estado.data_dir) / "operaciones.json").read_bytes() == antes      # nada se tocó


def test_restaurar_un_respaldo_antiguo_no_desbloquea_lo_posterior(estado, tmp_path):
    """El caso que pide la misión: lo incierto o confirmado DESPUÉS del respaldo sigue bloqueando tras restaurar."""
    reg = RegistroOperaciones(estado.data_dir)
    _confirmada(reg, H1, 1)
    r = R.respaldar(estado, tmp_path / "respaldos")["respaldo"]
    inc = reg.registrar_envio_humano(H2, "lab")                       # posterior al respaldo
    en_curso = reg.iniciar("h_" + "3" * 24, "lab", None, None)       # y otra a medias
    out = R.restaurar(estado, r)
    assert out["operaciones_actuales_sumadas"] == 2 and out["aparte"]
    nuevo = RegistroOperaciones(estado.data_dir)
    assert nuevo.general["motivo"] == "restaurado_desde_respaldo"
    assert nuevo.get(inc["id"])["estado"] == INCIERTA
    assert nuevo.get(en_curso["id"])["estado"] == INCIERTA
    nuevo.revisar("GENERAL", "revisado tras restaurar", "GENERAL")
    for h in (H1, H2, "h_" + "3" * 24):
        with pytest.raises(OperacionBloqueada):
            nuevo.comprobar(h)
    assert Path(out["aparte"], "operaciones.json").exists()           # lo anterior se apartó, no se borró


def test_restaurar_tras_perder_la_carpeta_entera(estado, tmp_path):
    import shutil
    reg = RegistroOperaciones(estado.data_dir)
    _confirmada(reg, H1)
    r = R.respaldar(estado, tmp_path / "respaldos")["respaldo"]
    shutil.rmtree(estado.data_dir)
    out = R.restaurar(estado, r)
    nuevo = RegistroOperaciones(estado.data_dir)
    assert nuevo.general is not None and out["solicitudes_usadas_sumadas"] == 1
    nuevo.revisar("GENERAL", "revisado tras restaurar", "GENERAL")
    with pytest.raises(OperacionBloqueada):
        nuevo.comprobar(H1)                                            # la clave volvió: la huella casa


def test_restaurar_con_un_registro_actual_de_otra_clave_lo_dice(estado, tmp_path):
    reg = RegistroOperaciones(estado.data_dir)
    r = R.respaldar(estado, tmp_path / "respaldos")["respaldo"]
    (Path(estado.data_dir) / "huella.key").unlink()                    # se perdió la clave y se siguió trabajando
    reg2 = RegistroOperaciones(estado.data_dir)
    reg2.revisar("GENERAL", "clave nueva aceptada", "GENERAL")
    reg2.registrar_envio_humano(H2, "lab")
    out = R.restaurar(estado, r)
    assert out["revision_general"] == "restaurado_registro_actual_con_otra_clave"
    assert RegistroOperaciones(estado.data_dir).general["motivo"] == "restaurado_registro_actual_con_otra_clave"


def test_no_se_restaura_con_forms_abierto(estado, tmp_path):
    RegistroOperaciones(estado.data_dir)
    r = R.respaldar(estado, tmp_path / "respaldos")["respaldo"]
    vivo = RegistroOperaciones(estado.data_dir, dueno=True)
    try:
        with pytest.raises(R.RespaldoInvalido):
            R.restaurar(estado, r)
    finally:
        vivo.soltar()


def test_orden_estado_restaurar_exige_confirmacion(estado, tmp_path, monkeypatch):
    from cauce_form_connector import cli
    monkeypatch.setenv("CAUCE_FORMS_DATA_DIR", estado.data_dir)
    monkeypatch.setenv("CAUCE_PROFILES_DIR", estado.profiles_dir)
    monkeypatch.setenv("CAUCE_ALLOWED_HOSTS_FILE", estado.allowed_hosts_file)
    RegistroOperaciones(estado.data_dir)
    assert cli.main(["estado", "respaldar", "--destino", str(tmp_path / "r")]) == 0
    zipf = next((tmp_path / "r").glob("respaldo-forms-*.zip"))
    assert cli.main(["estado", "verificar", str(zipf)]) == 0
    assert cli.main(["estado", "restaurar", str(zipf), "--confirmar", "si"]) == 1
    assert cli.main(["estado", "restaurar", str(zipf), "--confirmar", "RESTAURAR"]) == 0


# ================================================================== B · envíos de la persona con Chromium real
@pytest.fixture()
def portal():
    s = ServidorPortal().iniciar()
    yield s
    s.detener()


@pytest.fixture()
def interfaz(portal, tmp_path, monkeypatch):
    from servidor_app import servir
    from cauce_form_connector.web import app as web
    monkeypatch.setenv("CAUCE_FORMS_DATA_DIR", str(tmp_path / "forms"))
    monkeypatch.setenv("CAUCE_NE_SESSION_TOKEN_FILE", "")
    app = web.create_app()
    ws = app.state.ws
    with servir(app) as base:
        s = ws.browser()
        ws.rows = [fila(31)]
        from test_envio_protegido import rellenar
        ws.worker.run(lambda: rellenar(s, portal.url + "/tramite", fila(31)))
        ws.fila_rellenada = 0
        yield ws, s, base
        ws.close()


@pytest.mark.browser
def test_envio_de_la_persona_con_la_interfaz_en_reposo_llega_y_queda_registrado(interfaz, portal):
    """Antes, con la interfaz en reposo, la petición de la persona se quedaba parada en el interceptor de Forms
    (medido: 0 recibidas en 4 s) hasta la siguiente acción de la interfaz."""
    ws, s, _ = interfaz
    ws.worker.run(lambda: s.page.evaluate("setTimeout(() => document.querySelector('form').requestSubmit(), 200)"))
    time.sleep(3)                                     # ninguna llamada a Playwright: la interfaz está en reposo
    assert portal.portal.estadisticas()["recibidos"] == 1
    ops = ws.registro.lista()
    assert len(ops) == 1 and ops[0]["estado"] == INCIERTA


@pytest.mark.browser
def test_envio_por_javascript_sin_evento_submit_queda_registrado_antes_de_salir(interfaz, portal):
    ws, s, base = interfaz
    import httpx
    ws.worker.run(lambda: s.page.evaluate("""() => { const f = document.querySelector('form');
        setTimeout(() => fetch(f.action, {method: 'POST', body: new FormData(f)}), 100); }"""))
    time.sleep(2.5)
    ops = ws.registro.lista()
    assert len(ops) == 1 and ops[0]["motivo"] == "envio_humano_por_javascript_sin_acuse", ops
    assert portal.portal.estadisticas()["peticiones_post"] == 1
    r = httpx.post(base + "/api/submit", json={"phrase": "ENVIAR"}, timeout=30)
    assert r.status_code == 403                       # la aprobación de esa fila quedó anulada
    assert httpx.get(base + "/api/diagnostico").json()["operaciones_inciertas"] == 1


@pytest.mark.browser
def test_si_no_se_puede_registrar_el_envio_de_la_persona_no_sale(interfaz, portal, monkeypatch):
    ws, s, _ = interfaz

    def falla(*a, **k):
        raise OSError("disco lleno (simulado)")
    monkeypatch.setattr(ws.registro, "registrar_envio_humano", falla)
    ws.worker.run(lambda: s.page.evaluate("""() => { const f = document.querySelector('form');
        setTimeout(() => fetch(f.action, {method: 'POST', body: new FormData(f)}).catch(() => {}), 100); }"""))
    time.sleep(2)
    assert portal.portal.estadisticas()["peticiones_post"] == 0


@pytest.mark.browser
def test_envio_de_la_persona_pendiente_y_enviar_a_la_vez_no_duplica(interfaz, portal):
    """La petición de la persona estaba parada (interfaz sin atender eventos) y alguien pulsa ENVIAR: antes salían las
    dos. Ahora Forms atiende los eventos pendientes justo antes del clic y, si la persona ya envió, no pulsa."""
    import httpx
    ws, s, base = interfaz
    ws.worker.en_reposo = None                        # peor caso: nadie atiende los eventos mientras tanto
    ws.worker.run(lambda: s.page.evaluate("""() => { const f = document.querySelector('form');
        setTimeout(() => fetch(f.action, {method: 'POST', body: new FormData(f)}), 50); }"""))
    time.sleep(1.0)
    assert portal.portal.estadisticas()["peticiones_post"] == 0      # parada en el interceptor
    r = httpx.post(base + "/api/submit", json={"phrase": "ENVIAR"}, timeout=30)
    assert r.status_code == 409, r.text
    time.sleep(0.5)
    st = portal.portal.estadisticas()
    assert st["peticiones_post"] == 1, st
    estados = sorted(o["estado"] for o in ws.registro.lista())
    # según en qué llamada se atienda el evento pendiente: bloqueada por la incierta, o abortada justo antes del clic
    assert estados in (["incierta"], ["incierta", "no_enviada"]), estados


# ================================================================== B · modo preparar (contrato con Monitor)
@pytest.mark.browser
def test_preparar_envio_de_la_persona_queda_en_el_registro_y_no_se_prepara_otra_vez(tmp_path, monkeypatch):
    import cauce_form_connector.integracion as I
    from integ_helpers import LAB, Sitio, aprobar, fabrica_espia, settings_para, solicitud
    monkeypatch.setattr(I, "GRACIA_ACTIVACION_S", 0.5)
    sitio = Sitio()
    sitio.paginas["/f.html"] = (LAB / "01_tradicional.html").read_text(encoding="utf-8")
    try:
        st = settings_para(tmp_path)
        info = aprobar(st, sitio.base + "/f.html")

        def envia_por_javascript(s):            # la persona envía con un botón de la página que usa fetch
            s.page.wait_for_timeout(600)
            s.page.evaluate("fetch('/guardar', {method: 'POST', body: 'x'}).catch(() => {})")
            s.page.wait_for_timeout(300)
            s.page.close()
        sol = solicitud(info, espera=20)
        resp, _ = I.ejecutar(json.dumps(sol), settings=st, state_dir=tmp_path / "estado", fabrica_sesion=fabrica_espia,
                             gancho_revision=envia_por_javascript)
        ops = RegistroOperaciones(st.data_dir).lista()
        assert len(ops) == 1 and ops[0]["estado"] == INCIERTA
        assert ops[0]["motivo"] == "envio_humano_por_javascript_preparar"
        assert ops[0]["ref_solicitud"] == sol["solicitud_id"]          # para cruzarlo con el registro de Monitor
        assert "zz" not in json.dumps(ops).lower()                      # sin valores
        # Monitor vuelve a pedir el MISMO expediente: no se prepara (ni se abre el navegador)
        resp2, salida2 = I.ejecutar(json.dumps(solicitud(info, espera=20)), settings=st, state_dir=tmp_path / "estado",
                                    fabrica_sesion=lambda *_: pytest.fail("no debía abrir el navegador"))
        assert resp2["estado"] == "detenido_intervencion_humana"
        assert resp2["errores"] == [{"codigo": "INTERVENCION_HUMANA_REQUERIDA"}]
        # Otro expediente distinto sí se prepara
        from integ_helpers import VALORES
        otros = {**VALORES, "nombre": "Otra Persona Ficticia"}
        resp3, _ = I.ejecutar(json.dumps(solicitud(info, valores=otros, espera=0)), settings=st,
                              state_dir=tmp_path / "estado", fabrica_sesion=fabrica_espia)
        assert resp3["estado"] == "revision_caducada"
    finally:
        sitio.cerrar()
