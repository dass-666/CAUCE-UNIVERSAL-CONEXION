"""Lab 07: listas hechas con JavaScript, identificador de captura manual y fecha con máscara (datos SINTÉTICOS)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cauce_form_connector import puente_monitor as P

from integ_helpers import LAB, Sitio, Espia, fabrica_espia, settings_para

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config/formularios/lab_listas_personalizadas.json"
EXP = json.loads((ROOT / "sample_data/expediente_monitor_sintetico.json").read_text(encoding="utf-8"))
MANUAL = {"id_cliente": "CL-000123"}


def test_fecha_se_convierte_solo_con_formato_declarado():
    cfg = P.cargar_formulario(CFG, puerto=1)
    valores, faltan = P.extraer_campos(EXP, cfg, MANUAL)
    assert valores["fecha_nacimiento"] == "17/05/1990" and valores["operacion"] == "OP-N" and faltan == []
    _, faltan = P.extraer_campos(EXP, cfg)                     # sin captura manual: queda para la persona
    assert faltan == ["id_cliente"]


@pytest.fixture()
def sitio():
    s = Sitio()
    s.paginas["/07.html"] = (LAB / "07_listas_personalizadas.html").read_text(encoding="utf-8")
    yield s
    s.cerrar()


def _correr(sitio, tmp_path, pagina=None):
    if pagina:
        sitio.paginas["/07.html"] = pagina
    st = settings_para(tmp_path)
    cfg = P.cargar_formulario(CFG, url=sitio.base + "/07.html")
    P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
    return P.autorrellenar(cfg, EXP, st, espera_revision_s=0, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia,
                           manuales=MANUAL)


@pytest.mark.browser
def test_ocho_de_ocho_sin_envio(sitio, tmp_path):
    r = _correr(sitio, tmp_path)
    inf = r["informe"]
    assert len(inf["campos"]) == 8 and all(c["correcto"] for c in inf["campos"]), inf
    assert all(c["coinciden"] for c in inf["confirmaciones"]) and r["respuesta"]["envio"]["realizado_por_conector"] is False
    v = Espia.ultimo.valores
    assert v["operacion_valor"] == "OP-N" and v["tramite_valor"] == "T-REN" and v["nacimiento"] == "17/05/1990"
    assert "CL-000123" not in json.dumps(r)                     # la captura manual no aparece en la salida


@pytest.mark.browser
def test_control_que_no_registra_la_seleccion_se_detecta(sitio, tmp_path):
    roto = (LAB / "07_listas_personalizadas.html").read_text(encoding="utf-8").replace(
        "oculto.value = op.dataset.value;", "")                 # el componente no actualiza su valor interno
    r = _correr(sitio, tmp_path, roto)
    estados = {c["campo"]: c["estado"] for c in r["informe"]["campos"]}
    assert estados["operacion"] == "invalido" and estados["tipo_tramite"] == "invalido"
    assert r["informe"]["estado"] == "preparado_con_incidencias"
