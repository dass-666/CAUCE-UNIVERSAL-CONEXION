"""Puente Monitor -> conector: minimización, perfil de formulario configurable y recorrido completo (datos ficticios)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cauce_form_connector import puente_monitor as P

from integ_helpers import LAB, Sitio, fabrica_espia, settings_para, Espia

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config/formularios/lab_validaciones.json"
EXP = json.loads((ROOT / "sample_data/expediente_ficticio.json").read_text(encoding="utf-8"))


def test_config_invalida_se_rechaza(tmp_path):
    malo = tmp_path / "c.json"
    malo.write_text(json.dumps({"version": 1, "formulario_id": "FRM", "perfil": "p", "url": "http://x",
                                "campos_formulario": {"a": "f::a"}, "campos_expediente": {"b": "x"}}))
    with pytest.raises(P.ConfigInvalida):
        P.cargar_formulario(malo)
    with pytest.raises(P.ConfigInvalida):
        P.cargar_formulario(CFG)          # {puerto} sin resolver


def test_solo_se_extraen_campos_declarados():
    cfg = P.cargar_formulario(CFG, puerto=1)
    valores, faltan = P.extraer_campos(EXP, cfg)
    assert set(valores) == set(cfg["campos_formulario"]) and faltan == []
    blob = json.dumps(valores)
    assert "NO-SE-USA" not in blob and "jamás" not in blob and "Calle Falsa" not in blob
    assert valores["acepta_privacidad"] == "si"


def test_dato_ausente_no_se_inventa():
    cfg = P.cargar_formulario(CFG, puerto=1)
    exp = json.loads(json.dumps(EXP))
    exp["domicilio"]["ciudad"] = "  "
    del exp["contacto"]["telefono"]
    valores, faltan = P.extraer_campos(exp, cfg)
    assert "ciudad" not in valores and set(faltan) == {"ciudad", "telefono", "telefono_confirmacion"}


@pytest.mark.browser
def test_recorrido_completo_sin_envio(tmp_path):
    sitio = Sitio()
    sitio.paginas["/03.html"] = (LAB / "03_validaciones.html").read_text(encoding="utf-8")
    try:
        st = settings_para(tmp_path)
        cfg = P.cargar_formulario(CFG, url=sitio.base + "/03.html")
        assert P.autorrellenar(cfg, EXP, st, state_dir=tmp_path / "e")["salida"] == 6   # sin perfil aprobado: no corre
        P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
        exp = json.loads(json.dumps(EXP))
        del exp["domicilio"]["ciudad"]
        r = P.autorrellenar(cfg, exp, st, espera_revision_s=0, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia)
        inf = r["informe"]
        assert r["respuesta"]["envio"]["realizado_por_conector"] is False and inf["sin_dato"] == ["ciudad"]
        assert inf["campos"] and all(c["correcto"] for c in inf["campos"])
        assert all(c["coinciden"] for c in inf["confirmaciones"])
        assert "Mariana" not in json.dumps(r) and "example.com" not in json.dumps(r)     # sin valores en la salida
        assert "ciudad" not in Espia.ultimo.valores or Espia.ultimo.valores.get("ciudad", "") == ""
        assert "mariana.zz@example.com" == Espia.ultimo.valores["email"]
    finally:
        sitio.cerrar()
