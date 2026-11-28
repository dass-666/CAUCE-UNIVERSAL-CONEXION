"""Lab 12: demostración integral de ocho campos con escritura carácter por carácter. Listas dinámicas sobre cuadros de
texto (una con valor por defecto, otra que busca con retardo), fecha con calendario, identificador de una fuente de
prueba y confirmaciones. Se comprueba lo que REGISTRÓ el formulario y los eventos que recibió. Datos SINTÉTICOS."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cauce_form_connector import puente_monitor as P

from integ_helpers import LAB, Sitio, Espia, fabrica_espia, settings_para

ROOT = Path(__file__).resolve().parents[1]
EXP = json.loads((ROOT / "sample_data/expediente_monitor_sintetico.json").read_text(encoding="utf-8"))
CFG = ROOT / "config/formularios/lab_demo_ocho_campos.json"


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv("CAUCE_FUENTES_DIR", str(tmp_path))
    shutil.copy(ROOT / "sample_data/fuente_identificadores_sintetica.json", tmp_path / "identificadores.json")
    s = Sitio()
    s.paginas["/12_demo_ocho_campos.html"] = (LAB / "12_demo_ocho_campos.html").read_text(encoding="utf-8")
    yield s, tmp_path
    s.cerrar()


def _preparar(sitio, tmp_path, **cambios):
    cfg = P.cargar_formulario(CFG, puerto=sitio.port)
    cfg.update(cambios)
    st = settings_para(tmp_path)
    P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
    return cfg, st


@pytest.mark.browser
def test_ocho_de_ocho_escribiendo_y_verificando(demo):
    sitio, tmp = demo
    cfg, st = _preparar(sitio, tmp)
    for vuelta in range(2):                                   # dos veces seguidas con el mismo perfil aprobado
        r = P.autorrellenar(cfg, EXP, st, espera_revision_s=0, state_dir=tmp / f"e{vuelta}", fabrica_sesion=fabrica_espia)
        campos = r["informe"]["campos"]
        assert len(campos) == 8 and all(c["correcto"] for c in campos), r["informe"]
        assert r["respuesta"]["envio"]["realizado_por_conector"] is False
        v, t = Espia.ultimo.valores, Espia.ultimo.teclas
        # lo que registró el formulario: códigos en los campos ocultos, no solo el texto visible
        assert v["operacion"] == "1" and v["operacion_txt"] == "Nueva cita"        # sustituyó el valor por defecto
        assert v["tramite"] == "R2" and v["tramite_txt"] == "Renovación"           # pese a la búsqueda con retardo
        assert v["f_nac"] == "17/05/1990" and v["num_cliente"] == "CL-000123"       # fecha convertida, id de la fuente
        assert v["tel"] == v["tel2"] == EXP["telefono"] and v["mail"] == v["mail2"] == EXP["correo"]
        # las listas se rellenaron TECLEANDO en el cuadro (eventos de teclado reales), no asignando el valor
        assert t.get("operacion_txt") and t.get("tramite_txt"), t
    assert "CL-000123" not in json.dumps(r) and EXP["correo"] not in json.dumps(r["respuesta"])


@pytest.mark.browser
def test_respeta_las_opciones_disponibles(demo):
    sitio, tmp = demo
    cfg = P.cargar_formulario(CFG, puerto=sitio.port)
    cfg["equivalencias"]["tipo_tramite"]["valores"]["renovacion"] = "Renovación exprés"     # no existe en la lista
    st = settings_para(tmp)
    P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
    r = P.autorrellenar(cfg, EXP, st, espera_revision_s=0, state_dir=tmp / "e", fabrica_sesion=fabrica_espia)
    campos = {c["campo"]: c for c in r["informe"]["campos"]}
    assert campos["tipo_tramite"]["correcto"] is False                 # no se elige «la más parecida»
    assert all(c["correcto"] for k, c in campos.items() if k != "tipo_tramite")
    assert Espia.ultimo.valores["tramite"] == ""
