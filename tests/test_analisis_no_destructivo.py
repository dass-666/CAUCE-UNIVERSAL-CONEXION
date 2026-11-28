"""Lab 11: el análisis no debe alterar el formulario. Fase pasiva sin ninguna interacción; fase de clic sin teclas de
selección, con las escrituras al servidor bloqueadas y corte al primer cambio de valor. Diagnóstico con cifras reales y
versión anonimizada por lista blanca, bloqueada si no se puede garantizar."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from cauce_form_connector import analizador_formularios as A
from cauce_form_connector import diagnostico_controles as DC
from cauce_form_connector import puente_monitor as P
from cauce_form_connector.models import FieldDescriptor

from integ_helpers import LAB, Sitio, Espia, fabrica_espia, settings_para

ROOT = Path(__file__).resolve().parents[1]
EXP = json.loads((ROOT / "sample_data/expediente_monitor_sintetico.json").read_text(encoding="utf-8"))


@pytest.fixture
def sitio():
    s = Sitio()
    s.paginas["/11.html"] = (LAB / "11_analisis_no_destructivo.html").read_text(encoding="utf-8")
    s.paginas["/evento"] = ""
    yield s
    s.cerrar()


def _eventos(sitio) -> list[dict]:
    time.sleep(0.3)
    return [{k: v[0] for k, v in parse_qs(urlsplit(r).query).items()} for m, r in sitio.peticiones if r.startswith("/evento")]


@pytest.mark.browser
def test_analisis_pasivo_no_interactua_en_absoluto(tmp_path, sitio):
    inf = A.analizar_url(sitio.base + "/11.html", autorizado=False, settings=settings_para(tmp_path), expediente=EXP)
    assert _eventos(sitio) == []                                  # ni un clic, ni una tecla, ni un foco
    assert not [p for p in sitio.peticiones if p[0] == "POST"]
    seg = inf["seguridad_analisis"]
    assert seg == {"sondeo": "pasivo", "controles_alterados": [], "escrituras_bloqueadas": 0, "sin_comprobar": seg["sin_comprobar"]}
    assert "alta::sucursal_txt" in seg["sin_comprobar"]           # indicios de lista, pero no se pulsa: queda avisado
    assert any("Análisis pasivo" in a for a in inf["advertencias"])
    assert inf["perfil_borrador"]["sondeo"] == "pasivo"
    c = {x["clave"]: x for x in inf["campos"]}
    assert c["alta::cantidad"]["tipo"] == "text" and not any("cantidad" in k and k != "alta::cantidad" for k in c)
    assert inf["perfil_borrador"]["formatos_fecha"] == {"fecha_nacimiento": "DD/MM/AAAA"}      # «d/m/Y» del calendario


@pytest.mark.browser
def test_fase_de_clic_sin_teclas_ni_cambios(tmp_path, sitio):
    inf = A.analizar_url(sitio.base + "/11.html?trampa=0", autorizado=False, settings=settings_para(tmp_path),
                         expediente=EXP, sondeo="clic")
    ev = _eventos(sitio)
    assert {e["k"] for e in ev if e["t"] == "keydown"} <= {"Escape"}          # nunca flechas, Intro ni Tab
    assert not [e for e in ev if e["t"] == "cambio"]                          # ningún valor cambió
    assert not [e for e in ev if e.get("c") in ("cantidad", "mas", "menos", "f_nac")]   # número y fecha: ni se tocan
    assert not [p for p in sitio.peticiones if p[0] == "POST"]                # el guardado automático no salió
    seg = inf["seguridad_analisis"]
    assert seg["controles_alterados"] == [] and seg["escrituras_bloqueadas"] >= 1
    assert any("escritura" in a for a in inf["advertencias"])
    c = {x["clave"]: x for x in inf["campos"]}
    assert [o["etiqueta"] for o in c["alta::sucursal_txt"]["opciones"]] == ["Centro", "Norte", "Sur"]   # por su flecha
    assert [o["etiqueta"] for o in c["alta::zona_txt"]["opciones"]] == ["Oriente", "Poniente"]
    assert c["alta::cantidad"]["tipo"] == "text" and c["alta::referencia"]["tipo"] == "text"

    # El Markdown dice lo mismo que el análisis: mismas listas y mismas cifras de opciones
    rutas = A.guardar(inf, tmp_path / "out")
    d = json.loads(Path(rutas["diagnostico_json"]).read_text(encoding="utf-8"))
    md = Path(rutas["diagnostico_md"]).read_text(encoding="utf-8")
    listas = {k: len(x["opciones"]) for k, x in c.items() if x["tipo"] == "custom-select"}
    assert d["resumen"]["listas"] == len(listas) and d["resumen"]["opciones_en_total"] == sum(listas.values())
    assert f"- Listas: {len(listas)} · con opciones: {sum(1 for n in listas.values() if n)} · opciones en total: {sum(listas.values())}" in md
    for clave, n in listas.items():
        assert re.search(rf"\*\*{re.escape(clave)}\*\* — .*queda como «custom-select» con {n} opciones", md), clave
    assert "0 reconocidos como opciones" not in md and "Sondeos" not in md
    assert {e["clave"]: e["opciones"] for e in d["explicaciones"]} == {k: len(x["opciones"]) for k, x in c.items()}
    assert d["explicaciones"][0]["motivo"].startswith("campo numérico")


@pytest.mark.browser
def test_un_cambio_de_valor_interrumpe_la_fase_activa(tmp_path, sitio):
    inf = A.analizar_url(sitio.base + "/11.html", autorizado=False, settings=settings_para(tmp_path),
                         expediente=EXP, sondeo="clic")
    seg = inf["seguridad_analisis"]
    assert seg["controles_alterados"] == ["alta::turno"]
    assert inf["advertencias"][0].startswith("ATENCIÓN")
    ev = _eventos(sitio)
    assert [e["c"] for e in ev if e["t"] == "cambio"] == ["turno"]
    assert not [e for e in ev if e["t"] == "click" and e["c"] == "zona_txt"]   # lo que venía después ya no se pulsó
    rutas = A.guardar(inf, tmp_path / "out")
    d = json.loads(Path(rutas["diagnostico_json"]).read_text(encoding="utf-8"))
    e = {x["clave"]: x for x in d["explicaciones"]}
    assert e["alta::turno"]["valor_alterado_por_el_sondeo"] is True
    assert "interrumpió" in e["alta::zona_txt"]["motivo"] and e["alta::zona_txt"]["tipo_final"] == "text"
    assert "alta::turno" in Path(rutas["diagnostico_md"]).read_text(encoding="utf-8").split("Valores del formulario alterados:")[1].splitlines()[0]

    # y un perfil no se aprueba sobre una página así
    cfg = {"version": 1, "formulario_id": "frm_lab_trampa", "perfil": "perfil_lab_trampa", "url": sitio.base + "/11.html",
           "campos_formulario": {"turno": "alta::turno", "correo": "alta::mail"},
           "campos_expediente": {"turno": "@manual", "correo": "correo"}, "sondeo": "clic"}
    ruta = tmp_path / "cfg.json"
    ruta.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(P.ConfigInvalida, match="cambió el valor"):
        P.aprobar_perfil(P.cargar_formulario(ruta), settings_para(tmp_path), aprobado_por="rev_prueba")


@pytest.mark.browser
def test_relleno_del_combo_por_su_flecha_sin_tocar_el_numero(tmp_path, sitio):
    cfg = {"version": 1, "formulario_id": "frm_lab_combo", "perfil": "perfil_lab_combo", "url": sitio.base + "/11.html?trampa=0",
           "campos_formulario": {"sucursal": "alta::sucursal_txt", "fecha_nacimiento": "alta::f_nac", "correo": "alta::mail"},
           "campos_expediente": {"sucursal": "sucursal", "fecha_nacimiento": "fecha_nacimiento", "correo": "correo"},
           "formatos_fecha": {"fecha_nacimiento": "DD/MM/AAAA"}, "sondeo": "clic",
           "equivalencias": {"sucursal": {"campo_monitor": "sucursal", "valores": {"S001": "Centro", "S002": "Norte"}}}}
    ruta = tmp_path / "cfg.json"
    ruta.write_text(json.dumps(cfg), encoding="utf-8")
    st = settings_para(tmp_path)
    c = P.cargar_formulario(ruta)
    P.aprobar_perfil(c, st, aprobado_por="rev_prueba")
    assert not [e for e in _eventos(sitio) if e["t"] == "click" and e["c"] in ("zona_txt", "referencia", "turno")]   # solo lo del perfil
    r = P.autorrellenar(c, EXP, st, espera_revision_s=0, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia)
    assert all(x["correcto"] for x in r["informe"]["campos"]), r["informe"]
    v = Espia.ultimo.valores
    assert v["sucursal"] == "S001" and v["sucursal_txt"] == "Centro" and v["cantidad"] == "5" and v["f_nac"] == "17/05/1990"
    assert not [e for e in _eventos(sitio) if e["t"] == "keydown" and e["k"] in ("ArrowDown", "ArrowUp")]


# --------------------------------------------------------------------------------------------- anonimización
DIAG = {
    "formato": DC.FORMATO, "errores": [], "nota": "x", "url": "https://tramites.bancoejemplo.example/citas?id=9",
    "titulo_documento": "Banco Ejemplo · Citas", "idioma": "es-MX",
    "seguridad": {"sondeo": "clic", "controles_alterados": ["frmBancoEjemplo::curpTitular"], "escrituras_bloqueadas": 0, "sin_comprobar": []},
    "explicaciones": [{"clave": "frmBancoEjemplo::curpTitular", "tipo_detectado": "text", "tipo_final": "custom-select", "opciones": 2,
                       "decision": "lista", "modo": "clic", "fase": "activa", "motivo": "al interactuar (clic) aparecen 2 opciones: es una lista",
                       "pasos": [{"accion": "clic", "elementos_nuevos": 4, "opciones": 2, "calendario": False}],
                       "senales": {"clases": ["input.bancoejemplo-combo.x-form-field", "div.sucursalWrapper"], "aria": {"role": "combobox", "aria-owns": "listaSucursalesBancoEjemplo"},
                                   "datos": ["data-bancoejemplo-id", "data-ref"], "eventos": ["jq:focus", "jq:bancoejemploChange"],
                                   "widgets": ["uiAutocomplete", "bancoEjemploWidget"], "fuertes": ["clase de lista en el campo"],
                                   "componente": {"libreria": "extjs", "version": "6.2.0.981", "xtype": "bancoejemplocombo", "editable": True}}}],
    "campos_reconocidos": [{"clave": "frmBancoEjemplo::curpTitular", "tipo": "custom-select", "etiqueta": "Sucursal del titular", "opciones": 2}],
    "marcos": [{"host": "pagos.bancoejemplo.example", "mismo_origen": False, "campos_dentro": None, "visible": True, "titulo": "Pago Banco Ejemplo"}],
    "shadow_dom": [{"etiqueta": "bancoejemplo-selector.principal", "campos_dentro": 1}],
    "librerias": {"jquery": True, "ext_js": True},
    "candidatos": [{"n": 0, "motivo": "elemento pulsable con clase de lista", "sondeable": True, "selector": "#cmbSucursalBancoEjemplo12345678 > div:nth-of-type(2)",
                    "elemento": "div.bancoejemplo-dropdown.open", "atributos": {"role": "button", "data-sucursal": "Centro Histórico", "name": "sucursalBancoEjemplo"},
                    "ancestros": ["div.formularioBancoEjemplo", "form"], "texto": "Elija su sucursal Banco Ejemplo", "anterior": "label.etiquetaBanco",
                    "anterior_texto": "Sucursal", "siguiente": "span.ayudaBancoEjemplo",
                    "esqueleto": '<div.bancoejemplo-dropdown role="button" title="Sucursal Centro" data-sucursal="Centro"><span.valorActual>Centro Histórico</span></div>'}],
}


def test_anonimo_por_lista_blanca_no_deja_nombres_en_clases_ni_ids():
    a = DC.anonimizar(json.loads(json.dumps(DIAG)))
    txt = json.dumps(a, ensure_ascii=False).lower()
    for palabra in ("banco", "ejemplo", "curp", "titular", "sucursal", "centro", "histórico", "tramites", "citas", "pagos",
                    "example", "https", "12345678", "es-mx", "ayuda", "etiquetabanco", "valoractual"):
        assert palabra not in txt, palabra
    assert DC.verificar_anonimo(a) == [] and a["anonimizado"] is True
    # se conserva lo técnico: estructura, roles, librería, cifras y el motivo
    e = a["explicaciones"][0]
    assert e["clave"] == "campo1" and a["seguridad"]["controles_alterados"] == ["campo1"] and a["campos_reconocidos"][0]["clave"] == "campo1"
    assert e["opciones"] == 2 and e["motivo"] == DIAG["explicaciones"][0]["motivo"] and e["senales"]["aria"]["role"] == "combobox"
    assert e["senales"]["componente"]["libreria"] == "extjs" and "x-form-field" in e["senales"]["clases"][0]
    assert re.fullmatch(r"input\.t\d+-combo\.x-form-field", e["senales"]["clases"][0])
    assert a["candidatos"][0]["esqueleto"].startswith("<div.t") and 'role="button"' in a["candidatos"][0]["esqueleto"]
    # el mismo nombre recibe siempre el mismo seudónimo (se conserva la relación entre elementos)
    seud = re.search(r"input\.(t\d+)-combo", e["senales"]["clases"][0]).group(1)
    assert a["candidatos"][0]["elemento"] == f"div.{seud}-dropdown.open"


def test_exportacion_bloqueada_si_queda_una_palabra_ajena(tmp_path):
    malo = json.loads(json.dumps(DIAG))
    malo["explicaciones"][0]["motivo"] = "lista del Banco Ejemplo"          # texto de página donde solo cabe texto propio
    with pytest.raises(DC.ExportacionBloqueada):
        DC.anonimizar(malo)
    inf = {"_diagnostico_controles": malo, "url": "x", "resumen": {}, "perfil_borrador": {"perfil": "p", "_borrador": {}},
           "campos": [], "advertencias": [], "bloqueo_humano": {"blocked": False, "reasons": []},
           "expediente": {"rutas_disponibles": [], "esquema": {}, "nota": ""}}
    try:
        rutas = A.guardar(inf, tmp_path)
    except Exception:
        rutas = None                                                         # el informe mínimo de esta prueba no importa
    assert not (tmp_path / "diagnostico_controles_anonimo.json").exists()
    assert (tmp_path / "diagnostico_controles_anonimo.BLOQUEADO.txt").exists()
    assert rutas is None or "diagnostico_anonimo" not in rutas


# --------------------------------------------------------------------------------------------- fechas
@pytest.mark.parametrize("pista,esperado", [
    ("d/m/Y", "dd/mm/aaaa"), ("Y-m-d", "aaaa-mm-dd"), ("j.n.Y", "dd.mm.aaaa"), ("DD/MM/YYYY", "dd/mm/aaaa"),
    ("d/m/y", ""), ("dd/mm/yy", ""), ("99/99/9999", ""), ("M j, Y", ""),
])
def test_formato_de_fecha_solo_con_evidencia(pista, esperado):
    f = FieldDescriptor(key="k", selector="#k", tag="input", type="text", format_hint=pista)
    assert A._formato_fecha_texto(f) == esperado
