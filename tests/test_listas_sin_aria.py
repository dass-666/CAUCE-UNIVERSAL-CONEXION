"""Lab 08: listas JavaScript sin ARIA (una crea sus opciones al abrirse), fecha con formato detectado e identificador
desde una fuente local configurada (datos SINTÉTICOS)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cauce_form_connector import puente_monitor as P

from integ_helpers import LAB, Sitio, Espia, fabrica_espia, settings_para

ROOT = Path(__file__).resolve().parents[1]
EXP = json.loads((ROOT / "sample_data/expediente_monitor_sintetico.json").read_text(encoding="utf-8"))
CFG = {
    "version": 1, "formulario_id": "frm_lab_sin_aria", "perfil": "perfil_lab_sin_aria", "url": "",
    "campos_formulario": {"operacion": "alta::lista_operacion", "num_cliente": "alta::num_cliente",
                          "fecha_nacimiento": "alta::f_nac", "tipo_tramite": "alta::lista_tramite",
                          "telefono": "alta::tel", "telefono_confirmacion": "alta::tel2",
                          "correo": "alta::mail", "correo_confirmacion": "alta::mail2"},
    "campos_expediente": {"operacion": "operacion", "num_cliente": "@fuente:identificadores",
                          "fecha_nacimiento": "fecha_nacimiento", "tipo_tramite": "tipo_tramite", "telefono": "telefono",
                          "telefono_confirmacion": "telefono", "correo": "correo", "correo_confirmacion": "correo"},
    "formatos_fecha": {"fecha_nacimiento": "DD/MM/AAAA"},
    "equivalencias": {"operacion": {"campo_monitor": "operacion", "valores": {"nueva": "1", "reprogramar": "2"}},
                      "tipo_tramite": {"campo_monitor": "tipo_tramite",
                                       "valores": {"alta": "A1", "renovacion": "R2", "constancia": "C3"}}},
}


def test_fuente_sin_registro_dice_exactamente_que_falta(tmp_path, monkeypatch):
    monkeypatch.setenv("CAUCE_FUENTES_DIR", str(tmp_path))
    motivos: dict = {}
    _, faltan = P.extraer_campos(EXP, dict(CFG, equivalencias={}), motivos=motivos)
    assert "num_cliente" in faltan and "no existe la fuente" in motivos["num_cliente"]
    shutil.copy(ROOT / "sample_data/fuente_identificadores_sintetica.json", tmp_path / "identificadores.json")
    motivos = {}
    _, faltan = P.extraer_campos(dict(EXP, correo="otro@example.com"), dict(CFG, equivalencias={}), motivos=motivos)
    assert "no tiene registro" in motivos["num_cliente"] and "otro@" not in motivos["num_cliente"]


@pytest.mark.browser
def test_formulario_completo_y_reutilizable(tmp_path, monkeypatch):
    monkeypatch.setenv("CAUCE_FUENTES_DIR", str(tmp_path))
    shutil.copy(ROOT / "sample_data/fuente_identificadores_sintetica.json", tmp_path / "identificadores.json")
    s = Sitio()
    s.paginas["/08.html"] = (LAB / "08_listas_sin_aria.html").read_text(encoding="utf-8")
    try:
        ruta = tmp_path / "cfg.json"
        ruta.write_text(json.dumps(dict(CFG, url=s.base + "/08.html")), encoding="utf-8")
        st = settings_para(tmp_path)
        cfg = P.cargar_formulario(ruta)
        P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
        for vuelta in range(2):                     # la segunda vez reutiliza el perfil aprobado, sin aprobar de nuevo
            r = P.autorrellenar(cfg, EXP, st, espera_revision_s=0, state_dir=tmp_path / f"e{vuelta}",
                                fabrica_sesion=fabrica_espia)
            inf = r["informe"]
            assert len(inf["campos"]) == 8 and all(c["correcto"] for c in inf["campos"]), inf
            assert r["respuesta"]["envio"]["realizado_por_conector"] is False
        v = Espia.ultimo.valores
        assert v["operacion"] == "1" and v["tramite"] == "R2" and v["f_nac"] == "17/05/1990" and v["num_cliente"] == "CL-000123"
        assert "CL-000123" not in json.dumps(r)
    finally:
        s.cerrar()
