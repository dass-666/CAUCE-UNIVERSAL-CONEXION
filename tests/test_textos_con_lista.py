"""Lab 10: cuadros de texto que abren listas (lo que hacen los componentes de autocompletar de jQuery y similares: para la
página son `input type=text`). Sondeo con interacción ordinaria, explicación por control, lista que solo responde al
escribir, verificación del valor registrado y diagnóstico anonimizado. Datos SINTÉTICOS."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cauce_form_connector import analizador_formularios as A
from cauce_form_connector import diagnostico_controles as DC
from cauce_form_connector import puente_monitor as P

from integ_helpers import LAB, Sitio, Espia, fabrica_espia, settings_para

ROOT = Path(__file__).resolve().parents[1]
EXP = json.loads((ROOT / "sample_data/expediente_monitor_sintetico.json").read_text(encoding="utf-8"))
CFG = {
    "version": 1, "formulario_id": "frm_lab_textos", "perfil": "perfil_lab_textos", "url": "",
    "campos_formulario": {"operacion": "alta::operacion_txt", "num_cliente": "alta::num_cliente",
                          "fecha_nacimiento": "alta::f_nac", "tipo_tramite": "alta::tramite_txt", "modo": "alta::modo_txt",
                          "telefono": "alta::tel", "telefono_confirmacion": "alta::tel2",
                          "correo": "alta::mail", "correo_confirmacion": "alta::mail2"},
    "campos_expediente": {"operacion": "operacion", "num_cliente": "@fuente:identificadores",
                          "fecha_nacimiento": "fecha_nacimiento", "tipo_tramite": "tipo_tramite", "modo": "modo",
                          "telefono": "telefono", "telefono_confirmacion": "telefono",
                          "correo": "correo", "correo_confirmacion": "correo"},
    "formatos_fecha": {"fecha_nacimiento": "DD/MM/AAAA"},
    "equivalencias": {
        "operacion": {"campo_monitor": "operacion", "valores": {"nueva": "Nueva cita", "reprogramar": "Reprogramar cita"}},
        # lista que solo responde al escribir: se anota el texto exacto de la opción
        "tipo_tramite": {"campo_monitor": "tipo_tramite",
                         "valores": {"alta": "Alta", "renovacion": "Renovación", "constancia": "Constancia"}},
        "modo": {"campo_monitor": "modo", "valores": {"POR_DIA": "Por día", "POR_HORARIO": "Por horario"}}},
}


@pytest.mark.browser
def test_analisis_explica_cada_control_y_anonimiza(tmp_path):
    st = settings_para(tmp_path)
    inf = A.analizar_url((LAB / "10_textos_con_lista.html").as_uri(), autorizado=False, settings=st, expediente=EXP, sondeo="clic")
    c = {x["clave"]: x for x in inf["campos"]}
    assert c["alta::operacion_txt"]["tipo"] == "custom-select"
    assert [o["etiqueta"] for o in c["alta::operacion_txt"]["opciones"]] == ["Nueva cita", "Reprogramar cita"]
    assert c["alta::modo_txt"]["tipo"] == "custom-select" and len(c["alta::modo_txt"]["opciones"]) == 2   # solo lectura con menú
    assert c["alta::tramite_txt"]["tipo"] == "custom-select" and c["alta::tramite_txt"]["opciones"] == []  # solo al escribir
    assert c["alta::nombre"]["tipo"] == "text"                    # sugiere, pero es un dato libre: sigue siendo texto
    assert c["alta::f_nac"]["tipo"] == "text" and c["alta::f_nac"]["decision"] == "automatico"
    assert inf["perfil_borrador"]["formatos_fecha"] == {"fecha_nacimiento": "DD/MM/AAAA"}

    rutas = A.guardar(inf, tmp_path / "out")
    d = json.loads(Path(rutas["diagnostico_json"]).read_text(encoding="utf-8"))
    e = {x["clave"]: x for x in d["explicaciones"]}
    assert set(e) == set(c)                                       # hay explicación para TODOS los controles
    assert all(x.get("motivo") for x in e.values())
    assert e["alta::operacion_txt"]["decision"] == "lista" and e["alta::operacion_txt"]["modo"] == "clic"
    assert e["alta::tramite_txt"]["modo"] == "escribir" and "al escribir" in e["alta::tramite_txt"]["motivo"]
    assert "dato libre" in e["alta::nombre"]["motivo"]
    assert "fecha" in e["alta::f_nac"]["motivo"] and e["alta::f_nac"]["pasos"] == []      # no se abre el calendario
    assert "tel" in e["alta::tel"]["motivo"]
    assert not any(x.get("valor_alterado_por_el_sondeo") for x in e.values())             # el sondeo no escribe nada
    md = Path(rutas["diagnostico_md"]).read_text(encoding="utf-8")
    assert "Por qué cada control" in md and "alta::tramite_txt" in md

    anon = Path(rutas["diagnostico_anonimo"]).read_text(encoding="utf-8")
    for texto in ("Reprogramar", "Renovación", "Operación", "Laboratorio", "10_textos_con_lista", "file:", "Ana Ejemplo"):
        assert texto not in anon, texto
    a = json.loads(anon)
    assert a["anonimizado"] is True and "url" not in a and len(a["explicaciones"]) == len(d["explicaciones"])


@pytest.fixture
def sitio(tmp_path, monkeypatch):
    monkeypatch.setenv("CAUCE_FUENTES_DIR", str(tmp_path))
    shutil.copy(ROOT / "sample_data/fuente_identificadores_sintetica.json", tmp_path / "identificadores.json")
    s = Sitio()
    s.paginas["/10.html"] = (LAB / "10_textos_con_lista.html").read_text(encoding="utf-8")
    yield s
    s.cerrar()


def _preparar(tmp_path, sitio, cfg):
    ruta = tmp_path / "cfg.json"
    ruta.write_text(json.dumps(dict(cfg, url=sitio.base + "/10.html")), encoding="utf-8")
    st = settings_para(tmp_path)
    c = P.cargar_formulario(ruta)
    P.aprobar_perfil(c, st, aprobado_por="rev_prueba")
    return c, st


@pytest.mark.browser
def test_formulario_completo_con_textos_que_abren_listas(tmp_path, sitio):
    cfg, st = _preparar(tmp_path, sitio, CFG)
    for vuelta in range(2):                     # la segunda vez reutiliza el perfil aprobado, sin volver a sondear
        r = P.autorrellenar(cfg, EXP, st, espera_revision_s=0, state_dir=tmp_path / f"e{vuelta}",
                            fabrica_sesion=fabrica_espia)
        inf = r["informe"]
        assert len(inf["campos"]) == 9 and all(x["correcto"] for x in inf["campos"]), inf
        assert r["respuesta"]["envio"]["realizado_por_conector"] is False
    v = Espia.ultimo.valores
    # lo que REGISTRÓ el formulario (campos ocultos), no solo el texto visible
    assert v["operacion"] == "1" and v["tramite"] == "R2" and v["modo"] in ("D", "H")
    assert v["operacion_txt"] == "Nueva cita" and v["tramite_txt"] == "Renovación"
    assert v["f_nac"] == "17/05/1990" and v["num_cliente"] == "CL-000123" and v["nombre"] == ""


@pytest.mark.browser
def test_texto_que_no_es_opcion_queda_como_incidencia(tmp_path, sitio):
    cfg = json.loads(json.dumps(CFG))
    cfg["equivalencias"]["tipo_tramite"]["valores"]["renovacion"] = "Renovación total"      # no existe en la lista
    cfg, st = _preparar(tmp_path, sitio, cfg)
    r = P.autorrellenar(cfg, EXP, st, espera_revision_s=0, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia)
    campos = {x["campo"]: x for x in r["informe"]["campos"]}
    assert campos["tipo_tramite"]["correcto"] is False
    assert all(x["correcto"] for k, x in campos.items() if k != "tipo_tramite")             # el resto sí se completa
    assert Espia.ultimo.valores["tramite"] == ""                                            # no se eligió nada parecido
