"""Forms contra Network Engine E10 REAL (PR #16, commit 4f223a7, laboratorio): ``local_review`` y revisiones.

Necesita ``CAUCE_NE_E10_REPO`` (copia de solo lectura de Network Engine en 4f223a7). Red SIMULADA del motor y datos
ficticios. Lo que NO prueba: Windows físico ni redes reales.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import pytest

from cauce_form_connector.operaciones import INCIERTA, OperacionBloqueada, RegistroOperaciones
from ne_proceso import E10, NE_E10_REPO, MotorNE, esperar
from test_red_ne_rc3 import forms

pytestmark = pytest.mark.skipif(not (NE_E10_REPO and Path(NE_E10_REPO, "cauce", "sessions.py").exists()),
                                reason="CAUCE_NE_E10_REPO no apunta a una copia de Network Engine E10")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def ne(tmp_path):
    m = MotorNE(tmp_path / "ne", repo=NE_E10_REPO).iniciar()
    yield m
    m.detener()


def latir_hasta(d, cond, limite=10):
    return esperar(lambda: (d.latir(obligatorio=True) or True) and cond(), limite, 0.6)


def _incierta(ne, tmp_path):
    reg = RegistroOperaciones(tmp_path / "ops")
    d, *_ = forms(ne, tmp_path, registro=reg)
    assert latir_hasta(d, lambda: d.estado()["fase"] == "activa", 20)
    op = reg.iniciar("h_" + "e" * 24, "lab", None, d.c.session_id)
    d.operacion_inicio()
    reg.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    d.operacion_fin(resultado_seguro=False)
    assert latir_hasta(d, lambda: d.estado()["requiere_persona"])
    return reg, d, op


def test_version_de_network_engine_es_e10():
    import subprocess
    c = subprocess.run(["git", "-C", NE_E10_REPO, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert c == E10


def test_pin_e10_coincide_con_el_motor():
    pin = (ROOT / "cauce_form_connector" / "red" / "PIN.txt").read_text("utf-8")
    pares = re.findall(r"^e10-sha256 (\S+): ([0-9a-f]{64})$", pin, re.M)
    assert len(pares) == 3
    for ruta, sha in pares:
        real = hashlib.sha256((Path(NE_E10_REPO) / ruta).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        assert real == sha, ruta
    orig = (Path(NE_E10_REPO) / "cauce" / "client.py").read_text("utf-8")
    copia = (ROOT / "cauce_form_connector" / "red" / "cliente_ne.py").read_text("utf-8")
    for f in ('body["local_review"] = local_review', '"/sessions/{self._sid()}/reviews"'):
        assert f in orig and f in copia, f


def test_forms_declara_local_review_pendiente_y_el_motor_no_deja_cerrar_la_marca(ne, tmp_path):
    reg, d, op = _incierta(ne, tmp_path)
    assert d.e10 is True and d.local_review_enviado == "pending"
    assert any((b or {}).get("local_review") == "pending" for _, _, b in d.c.enviados)
    assert latir_hasta(d, lambda: ne.sesion(d.c.session_id)["operation"] == "idle")
    v = ne.sesion(d.c.session_id)
    assert v["local_review"] == "pending" and v["requires_human"]
    r = ne.resolver(d.c.session_id, esperado=409, nota="revisión de red del laboratorio")
    assert r["error"]["code"] == "condition_persists"
    assert r["error"]["details"]["condition"] == "owner_local_review_pending"
    assert reg.get(op["id"])["estado"] == INCIERTA                   # nada cambió en el puesto
    with pytest.raises(OperacionBloqueada):
        reg.comprobar("h_" + "e" * 24)


def test_revision_en_el_puesto_y_despues_en_el_motor_queda_limpio_y_la_revision_de_red_no_confirma(ne, tmp_path):
    reg, d, op = _incierta(ne, tmp_path)
    reg.revisar(op["id"], "comprobado en el portal: no llegó", op["id"], resultado="no_recibido")
    assert latir_hasta(d, lambda: ne.sesion(d.c.session_id).get("local_review") == "none"
                       and ne.sesion(d.c.session_id)["operation"] == "idle")
    ne.resolver(d.c.session_id, nota="revisada la red del puesto")
    assert latir_hasta(d, lambda: not d.estado()["requiere_persona"])
    time.sleep(2.5)
    assert d.latir(obligatorio=True) and not d.estado()["requiere_persona"]      # no reaparece
    rv = d.ultima_revision_red                                     # consultada solo para anotarla
    assert rv and rv["confirms_receipt"] is False and rv["evidence"] == "none"
    assert re.fullmatch(r"[0-9a-f]{16}", rv["note_sha256"])        # la nota del operador no llega, solo su huella


def test_a1_con_e10_ni_siquiera_un_resolve_aceptado_desbloquea(ne, tmp_path, monkeypatch):
    """Peor caso: el motor acepta un resolve mientras el puesto tiene una incierta (por ejemplo, Forms todavía no
    había declarado local_review). Forms lo anota y sigue bloqueando: A1 no puede repetirse."""
    reg, d, op = _incierta(ne, tmp_path)
    monkeypatch.setattr(d, "_local_review", lambda: "none")         # simula un latido que aún no lo declaraba
    assert latir_hasta(d, lambda: ne.sesion(d.c.session_id).get("local_review") == "none"
                       and ne.sesion(d.c.session_id)["operation"] == "idle")
    ne.resolver(d.c.session_id, nota="resolución prematura")
    assert latir_hasta(d, lambda: reg.get(op["id"]).get("revision_red"))
    o = reg.get(op["id"])
    assert o["estado"] == INCIERTA and o["revision_red"]["confirma_recepcion"] is False
    assert o["revision_red"].get("id", "").startswith("rv_")
    with pytest.raises(OperacionBloqueada):
        reg.comprobar("h_" + "e" * 24)
    monkeypatch.undo()
    assert latir_hasta(d, lambda: ne.sesion(d.c.session_id)["requires_human"])  # y la marca vuelve al motor


def test_s1_forms_pierde_su_id_y_recupera_la_sesion_heredando_la_marca(ne, tmp_path):
    """E10 S1: con el motor vivo, si Forms pierde su id, espera lo que dice el motor (retry_after_s) y, cuando la
    sesión anterior calla todo su plazo, la nueva la sustituye y hereda la marca. Lo incierto sigue bloqueado."""
    reg = RegistroOperaciones(tmp_path / "ops")
    d, tid, ruta, puesto = forms(ne, tmp_path, registro=reg, latido_max_s=5)
    assert latir_hasta(d, lambda: d.estado()["fase"] == "activa", 20)
    op = reg.iniciar("h_" + "f" * 24, "lab", None, d.c.session_id)
    d.operacion_inicio()
    reg.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    d.operacion_fin(resultado_seguro=False)
    assert latir_hasta(d, lambda: d.estado()["requiere_persona"])
    viejo = d.c.session_id
    (tmp_path / "forms-puesto-1" / "puesto.json").unlink()            # Forms pierde su id con el motor vivo
    d2, *_ = forms(ne, tmp_path, registro=reg, credencial=(tid, ruta), latido_max_s=5)
    d.latir(obligatorio=True)                                          # la anterior aún late una vez
    assert d2.latir(obligatorio=True) is False and d2.estado()["motor"] == "sesion_ocupada"
    assert d2._espera_hasta > d2.reloj()                               # respeta retry_after_s
    assert esperar(lambda: (d2.latir(obligatorio=True) or True) and d2.estado()["motor"] == "conectado"
                   and d2.c.session_id and d2.c.session_id != viejo, 20, 0.6)
    assert latir_hasta(d2, lambda: d2.estado()["requiere_persona"])   # hereda (o vuelve a entregar) la marca
    assert reg.get(op["id"])["estado"] == INCIERTA
    with pytest.raises(OperacionBloqueada):
        reg.comprobar("h_" + "f" * 24)


def test_a5_credencial_rotada_con_la_sesion_vieja_viva_no_detiene_forms_y_la_marca_sigue(ne, tmp_path):
    """A5 de E10: el puesto guarda el id de una sesión de la credencial ANTERIOR (sin revocar, sigue viva). Antes el
    403 not_session_owner dejaba Forms en credencial_incorrecta hasta un reanudar() manual. Ahora registra una propia
    y vuelve a entregar la marca. Lo incierto sigue bloqueado en el puesto."""
    reg = RegistroOperaciones(tmp_path / "ops")
    d, tid, ruta, puesto = forms(ne, tmp_path, registro=reg)
    assert latir_hasta(d, lambda: d.estado()["fase"] == "activa", 20)
    op = reg.iniciar("h_" + "a" * 24, "lab", None, d.c.session_id)
    d.operacion_inicio()
    reg.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    d.operacion_fin(resultado_seguro=False)
    assert latir_hasta(d, lambda: d.estado()["requiere_persona"])
    viejo = d.c.session_id
    nueva = ne.credencial("forms-puesto-1-rotada")                     # rotación SIN revocar la anterior
    d2, *_ = forms(ne, tmp_path, registro=reg, credencial=nueva)        # mismo puesto.json: recuerda la sesión vieja
    assert d2.c.session_id == viejo
    d2.latir(obligatorio=True)
    # la sesión vieja sigue viva y ocupa la etiqueta: Forms espera y reintenta (antes: detenido para siempre)
    assert d2.estado()["motor"] != "credencial_incorrecta", d2.estado()
    assert d2.c.session_id is None
    ne.revocar(tid)                                                    # el administrador retira la credencial vieja
    assert latir_hasta(d2, lambda: d2.estado()["motor"] == "conectado" and d2.c.session_id not in (None, viejo), 20)
    assert latir_hasta(d2, lambda: ne.sesion(d2.c.session_id)["requires_human"])
    assert reg.get(op["id"])["estado"] == INCIERTA
    with pytest.raises(OperacionBloqueada):
        reg.comprobar("h_" + "a" * 24)


def test_a5_un_403_con_la_sesion_recien_registrada_si_detiene(tmp_path):
    """Si el 403 llega justo después de registrar (ámbito incorrecto u otro fallo real), Forms se detiene: sin bucle."""
    from cauce_form_connector.red.cliente_ne import CauceAPIError
    from cauce_form_connector.red.diagnostico import DiagnosticoRed

    class Cli:
        session_id = None
        n = 0

        def register(self, *a, **k):
            self.session_id = "bs_" + "1" * 12
            return {}

        def heartbeat(self, *a, **k):
            Cli.n += 1
            raise CauceAPIError(403, "not_session_owner", "x")
    d = DiagnosticoRed(Cli(), None, conexion="sim-a", intervalo_s=1, latido_max_s=30)
    assert d.latir(obligatorio=True) is False
    assert d.estado()["motor"] == "credencial_incorrecta" and Cli.n == 1
