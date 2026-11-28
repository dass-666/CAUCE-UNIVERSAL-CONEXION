"""Bloques B, D y F · Forms contra Network Engine RC3 REAL (proceso aparte, red SIMULADA del motor).

Necesita ``CAUCE_NE_REPO`` (copia de solo lectura de RC3). En CI se fija el commit f93dd58.
Lo que NO prueba: Windows físico, DPAPI real (aquí el archivo es 0600 sin cifrar), redes reales ni Monitor.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from cauce_form_connector.operaciones import INCIERTA, REVISADA, RegistroOperaciones
from cauce_form_connector.red import diagnostico as D
from cauce_form_connector.red.cliente_ne import CauceSessionClient, read_secret
from cauce_form_connector.red.diagnostico import DiagnosticoRed
from cauce_form_connector.red.identidad import Puesto
from ne_proceso import MotorNE, commit_ne, esperar, necesita_ne

pytestmark = necesita_ne


@pytest.fixture()
def ne(tmp_path):
    m = MotorNE(tmp_path / "ne").iniciar()
    yield m
    m.detener()


class Espia(CauceSessionClient):
    """Registra cada cuerpo enviado al motor, para comprobar la lista blanca."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.enviados: list = []
        real = self._c.call

        def call(method, path, body=None, timeout=None):
            self.enviados.append((method, path, dict(body) if body else None))
            return real(method, path, body, timeout)
        self._c.call = call


def forms(ne: MotorNE, tmp: Path, nombre: str = "forms-puesto-1", registro=None, intervalo=1.0, **kw):
    """Un «Forms»: credencial session oficial, puesto estable, diagnóstico y (opcional) registro de operaciones."""
    tid, ruta = kw.pop("credencial", None) or ne.credencial(nombre)
    puesto = Puesto(tmp / nombre)
    cli = Espia(read_secret(str(ruta)), port=ne.port)
    reg = registro
    d = DiagnosticoRed(cli, puesto, conexion="sim-a", intervalo_s=intervalo, latido_max_s=kw.pop("latido_max_s", 30),
                       hay_inciertas=reg.hay_abiertas if reg else None,
                       al_ver_marca=reg.marca_vista_en if reg else None,
                       al_resolver=reg.resuelta_por_operador_ne if reg else None, **kw)
    return d, tid, ruta, puesto


def fase(d, f, limite=30):
    return esperar(lambda: (d.latir(obligatorio=True) or True) and d.estado()["fase"] == f, limite, 0.6)


def test_version_de_network_engine_es_rc3():
    c = commit_ne()
    assert c in ("", "f93dd58eeddc1cc3032711bb0b6e378708063d41") or c.startswith("f93dd58"), c


def test_01_funcionamiento_normal_y_lista_blanca(ne, tmp_path):
    d, *_ = forms(ne, tmp_path)
    assert d.latir()
    e = d.estado()
    assert e["motor"] == "conectado" and e["fase"] in ("iniciada", "activa")
    assert fase(d, "activa")
    assert d.estado()["conectividad"] == "verificada"
    assert d.operacion_inicio() is True
    assert ne.sesion(d.c.session_id)["operation"] == "in_progress"
    assert d.operacion_fin(resultado_seguro=True) is True
    v = ne.sesion(d.c.session_id)
    assert v["operation"] == "idle" and v["requires_human"] is False
    permitidos = {"register": {"label", "connection_id", "authorized_by_user", "heartbeat_timeout_s"},
                  "heartbeat": {"status", "error_code", "operation"}}
    for _m, path, body in d.c.enviados:
        if body:
            assert set(body) <= permitidos["heartbeat" if path.endswith("/heartbeat") else "register"], body
    # identidad estable y sin datos personales
    assert v["label"].startswith("forms-") and v["registered_by"] == "forms-puesto-1"


def test_02_motor_apagado_no_bloquea_y_dice_sin_diagnostico(ne, tmp_path):
    d, *_ = forms(ne, tmp_path)
    assert fase(d, "activa")
    ne.detener()
    t0 = time.monotonic()
    llegado = d.operacion_inicio()
    assert llegado is False and time.monotonic() - t0 < D.AVISO_MAX_S + 0.5
    e = d.estado()
    assert e["motor"] == "no_disponible" and e["conectividad"] == "sin_diagnostico"
    assert "NO indica falta de internet" in e["motor_texto"]
    assert e["fase"] is None                          # no se muestra una fase vieja como si fuera actual
    assert d.avisos_no_entregados == 1


def test_03_motor_reiniciado_con_operacion_en_curso_pide_revision(ne, tmp_path):
    reg = RegistroOperaciones(tmp_path / "ops")
    d, *_ = forms(ne, tmp_path, registro=reg)
    assert fase(d, "activa")
    sid = d.c.session_id
    assert d.operacion_inicio()
    ne.detener(matar=True)                            # el motor cae a mitad de la operación
    ne.iniciar()
    assert d.latir(obligatorio=True)
    e = d.estado()
    assert d.c.session_id == sid                      # mismo id: la sesión se restauró
    assert e["requiere_persona"] and e["motivo"] == "engine_restart_during_operation"


def test_04_forms_reiniciado_sigue_con_el_mismo_id_y_reentrega_la_marca(ne, tmp_path):
    reg = RegistroOperaciones(tmp_path / "ops")
    d, tid, ruta, puesto = forms(ne, tmp_path, registro=reg)
    assert fase(d, "activa")
    sid = d.c.session_id
    op = reg.iniciar("h_x", "http://127.0.0.1/tramite", None, sid)     # «Forms muere» durante un envío
    d.detener()
    reg2 = RegistroOperaciones(tmp_path / "ops")                       # Forms vuelve
    assert reg2.get(op["id"])["estado"] == INCIERTA
    time.sleep(0.6)                                                    # un reinicio real tarda más que eso
    d2, *_ = forms(ne, tmp_path, registro=reg2, credencial=(tid, ruta))
    d2.exigir_marca()
    assert d2.latir(obligatorio=True)
    assert d2.c.session_id == sid                                      # sin 409: recordó su id
    assert esperar(lambda: (d2.latir(obligatorio=True) or True) and ne.sesion(sid).get("requires_human"), 10)
    assert reg2.get(op["id"])["marca_ne"] == sid


def test_06_reinicio_simultaneo_de_motor_y_forms_hereda_la_marca(ne, tmp_path):
    reg = RegistroOperaciones(tmp_path / "ops")
    d, tid, ruta, puesto = forms(ne, tmp_path, registro=reg)
    assert fase(d, "activa")
    assert d.operacion_inicio()
    reg.iniciar("h_y", "x", None, d.c.session_id)
    d.detener()
    ne.detener(matar=True)
    (tmp_path / "forms-puesto-1" / "puesto.json").unlink()            # peor caso: Forms pierde también el id
    ne.iniciar()
    reg2 = RegistroOperaciones(tmp_path / "ops")
    d2, *_ = forms(ne, tmp_path, registro=reg2, credencial=(tid, ruta))
    assert d2.latir(obligatorio=True)
    e = d2.estado()
    assert e["requiere_persona"] is True              # la nueva sesión HEREDA la marca de la restaurada
    resumen = ne.resumen()
    assert any(c.get("reason") == "replaced_after_restart" for c in resumen["closed"])


def test_07_credencial_revocada_durante_la_operacion_no_borra_alertas(ne, tmp_path):
    reg = RegistroOperaciones(tmp_path / "ops")
    d, tid, ruta, _ = forms(ne, tmp_path, registro=reg, latido_max_s=5)
    assert fase(d, "activa")
    sid = d.c.session_id
    op = reg.iniciar("h_z", "x", None, sid)
    assert d.operacion_inicio()
    ne.revocar(tid)
    reg.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    assert d.operacion_fin(resultado_seguro=False) is False
    e = d.estado()
    assert e["motor"] == "credencial_invalida"
    assert e["marca_por_entregar"] is True            # Forms sabe que el motor no recibió el «error»
    assert d.latir() is False                         # detenido: no insiste con una credencial rota
    assert reg.get(op["id"])["estado"] == INCIERTA    # la alerta local sigue (y sobrevive a reinicios)
    # En el motor: la sesión queda sin latidos con la operación en curso → pide revisión humana por sí sola
    assert esperar(lambda: ne.sesion(sid).get("requires_human") is True, 20)
    assert ne.sesion(sid)["human_reason"] == "operation_interrupted"


def test_08_09_perdida_y_recuperacion_no_desbloquean_lo_incierto(ne, tmp_path):
    reg = RegistroOperaciones(tmp_path / "ops")
    d, *_ = forms(ne, tmp_path, registro=reg)
    assert fase(d, "activa")
    ne.red("down")
    assert fase(d, "perdida_conectividad", 40)
    assert d.estado()["conectividad"] == "perdida"
    op = reg.iniciar("h_w", "x", None, d.c.session_id)
    d.operacion_inicio()
    reg.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    d.operacion_fin(resultado_seguro=False)
    assert fase(d, "requiere_intervencion", 20)
    ne.red("up")
    time.sleep(8)
    for _ in range(4):
        d.latir(obligatorio=True)
    e = d.estado()
    assert e["conectividad"] == "verificada"
    assert e["requiere_persona"] is True and e["fase"] == "requiere_intervencion"
    assert reg.get(op["id"])["estado"] == INCIERTA   # volver la red NO desbloquea


def test_operador_resuelve_en_ne_y_forms_lo_ve_en_la_misma_sesion(ne, tmp_path):
    reg = RegistroOperaciones(tmp_path / "ops")
    d, *_ = forms(ne, tmp_path, registro=reg)
    assert fase(d, "activa")
    op = reg.iniciar("h_r", "x", None, d.c.session_id)
    d.operacion_inicio()
    reg.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    d.operacion_fin(resultado_seguro=False)
    assert esperar(lambda: (d.latir(obligatorio=True) or True) and d.estado()["requiere_persona"], 10)
    assert reg.get(op["id"])["marca_ne"] == d.c.session_id
    ne.resolver(d.c.session_id)                        # el OPERADOR, con su credencial administrativa
    assert esperar(lambda: (d.latir(obligatorio=True) or True) and not d.estado()["requiere_persona"], 10)
    assert reg.get(op["id"])["estado"] == REVISADA
    assert reg.get(op["id"])["revision"]["por"] == "operador_network_engine"


def test_forms_no_puede_resolver_marcas(ne, tmp_path):
    d, tid, ruta, _ = forms(ne, tmp_path)
    assert d.latir()
    sid = d.c.session_id
    tok = read_secret(str(ruta))
    st, _ = ne.llamar("POST", f"/sessions/{sid}/resolve", {}, token=tok)
    assert st == 403
    st, _ = ne.llamar("GET", "/sessions/pending", token=tok)
    assert st == 403
    # y el código de Forms no contiene ninguna llamada a las rutas de resolución
    raiz = Path(__file__).resolve().parents[1] / "cauce_form_connector"
    for py in raiz.rglob("*.py"):
        texto = py.read_text("utf-8")
        assert '/resolve"' not in texto and "/sessions/pending" not in texto, py


def test_10_navegador_cerrado_se_informa(ne, tmp_path):
    d, *_ = forms(ne, tmp_path)
    assert fase(d, "activa")
    d.navegador(False, "browser_closed")
    assert fase(d, "interrumpida", 10)
    d.navegador(True)
    assert esperar(lambda: (d.latir(obligatorio=True) or True) and d.estado()["fase"] in ("recuperada", "activa"), 10)


def test_14_cerrar_la_sesion_con_revision_pendiente_no_la_borra(ne, tmp_path):
    reg = RegistroOperaciones(tmp_path / "ops")
    d, tid, ruta, puesto = forms(ne, tmp_path, registro=reg)
    assert fase(d, "activa")
    op = reg.iniciar("h_c", "x", None, d.c.session_id)
    d.operacion_inicio()
    reg.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    d.operacion_fin(resultado_seguro=False)
    assert esperar(lambda: (d.latir(obligatorio=True) or True) and d.estado()["requiere_persona"], 10)
    d.detener(cerrar=True)
    assert puesto.sesion_ne is None
    pend = ne.resumen()["pending_human_review"]
    assert any(p["label"] == puesto.etiqueta for p in pend)          # visible para el centro de revisiones
    assert reg.get(op["id"])["estado"] == INCIERTA                   # y en Forms
    time.sleep(0.6)
    d2, *_ = forms(ne, tmp_path, registro=reg, credencial=(tid, ruta))
    assert d2.latir(obligatorio=True)
    assert d2.estado()["requiere_persona"] is True                    # la nueva sesión la hereda


def test_15_estado_del_motor_danado_pide_revision(ne, tmp_path):
    d, tid, ruta, _ = forms(ne, tmp_path)
    assert fase(d, "activa")
    ne.detener()
    for n in ("sessions_state.json", "sessions_state.json.bak"):
        p = ne.data / n
        if p.exists():
            p.write_text("{dañado")
    ne.iniciar()
    assert d.latir(obligatorio=True)
    assert d.estado()["requiere_persona"] is True and d.estado()["motivo"] == "previous_state_unreadable"


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="SIGSTOP no existe en Windows")
def test_16_motor_lento_o_congelado_no_retiene_la_accion(ne, tmp_path):
    d, *_ = forms(ne, tmp_path, intervalo=1.0, desactualizado_s=4)
    d.iniciar()
    assert esperar(lambda: d.estado()["fase"] == "activa", 15)
    ne.congelar()
    try:
        t0 = time.monotonic()
        d.operacion_inicio()
        assert time.monotonic() - t0 < D.AVISO_MAX_S + 0.6
        assert esperar(lambda: d.estado()["conectividad"] in ("desactualizada", "sin_diagnostico"), 15)
    finally:
        ne.descongelar()
    assert esperar(lambda: d.estado()["motor"] == "conectado" and d.estado()["conectividad"] == "verificada", 20)
    d.detener()


def test_17_varias_sesiones_simultaneas_aisladas(ne, tmp_path):
    puestos = [forms(ne, tmp_path, f"forms-puesto-{i}") for i in range(1, 4)]
    for d, *_ in puestos:
        assert d.latir()
    etiquetas = {p[3].etiqueta for p in puestos}
    assert len(etiquetas) == 3
    a, b = puestos[0][0], puestos[1][0]
    a.operacion_inicio()
    a.operacion_fin(resultado_seguro=False)
    assert esperar(lambda: (a.latir(obligatorio=True) or True) and a.estado()["requiere_persona"], 10)
    for d, *_ in puestos[1:]:
        d.latir(obligatorio=True)
        assert d.estado()["requiere_persona"] is False                # la marca de A no contamina a B ni C
    # B no puede latir en la sesión de A (acceso entre sesiones)
    tok_b = read_secret(str(puestos[1][2]))
    st, body = ne.llamar("POST", f"/sessions/{a.c.session_id}/heartbeat", {"status": "ok"}, token=tok_b)
    assert st == 403 and body["error"]["code"] == "not_session_owner"
    st, _ = ne.llamar("GET", f"/sessions/{a.c.session_id}", token=tok_b)
    assert st == 403
    assert ne.resumen()["isolation"]["one_credential_per_session"] is True
    # latidos concurrentes de los tres puestos
    errores = []

    def golpear(d):
        for _ in range(5):
            if not d.latir(obligatorio=True):
                errores.append(d.estado())
    ts = [threading.Thread(target=golpear, args=(p[0],)) for p in puestos]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errores, errores


@pytest.mark.parametrize("ambito", ["admin", "read", "review"])
def test_18_403_credenciales_de_otro_ambito_se_rechazan(ne, tmp_path, ambito):
    if ambito == "admin":
        ruta = ne.data / "api.token"                  # la credencial maestra del motor: Forms NUNCA debe usarla
        tid = None
    else:
        tid, ruta = ne.credencial(f"otra-{ambito}", ambito)
    d, *_ = forms(ne, tmp_path, credencial=(tid, ruta))
    assert d.latir() is False
    e = d.estado()
    assert e["motor"] == "credencial_incorrecta" and e["codigo"] == "session_credential_required" if ambito == "admin" \
        else e["motor"] == "credencial_incorrecta"
    assert d.latir() is False                          # detenido: no insiste
    assert ne.resumen()["sessions"] == []


def test_18_429_se_espera_sin_insistir(ne, tmp_path):
    d, tid, ruta, _ = forms(ne, tmp_path)
    assert d.latir()
    tok = read_secret(str(ruta))
    ne.llamar("POST", f"/sessions/{d.c.session_id}/heartbeat", {"status": "ok"}, token=tok)   # otro latido ajeno
    d._ultimo_envio = float("-inf")
    assert d.latir(obligatorio=True) is False
    assert d.estado()["motor"] == "limitado"
    n = len(d.c.enviados)
    assert d.latir() is False and len(d.c.enviados) == n                    # en pausa: no envía
    time.sleep(D.PAUSA_429_S + 0.1)
    assert d.latir() is True and d.estado()["motor"] == "conectado"


def test_sesion_ocupada_si_forms_pierde_el_id_con_el_motor_vivo(ne, tmp_path):
    """Solicitud para Network Engine: con el motor vivo, si Forms pierde su id, el registro choca con 409 hasta que la
    sesión caduque (``restore_max_age_s``, 1 h por omisión). Forms lo dice claramente y no insiste a ciegas."""
    d, tid, ruta, puesto = forms(ne, tmp_path)
    assert d.latir()
    d.detener()
    (tmp_path / "forms-puesto-1" / "puesto.json").unlink()
    time.sleep(0.6)
    d2, *_ = forms(ne, tmp_path, credencial=(tid, ruta))
    assert d2.latir(obligatorio=True) is False
    assert d2.estado()["motor"] == "sesion_ocupada"


def test_ningun_secreto_en_los_archivos_de_forms(ne, tmp_path):
    reg = RegistroOperaciones(tmp_path / "forms-puesto-1")
    d, tid, ruta, puesto = forms(ne, tmp_path, registro=reg)
    assert d.latir()
    op = reg.iniciar("h_s", "http://127.0.0.1/tramite", "tok_abcdefgh", d.c.session_id)
    reg.cerrar(op["id"], INCIERTA, "x")
    tok = read_secret(str(ruta))
    secreto = tok.split(".")[-1]
    for p in (tmp_path / "forms-puesto-1").rglob("*"):
        if p.is_file():
            assert secreto not in p.read_bytes().decode("utf-8", "ignore"), p
    assert json.loads((tmp_path / "forms-puesto-1" / "puesto.json").read_text()).keys() == {"etiqueta", "sesion_ne"}


def test_credencial_protegida_con_el_mecanismo_del_sistema(ne, tmp_path):
    """Windows: la orden oficial guarda la credencial con DPAPI y Forms la descifra (mismo usuario). Linux: 0600."""
    import sys
    from cauce_form_connector.red.cliente_ne import storage_method
    tid, ruta = ne.credencial("forms-dpapi")
    esperado = "dpapi" if sys.platform == "win32" else "file-0600"
    assert storage_method(str(ruta)) == esperado
    tok = read_secret(str(ruta))
    assert tok.startswith("cr1.") and tok.encode() not in ruta.read_bytes() if sys.platform == "win32" else True
    d, *_ = forms(ne, tmp_path, credencial=(tid, ruta))
    assert d.latir() and d.estado()["motor"] == "conectado"


def test_paso_09b_con_forms_nativo_mismo_contrato(ne, tmp_path):
    """Bloque G: el sustituto propuesto del consumidor de referencia del paso 09b recorre las 8 fases con Forms nativo,
    Chromium real y el portal propio, y no envía nada."""
    import os
    import subprocess
    import sys
    from lab_portal.servidor import ServidorPortal
    tid, ruta = ne.credencial("lab-sesion-09b")
    portal = ServidorPortal(0).iniciar()
    try:
        env = dict(os.environ, CAUCE_LAB_NE_PORT=str(ne.port), CAUCE_LAB_NE_ADMIN=ne.admin,
                   CAUCE_LAB_NE_SESSION=read_secret(str(ruta)), CAUCE_LAB_FORMS_URL=portal.url + "/tramite",
                   CAUCE_LAB_ETIQUETA="lab-forms-09b", CAUCE_FORMS_DATA_DIR=str(tmp_path / "forms"))
        raiz = Path(__file__).resolve().parents[1]
        r = subprocess.run([sys.executable, str(raiz / "scripts" / "paso_09b_nativo.py")], cwd=str(raiz), env=env,
                           capture_output=True, text=True, timeout=400)
        res = json.loads(r.stdout.strip().splitlines()[-1])
        assert not res.get("error"), (res, r.stderr[-2000:])
        assert all(res["fases"].values()), res
        assert res["formulario_enviado"] is False
        assert res["sigue_pidiendo_persona_tras_recuperar"] is True
        assert portal.portal.estadisticas()["peticiones_post"] == 0
    finally:
        portal.detener()
