"""El autorrellenado dice sin ambigüedad si quedó completo: cuenta de campos verificados, lista de pendientes con su
motivo y código de salida propio (8) cuando el contrato terminó en 0 pero falta algún campo. Datos SINTÉTICOS (lab 12)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cauce_form_connector import lanzador as LZ
from cauce_form_connector import puente_monitor as P

from integ_helpers import LAB, Sitio, fabrica_espia, fabrica_prohibida, settings_para

ROOT = Path(__file__).resolve().parents[1]
EXP = json.loads((ROOT / "sample_data/expediente_monitor_sintetico.json").read_text(encoding="utf-8"))
CFG = ROOT / "config/formularios/lab_demo_ocho_campos.json"


def persona_cierra(s):
    s.page.wait_for_timeout(200)
    s.page.close()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv("CAUCE_FUENTES_DIR", str(tmp_path))
    shutil.copy(ROOT / "sample_data/fuente_identificadores_sintetica.json", tmp_path / "identificadores.json")
    s = Sitio()
    s.paginas["/12_demo_ocho_campos.html"] = (LAB / "12_demo_ocho_campos.html").read_text(encoding="utf-8")
    yield s, tmp_path
    s.cerrar()


def _correr(sitio, tmp, exp=EXP, editar=None, n="e"):
    cfg = P.cargar_formulario(CFG, puerto=sitio.port)
    if editar:
        editar(cfg)
    st = settings_para(tmp)
    P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
    r = P.autorrellenar(cfg, exp, st, espera_revision_s=20, state_dir=tmp / n, fabrica_sesion=fabrica_espia,
                        gancho_revision=persona_cierra)
    return r, P.texto_informe(r["informe"])


@pytest.mark.browser
def test_completo_es_cero_y_lo_dice(demo):
    r, texto = _correr(*demo)
    res = r["informe"]["resultado"]
    assert (r["salida"], r["salida_contrato"]) == (0, 0)
    assert res == {"total": 8, "verificados": 8, "pendientes": [], "completo": True}
    assert "RESULTADO: COMPLETO. 8 de 8 campos rellenados y verificados." in texto
    assert LZ.linea_resultado(texto, 0).startswith("✔ COMPLETO. 8 de 8")


@pytest.mark.browser
def test_dato_faltante_es_parcial(demo):
    r, texto = _correr(*demo, exp={**EXP, "telefono": None})
    res = r["informe"]["resultado"]
    assert (r["salida"], r["salida_contrato"]) == (P.SALIDA_PARCIAL, 0)        # antes: 0, igual que un éxito
    assert r["respuesta"]["estado"] == "preparado_revisado_sin_envio"           # el contrato con Monitor no cambia
    assert (res["total"], res["verificados"], res["completo"]) == (8, 6, False)
    assert [p["campo"] for p in res["pendientes"]] == ["telefono", "telefono_confirmacion"]
    assert "RESULTADO: INCOMPLETO. 6 de 8" in texto and "quedan 2 pendientes" in texto
    linea = LZ.linea_resultado(texto, r["salida"])
    assert linea.startswith("✘ INCOMPLETO. 6 de 8") and "telefono" in linea and "telefono_confirmacion" in linea
    assert "INCOMPLETO" in LZ.SALIDAS[P.SALIDA_PARCIAL] and LZ.SALIDAS[0] == "Terminado."


@pytest.mark.browser
def test_equivalencia_ausente_es_parcial(demo):
    def sin_fila(cfg):
        del cfg["equivalencias"]["tipo_tramite"]["valores"]["renovacion"]       # el expediente trae «renovacion»
    r, texto = _correr(*demo, editar=sin_fila)
    res = r["informe"]["resultado"]
    assert r["salida"] == P.SALIDA_PARCIAL and res["verificados"] == 7
    assert [(p["campo"], "equivalencias" in p["motivo"]) for p in res["pendientes"]] == [("tipo_tramite", True)]
    assert "queda 1 pendiente" in texto


@pytest.mark.browser
def test_identificador_ausente_es_parcial(demo):
    sitio, tmp = demo
    (tmp / "identificadores.json").unlink()                                     # la fuente no está: nada se inventa
    r, texto = _correr(sitio, tmp)
    res = r["informe"]["resultado"]
    assert r["salida"] == P.SALIDA_PARCIAL and res["verificados"] == 7
    assert res["pendientes"][0]["campo"] == "num_cliente" and "fuente" in res["pendientes"][0]["motivo"]
    assert "CL-000123" not in json.dumps(r)


@pytest.mark.browser
def test_campo_no_verificado_conserva_el_codigo_2(demo):
    def opcion_inexistente(cfg):
        cfg["equivalencias"]["tipo_tramite"]["valores"]["renovacion"] = "Renovación exprés"
    r, texto = _correr(*demo, editar=opcion_inexistente)
    res = r["informe"]["resultado"]
    assert (r["salida"], r["salida_contrato"]) == (2, 2)                        # compatibilidad: sigue siendo 2
    assert res["verificados"] == 7 and res["pendientes"][0]["campo"] == "tipo_tramite"
    assert "no quedó verificado" in res["pendientes"][0]["motivo"] and "RESULTADO: INCOMPLETO. 7 de 8" in texto


def test_sin_perfil_aprobado_no_es_exito(tmp_path):
    cfg = P.cargar_formulario(CFG, puerto=1)
    r = P.autorrellenar(cfg, EXP, settings_para(tmp_path), state_dir=tmp_path / "e", fabrica_sesion=fabrica_prohibida)
    res = r["informe"]["resultado"]
    assert r["salida"] == 6 and (res["total"], res["verificados"], res["completo"]) == (8, 0, False)
    assert "RESULTADO: INCOMPLETO. 0 de 8" in P.texto_informe(r["informe"])


def test_linea_de_la_ventana_sin_informe():
    assert LZ.linea_resultado("Error de configuración: algo\n", 6) == ""
