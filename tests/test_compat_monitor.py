"""Bloque F/G · compatibilidad con el código REAL de Cauce Monitor 7b92446 (lectura de red y centro de revisiones).

Network Engine RC3 real (modo simulado) + Forms nativo + el código de Monitor ``monitoreo/red_lectura`` ejecutado en un
proceso aparte con sus variables oficiales. Cada lectura de Monitor es un proceso nuevo: equivale a «Monitor
reiniciado». No se arranca la aplicación web completa de Monitor (inicio de sesión, base de datos): solo el servicio que
alimenta su página de red y su centro de revisiones. Se omite si no se indican CAUCE_NE_REPO y CAUCE_MONITOR_REPO.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cauce_form_connector.operaciones import INCIERTA, REVISADA, RegistroOperaciones
from ne_proceso import NE_REPO, MotorNE, esperar, necesita_ne
from test_red_ne_rc3 import fase, forms

MONITOR_REPO = os.environ.get("CAUCE_MONITOR_REPO", "")
pytestmark = [necesita_ne, pytest.mark.skipif(
    not (MONITOR_REPO and Path(MONITOR_REPO, "monitoreo", "red_lectura", "servicio.py").is_file()),
    reason="CAUCE_MONITOR_REPO no indicado (clon de Cauce Monitor 7b92446)")]
DRIVER = str(Path(__file__).with_name("monitor_lectura.py"))


@pytest.fixture()
def ne(tmp_path):
    m = MotorNE(tmp_path / "ne").iniciar()
    yield m
    m.detener()


@pytest.fixture()
def monitor(ne, tmp_path):
    """Credenciales de Monitor creadas con la orden oficial: «read» para ver y «review» (distinta) para resolver."""
    _, lectura = ne.credencial("monitor-lectura", "read")
    _, revision = ne.credencial("monitor-revisiones", "review")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CAUCE_", "MONITOREO_"))}
    env.update(CAUCE_MONITOR_REPO=MONITOR_REPO, MONITOREO_NETWORK_TOKEN_FILE=str(lectura),
               MONITOREO_NETWORK_REVISIONES_TOKEN_FILE=str(revision), MONITOREO_NETWORK_PUERTO=str(ne.port),
               MONITOREO_NETWORK_DIR=NE_REPO)

    def correr(*args: str) -> tuple[int, dict]:
        r = subprocess.run([sys.executable, DRIVER, *args], env=env, capture_output=True, text=True, timeout=60)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])
    return correr


def _mi_fila(vista: dict, etiqueta: str):
    return next((f for f in (vista.get("sesiones") or {}).get("activas", []) if f["etiqueta"] == etiqueta), None)


def _incierta(ne, tmp_path, reg):
    d, tid, ruta, puesto = forms(ne, tmp_path, registro=reg)
    assert fase(d, "activa")
    op = reg.iniciar("h_m", "x", None, d.c.session_id)
    d.operacion_inicio()
    reg.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    d.operacion_fin(resultado_seguro=False)
    assert esperar(lambda: (d.latir(obligatorio=True) or True) and d.estado()["requiere_persona"], 10)
    return d, tid, ruta, puesto, op


def test_05_monitor_ve_la_revision_de_forms_y_reiniciarlo_no_cambia_nada(ne, monitor, tmp_path):
    reg = RegistroOperaciones(tmp_path / "ops")
    d, _, _, puesto, op = _incierta(ne, tmp_path, reg)
    vistas = []
    for _ in range(2):                                   # dos arranques de Monitor seguidos
        rc, v = monitor("leer")
        assert rc == 0 and v["network_engine"] == "disponible", v
        vistas.append(v)
    for v in vistas:
        f = _mi_fila(v, puesto.etiqueta)
        assert f and f["requiere_persona"] is True and f["fase_codigo"] == "requiere_intervencion"
        assert v["sesiones"]["revisiones_pendientes"] >= 1
        texto = json.dumps(v)
        assert d.c.session_id not in texto and "tok_" not in texto      # Monitor no muestra ids ni credenciales
    assert d.latir(obligatorio=True) and d.estado()["requiere_persona"] is True   # Forms no se entera ni le afecta
    assert reg.get(op["id"])["estado"] == INCIERTA


def test_centro_de_revisiones_de_monitor_y_marca_de_forms_sin_sesion(ne, monitor, tmp_path):
    reg = RegistroOperaciones(tmp_path / "ops")
    d, tid, ruta, puesto, op = _incierta(ne, tmp_path, reg)
    d.detener(cerrar=True)                               # Forms cierra con la revisión pendiente
    rc, v = monitor("leer")
    rev = [r for r in v["sesiones"]["revisiones"] if r["etiqueta"] == puesto.etiqueta]
    assert len(rev) == 1 and rev[0]["tipo"] == "sesion" and rev[0]["confirmar_con"] == puesto.etiqueta
    pid = rev[0]["id"]
    rc, r = monitor("resolver", pid, "Comprobado en el portal de laboratorio", "otra-etiqueta")
    assert rc == 1 and r["code"] == "confirmation_mismatch"           # confirmación exacta
    rc, r = monitor("resolver", pid, "Comprobado en el portal de laboratorio", puesto.etiqueta)
    assert rc == 0, r
    rc, v = monitor("leer")
    assert not [r for r in v["sesiones"]["revisiones"] if r["etiqueta"] == puesto.etiqueta]
    # Forms NO da por revisada su operación local por eso: Network Engine no le dice que el operador resolvió la marca
    # (la credencial «session» no ve eventos ni marcas). Al volver, Forms vuelve a pedir revisión: más vale dos veces.
    assert reg.get(op["id"])["estado"] == INCIERTA
    time.sleep(0.6)
    d2, *_ = forms(ne, tmp_path, registro=reg, credencial=(tid, ruta))
    assert esperar(lambda: (d2.latir(obligatorio=True) or True) and d2.estado()["requiere_persona"], 10)
    rc, v = monitor("leer")
    assert _mi_fila(v, puesto.etiqueta)["requiere_persona"] is True
    # Ya entregada la marca, Forms vuelve a latir «ok / sin operación». Si el operador resolviera ANTES de ese latido
    # (último informe: error con operación en curso), Network Engine volvería a marcarla: hay que resolver otra vez
    # (dirección segura). Un operador real llega minutos después; aquí se espera a ese latido.
    assert esperar(lambda: (d2.latir(obligatorio=True) or True) and ne.sesion(d2.c.session_id)["operation"] == "idle", 10)
    # Revisión local del operador (orden de Forms) + resolución en el motor: todo queda limpio en los tres
    reg.revisar(op["id"], "Comprobado en el portal de laboratorio", op["id"])
    ne.resolver(d2.c.session_id)
    assert esperar(lambda: (d2.latir(obligatorio=True) or True) and not d2.estado()["requiere_persona"], 10)
    time.sleep(2.5)
    assert d2.latir(obligatorio=True) and not d2.estado()["requiere_persona"]        # no se vuelve a marcar
    rc, v = monitor("leer")
    assert _mi_fila(v, puesto.etiqueta)["requiere_persona"] is False
    assert reg.get(op["id"])["estado"] == REVISADA


def test_06_reinicio_simultaneo_de_los_tres(ne, monitor, tmp_path):
    reg = RegistroOperaciones(tmp_path / "ops")
    d, tid, ruta, puesto, op = _incierta(ne, tmp_path, reg)
    d.detener()                                          # Forms cae (sin cerrar la sesión)...
    ne.detener(matar=True)                               # ...el motor cae a la vez...
    rc, v = monitor("leer")                              # ...y Monitor arranca mientras el motor no está
    assert v["network_engine"] != "disponible"
    ne.iniciar()
    reg2 = RegistroOperaciones(tmp_path / "ops")         # Forms arranca de nuevo con su registro en disco
    time.sleep(0.6)
    d2, *_ = forms(ne, tmp_path, registro=reg2, credencial=(tid, ruta))
    assert esperar(lambda: (d2.latir(obligatorio=True) or True) and d2.estado()["requiere_persona"], 15)
    rc, v = monitor("leer")                              # Monitor arranca otra vez
    f = _mi_fila(v, puesto.etiqueta)
    assert f and f["requiere_persona"] is True
    assert reg2.get(op["id"])["estado"] == INCIERTA
