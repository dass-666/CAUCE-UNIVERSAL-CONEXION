"""Lab 09: listas con la estructura de las librerías habituales (select real escondido y sustituido, cuadro de texto con
lista filtrable dibujada al final de la página, identificadores que cambian en cada carga), formato de fecha declarado en
un atributo, diagnóstico de controles y conservación de la configuración ya aprobada. Datos SINTÉTICOS."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cauce_form_connector import analizador_formularios as A
from cauce_form_connector import diagnostico_controles as DC
from cauce_form_connector import puente_monitor as P
from cauce_form_connector.models import FieldDescriptor

from integ_helpers import LAB, Sitio, Espia, fabrica_espia, settings_para

ROOT = Path(__file__).resolve().parents[1]
EXP = json.loads((ROOT / "sample_data/expediente_monitor_sintetico.json").read_text(encoding="utf-8"))
CFG = {
    "version": 1, "formulario_id": "frm_lab_libreria", "perfil": "perfil_lab_libreria", "url": "",
    "campos_formulario": {"operacion": "alta::operacion", "num_cliente": "alta::num_cliente",
                          "fecha_nacimiento": "alta::f_nac", "tipo_tramite": "alta::auto_tramite",
                          "telefono": "alta::tel", "telefono_confirmacion": "alta::tel2",
                          "correo": "alta::mail", "correo_confirmacion": "alta::mail2"},
    "campos_expediente": {"operacion": "operacion", "num_cliente": "@fuente:identificadores",
                          "fecha_nacimiento": "fecha_nacimiento", "tipo_tramite": "tipo_tramite", "telefono": "telefono",
                          "telefono_confirmacion": "telefono", "correo": "correo", "correo_confirmacion": "correo"},
    "formatos_fecha": {"fecha_nacimiento": "DD/MM/AAAA"},
    "equivalencias": {"operacion": {"campo_monitor": "operacion", "valores": {"nueva": "1", "reprogramar": "2"}},
                      "tipo_tramite": {"campo_monitor": "tipo_tramite",
                                       "valores": {"alta": "Alta", "renovacion": "Renovación", "constancia": "Constancia"}}},
}


@pytest.mark.parametrize("texto,esperado", [
    ("dd/mm/aaaa", {"dd/mm/aaaa"}), ("DD-MM-YYYY", {"dd-mm-aaaa"}), ("d/m/Y", {"dd/mm/aaaa"}), ("d/m/y", set()),
    ("%d.%m.%Y", {"dd.mm.aaaa"}), ("dd/MM/yyyy", {"dd/mm/aaaa"}), ("yyyy-mm-dd", {"aaaa-mm-dd"}),
    ("ej. 31/12/2024", {"dd/mm/aaaa"}), ("12/31/2024", {"mm/dd/aaaa"}), ("2024-12-31", {"aaaa-mm-dd"}),
    ("05/06/2024", set()), ("99/99/9999", set()), ("Nombre y apellidos", set()),
])
def test_formatos_de_fecha_declarados(texto, esperado):
    assert A._formatos_en(texto) == esperado


def test_formato_ambiguo_o_contradictorio_no_se_adivina():
    f = lambda **k: FieldDescriptor(key="k", selector="#k", tag="input", type="text", **k)
    assert A._formato_fecha_texto(f(format_hint="dd/mm/yyyy")) == "dd/mm/aaaa"
    assert A._formato_fecha_texto(f(format_hint="mm/dd/yyyy | dd/mm/yyyy")) == ""
    assert A._formato_fecha_texto(f(label="Fecha", format_hint="99/99/9999")) == ""
    # lo más cercano al campo manda: el texto de ayuda del propio campo antes que una pista secundaria
    assert A._formato_fecha_texto(f(placeholder="mm/dd/yyyy", format_hint="ej. 31/12/2024")) == "mm/dd/aaaa"


@pytest.mark.browser
def test_analisis_reconoce_listas_fecha_y_genera_diagnostico(tmp_path):
    st = settings_para(tmp_path)
    inf = A.analizar_url((LAB / "09_listas_de_libreria.html").as_uri(), autorizado=False, settings=st, expediente=EXP, sondeo="clic")
    campos = {c["clave"]: c for c in inf["campos"]}
    assert campos["alta::operacion"]["tipo"] == "custom-select"
    assert [o["valor"] for o in campos["alta::operacion"]["opciones"]] == ["1", "2"]
    assert campos["alta::auto_tramite"]["tipo"] == "custom-select"
    assert [o["etiqueta"] for o in campos["alta::auto_tramite"]["opciones"]] == ["Alta", "Renovación", "Constancia"]
    assert sum(1 for c in inf["campos"] if "operacion" in c["clave"]) == 1      # el select escondido no sale aparte
    assert campos["alta::f_nac"]["decision"] == "automatico" and inf["perfil_borrador"]["formatos_fecha"] == {"fecha_nacimiento": "DD/MM/AAAA"}
    assert campos["alta::f_cita"]["decision"] == "humano" and "calendario" in campos["alta::f_cita"]["motivo"]

    # dos análisis dan las mismas claves aunque los identificadores de la página cambien en cada carga
    otra = A.analizar_url((LAB / "09_listas_de_libreria.html").as_uri(), autorizado=False, settings=st, expediente=EXP, sondeo="clic")
    assert [c["clave"] for c in otra["campos"]] == [c["clave"] for c in inf["campos"]]
    assert otra["perfil_borrador"]["_borrador"]["firma_estructura_analizada"] == inf["perfil_borrador"]["_borrador"]["firma_estructura_analizada"]

    assert A.adjuntar_perfil_previo(inf, dict(CFG, url=inf["perfil_borrador"]["url"])) is True
    assert A.adjuntar_perfil_previo(dict(otra), dict(CFG, url="https://otro.example/form")) is False
    rutas = A.guardar(inf, tmp_path / "out")
    informe = json.loads(Path(rutas["informe_json"]).read_text(encoding="utf-8"))
    assert "_diagnostico_controles" not in informe
    assert informe["perfil_previo"]["equivalencias"]["operacion"]["valores"] == {"nueva": "1", "reprogramar": "2"}
    assert "perfil_previo" in Path(rutas["vista_previa"]).read_text(encoding="utf-8")
    d = json.loads(Path(rutas["diagnostico_json"]).read_text(encoding="utf-8"))
    assert d["formato"] == DC.FORMATO and not d.get("errores")
    assert {e["clave"] for e in d["explicaciones"] if e["opciones"]} >= {"alta::operacion", "alta::auto_tramite"}
    assert not d["candidatos"]
    crudo = Path(rutas["diagnostico_json"]).read_text(encoding="utf-8") + Path(rutas["diagnostico_md"]).read_text(encoding="utf-8")
    for dato in (EXP["correo"], EXP["telefono"], EXP["nombre"] if "nombre" in EXP else EXP["correo"]):
        assert str(dato) not in crudo


@pytest.mark.browser
def test_formulario_completo_con_listas_de_libreria(tmp_path, monkeypatch):
    monkeypatch.setenv("CAUCE_FUENTES_DIR", str(tmp_path))
    shutil.copy(ROOT / "sample_data/fuente_identificadores_sintetica.json", tmp_path / "identificadores.json")
    s = Sitio()
    s.paginas["/09.html"] = (LAB / "09_listas_de_libreria.html").read_text(encoding="utf-8")
    try:
        ruta = tmp_path / "cfg.json"
        ruta.write_text(json.dumps(dict(CFG, url=s.base + "/09.html")), encoding="utf-8")
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
        assert v["operacion"] == "1" and v["f_nac"] == "17/05/1990" and v["num_cliente"] == "CL-000123"
        assert "Renovación" in v.values()           # el cuadro con lista conserva la opción elegida, no texto suelto
        assert v["f_cita"] == ""                    # la fecha de solo lectura no se toca
    finally:
        s.cerrar()
