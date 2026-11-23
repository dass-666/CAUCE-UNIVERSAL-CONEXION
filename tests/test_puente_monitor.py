"""Puente Monitor -> conector: minimización, perfil de formulario configurable y recorrido completo (datos ficticios)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cauce_form_connector import puente_monitor as P

from integ_helpers import LAB, Sitio, fabrica_espia, settings_para, Espia

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config/formularios/lab_validaciones.json"
EXP = json.loads((ROOT / "sample_data/expediente_monitor_sintetico.json").read_text(encoding="utf-8"))


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
    assert set(cfg["campos_expediente"].values()) == {"nombre", "correo", "telefono"}   # rutas planas de Monitor
    valores, faltan = P.extraer_campos(EXP, cfg)
    assert set(valores) == set(cfg["campos_formulario"]) and faltan == []
    blob = json.dumps(valores)
    assert "renovacion" not in blob and "S001" not in blob and "1990-05-17" not in blob   # no declarados: no salen


def test_dato_ausente_no_se_inventa():
    cfg = P.cargar_formulario(CFG, puerto=1)
    exp = json.loads(json.dumps(EXP))
    exp["nombre"] = "  "
    del exp["telefono"]
    valores, faltan = P.extraer_campos(exp, cfg)
    assert "nombre" not in valores and set(faltan) == {"nombre", "telefono", "telefono_confirmacion"}


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
        exp["telefono"] = None
        r = P.autorrellenar(cfg, exp, st, espera_revision_s=0, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia)
        inf = r["informe"]
        assert r["respuesta"]["envio"]["realizado_por_conector"] is False
        assert inf["sin_dato"] == ["telefono", "telefono_confirmacion"]
        assert len(inf["campos"]) == 3 and all(c["correcto"] for c in inf["campos"])
        assert all(c["coinciden"] for c in inf["confirmaciones"])
        assert "Zzmariana" not in json.dumps(r) and "example.com" not in json.dumps(r)     # sin valores en la salida
        assert Espia.ultimo.valores["telefono"] == "" and Espia.ultimo.valores["ciudad"] == ""   # no se inventan
        assert "zz.mariana@example.com" == Espia.ultimo.valores["email"]
    finally:
        sitio.cerrar()
