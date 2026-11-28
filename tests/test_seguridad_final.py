"""Seguridad antes de congelar Forms 0.2.0rc2: resoluciones de red, permisos efectivos de Windows y respaldos.

Las pruebas de Windows solo corren en Windows (CI windows-latest, una máquina virtual: no es un equipo físico).
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cauce_form_connector import respaldo as R
from cauce_form_connector.operaciones import (INCIERTA, OperacionBloqueada, RegistroOperaciones)

ROOT = Path(__file__).resolve().parents[1]
H = "h_" + "5" * 24


# ------------------------------------------------------------------ ninguna resolución de red desbloquea
@pytest.mark.parametrize("revision", [
    None, {}, {"id": "rv_" + "0" * 12, "confirms_receipt": True, "evidence": "portal_receipt", "folio": "LAB-1"},
    {"confirms_receipt": "true", "evidence": "operator_checked_portal", "estado": "confirmada", "resultado": "no_recibido"},
    {"id": "rv_" + "1" * 12, "note_sha256": "a" * 16, "meaning": "network_or_session_review"},
])
def test_ninguna_revision_de_red_cambia_ni_desbloquea_una_incierta(tmp_path, revision):
    """Ni siquiera una revisión que DIJERA confirmar la recepción, ni repetida, ni de otra sesión."""
    reg = RegistroOperaciones(tmp_path)
    op = reg.iniciar(H, "lab", None, "bs_" + "a" * 12)
    reg.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    reg.marca_vista_en("bs_" + "a" * 12)
    for _ in range(3):
        reg.resuelta_por_operador_ne("bs_" + "a" * 12, revision)
        reg.resuelta_por_operador_ne("bs_" + "b" * 12, revision)
    o = RegistroOperaciones(tmp_path).get(op["id"])
    assert o["estado"] == INCIERTA and o["revision_red"]["confirma_recepcion"] is False
    assert set(o["revision_red"]) <= {"en", "por", "confirma_recepcion", "id", "nota_sha256"}   # nada más se copia
    assert reg.pide_revision()
    with pytest.raises(OperacionBloqueada) as e:
        reg.comprobar(H)
    assert e.value.codigo == "expediente_incierta"


def _llamadas(metodo: str) -> set[str]:
    out = set()
    for py in (ROOT / "cauce_form_connector").rglob("*.py"):
        for n in ast.walk(ast.parse(py.read_text("utf-8"))):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == metodo:
                out.add(py.relative_to(ROOT).as_posix())
    return out


def test_solo_el_puesto_puede_sacar_una_operacion_de_incierta():
    """Mapa de quién llama a cada transición. La red (paquete red/) no llama a ninguna."""
    assert _llamadas("revisar") == {"cauce_form_connector/cli.py"}              # orden del operador en el puesto
    assert _llamadas("confirmar_por_acuse") == {"cauce_form_connector/envio.py"}  # acuse del portal PROPIO (local)
    assert _llamadas("resuelta_por_operador_ne") == {"cauce_form_connector/web/app.py"}  # solo anota
    for metodo in ("revisar", "confirmar_por_acuse", "cerrar", "incorporar", "actualizar"):
        assert not any(p.startswith("cauce_form_connector/red/") for p in _llamadas(metodo)), metodo


# ------------------------------------------------------------------ respaldos: aviso de cifrado y recuperación
@pytest.fixture()
def estado(tmp_path, monkeypatch):
    from cauce_form_connector.settings import Settings
    st = Settings()
    st.data_dir = str(tmp_path / "forms")
    st.profiles_dir = str(tmp_path / "forms" / "perfiles")
    monkeypatch.setenv("CAUCE_FORMS_DATA_DIR", st.data_dir)
    return st


def test_respaldar_avisa_que_contiene_huella_key_y_exige_medio_cifrado(estado, tmp_path, capsys):
    from types import SimpleNamespace
    RegistroOperaciones(estado.data_dir).registrar_envio_humano(H, "lab")
    assert R.orden_estado(SimpleNamespace(accion="respaldar", destino=str(tmp_path / "r")), estado) == 0
    cap = capsys.readouterr()
    assert "huella.key" in cap.err and "cifrado" in cap.err
    assert "cifrado" in json.loads(cap.out)["aviso"]


def test_recuperacion_completa_tras_perder_el_estado(estado, tmp_path):
    reg = RegistroOperaciones(estado.data_dir)
    reg.registrar_envio_humano(H, "lab")
    zipf = R.respaldar(estado, tmp_path / "r")["respaldo"]
    del reg
    import shutil
    shutil.rmtree(estado.data_dir)
    R.restaurar(estado, zipf)
    r2 = RegistroOperaciones(estado.data_dir)
    assert r2.general and r2.general["motivo"] == "restaurado_desde_respaldo"
    r2.revisar("GENERAL", "restauración comprobada en el laboratorio", "GENERAL")
    with pytest.raises(OperacionBloqueada) as e:
        r2.comprobar(H)
    assert e.value.codigo == "expediente_incierta"


# ------------------------------------------------------------------ Windows: permisos efectivos
def _ajenos(ruta: Path) -> str:
    out = subprocess.run(["icacls", str(ruta)], capture_output=True, text=True).stdout
    print(out)
    return out


AJENOS = ("Authenticated Users", "BUILTIN\\Users", "Everyone", "NT AUTHORITY\\Authenticated", "Usuarios")


@pytest.mark.skipif(sys.platform != "win32", reason="ACL de Windows")
def test_windows_el_respaldo_solo_lo_lee_el_usuario_y_la_carpeta_de_destino_no_se_toca(estado, tmp_path):
    RegistroOperaciones(estado.data_dir).registrar_envio_humano(H, "lab")
    destino = tmp_path / "r"
    destino.mkdir()
    antes = subprocess.run(["icacls", str(destino)], capture_output=True, text=True).stdout
    out = R.respaldar(estado, destino)
    assert out["proteccion"] == "acl_usuario"
    acl = _ajenos(Path(out["respaldo"]))
    for a in AJENOS:
        assert a not in acl, acl
    assert "(I)" not in acl                                           # sin herencia de la carpeta elegida
    assert subprocess.run(["icacls", str(destino)], capture_output=True, text=True).stdout == antes


@pytest.mark.skipif(sys.platform != "win32", reason="ACL de Windows")
def test_windows_la_carpeta_restaurada_queda_protegida_y_es_utilizable(estado, tmp_path):
    reg = RegistroOperaciones(estado.data_dir, dueno=True)
    reg.registrar_envio_humano(H, "lab")
    reg.soltar()
    zipf = R.respaldar(estado, tmp_path / "r")["respaldo"]
    import shutil
    shutil.rmtree(estado.data_dir)
    R.restaurar(estado, zipf)
    for nombre in ("huella.key", "operaciones.json"):
        acl = _ajenos(Path(estado.data_dir) / nombre)
        for a in AJENOS:
            assert a not in acl, acl
    r2 = RegistroOperaciones(estado.data_dir, dueno=True)              # el usuario sigue pudiendo usarla
    assert r2.proteccion == "acl_usuario"
    with pytest.raises(OperacionBloqueada):
        r2.comprobar(H)
    r2.soltar()
