"""Tablas de equivalencias aprobadas: código de Monitor -> valor de opción del formulario (datos SINTÉTICOS)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cauce_form_connector import puente_monitor as P
from cauce_form_connector.config_store import ProfileStore

from integ_helpers import LAB, Sitio, fabrica_espia, settings_para, Espia, solicitud
from cauce_form_connector.integracion import ejecutar

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config/formularios/lab_cita_monitor.json"
EXP = json.loads((ROOT / "sample_data/expediente_monitor_sintetico.json").read_text(encoding="utf-8"))


def _cfg_dict() -> dict:
    return json.loads(CFG.read_text(encoding="utf-8"))


def _escribir(tmp_path, cfg: dict) -> Path:
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return p


# ------------------------------------------------------------------------------- sin navegador
def test_traduce_solo_por_la_tabla():
    cfg = P.cargar_formulario(CFG, puerto=1)
    valores, faltan = P.extraer_campos(EXP, cfg)
    assert valores["tipo_tramite"] == "renovacion" and valores["modo"] == "dia" and valores["sucursal"] == "101"
    assert faltan == []


def test_codigo_sin_fila_no_se_adivina():
    cfg = P.cargar_formulario(CFG, puerto=1)
    exp = dict(EXP, sucursal="S999", modo="POR_HORARIO")
    valores, faltan = P.extraer_campos(exp, cfg)
    assert "sucursal" not in valores and "sucursal" in faltan              # S999 no tiene fila: lo hace la persona
    assert valores["modo"] == "horario"
    assert P.codigos_sin_equivalencia(exp, cfg) == ["sucursal"]


@pytest.mark.parametrize("cambio, mensaje", [
    (lambda c: c.pop("equivalencias"), "no tiene tabla"),                                   # código sin tabla
    (lambda c: c["equivalencias"]["modo"].update(campo_monitor="tipo_tramite"), "inválida"),  # no coincide con la ruta
    (lambda c: c["equivalencias"]["tipo_tramite"]["valores"].update(baja="alta"), "inválida"),  # código inexistente
    (lambda c: c["equivalencias"]["modo"]["valores"].update(POR_DIA=""), "inválida"),          # valor vacío
    (lambda c: c["equivalencias"].update(nombre={"campo_monitor": "nombre", "valores": {"a": "b"}}), "inválida"),
])
def test_tablas_invalidas_se_rechazan(tmp_path, cambio, mensaje):
    cfg = _cfg_dict()
    cambio(cfg)
    with pytest.raises(P.ConfigInvalida, match=mensaje):
        P.cargar_formulario(_escribir(tmp_path, cfg), puerto=1)


def test_catalogo_desconocido_admite_codigos_con_formato_valido(tmp_path):
    cfg = _cfg_dict()
    cfg["equivalencias"]["sucursal"]["valores"] = {"S-001": "101"}
    assert P.cargar_formulario(_escribir(tmp_path, cfg), puerto=1)["equivalencias"]["sucursal"]["valores"] == {"S-001": "101"}
    cfg["equivalencias"]["sucursal"]["valores"] = {"S 001; DROP": "101"}
    with pytest.raises(P.ConfigInvalida):
        P.cargar_formulario(_escribir(tmp_path, cfg), puerto=1)


# ------------------------------------------------------------------------------- navegador
@pytest.fixture()
def sitio():
    s = Sitio()
    s.paginas["/06.html"] = (LAB / "06_cita_monitor.html").read_text(encoding="utf-8")
    yield s
    s.cerrar()


@pytest.mark.browser
def test_recorrido_con_tablas_aprobadas(sitio, tmp_path):
    st = settings_para(tmp_path)
    cfg = P.cargar_formulario(CFG, url=sitio.base + "/06.html")
    info = P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
    assert info["equivalencias"]["tipo_tramite"]["codigos_sin_fila"] == []
    perfil = json.loads(Path(info["archivo"]).read_text(encoding="utf-8"))
    assert perfil["integracion"]["equivalencias"] == cfg["equivalencias"]   # aprobadas con el perfil (cubiertas por la huella)
    r = P.autorrellenar(cfg, EXP, st, espera_revision_s=0, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia)
    inf = r["informe"]
    # espera 0: nadie revisa, así que el contrato responde «revision_caducada» tras rellenar y verificar
    assert r["respuesta"]["contrato"] == "cauce.conector.v1" and inf["estado"] == "revision_caducada", inf
    assert r["respuesta"]["envio"]["realizado_por_conector"] is False
    assert len(inf["campos"]) == 8 and all(c["correcto"] for c in inf["campos"]), inf
    v = Espia.ultimo.valores
    assert v["tipo_tramite"] == "renovacion" and v["oficina"] == "101" and v["nacimiento"] == "1990-05-17"
    assert {c["campo"]: c["correcto"] for c in inf["campos"]}["modo"] is True
    assert v["rfc"] == "" and v["registro_previo"] == ""                  # no declarados: intactos


@pytest.mark.browser
def test_codigo_sin_fila_queda_para_la_persona(sitio, tmp_path):
    st = settings_para(tmp_path)
    cfg = P.cargar_formulario(CFG, url=sitio.base + "/06.html")
    P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
    r = P.autorrellenar(cfg, dict(EXP, sucursal="S777"), st, espera_revision_s=0, state_dir=tmp_path / "e",
                        fabrica_sesion=fabrica_espia)
    assert r["informe"]["sin_equivalencia"] == ["sucursal"]
    assert Espia.ultimo.valores["oficina"] == ""                         # no se eligió ninguna oficina
    assert "S777" not in json.dumps(r)


@pytest.mark.browser
def test_tabla_alterada_tras_aprobar_se_rechaza(sitio, tmp_path):
    st = settings_para(tmp_path)
    cfg = P.cargar_formulario(CFG, url=sitio.base + "/06.html")
    P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
    cfg_mod = _cfg_dict()
    cfg_mod["equivalencias"]["tipo_tramite"]["valores"]["renovacion"] = "alta"     # alguien cambia la tabla después
    cfg2 = P.cargar_formulario(_escribir(tmp_path, cfg_mod), url=sitio.base + "/06.html")
    r = P.autorrellenar(cfg2, EXP, st, espera_revision_s=0, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia)
    assert r["salida"] == 6 and r["informe"]["estado"] == "equivalencias_no_aprobadas"


@pytest.mark.browser
def test_tabla_con_opcion_inexistente_no_se_aprueba(sitio, tmp_path):
    cfg_mod = _cfg_dict()
    cfg_mod["equivalencias"]["sucursal"]["valores"]["S002"] = "999"
    cfg = P.cargar_formulario(_escribir(tmp_path, cfg_mod), url=sitio.base + "/06.html")
    with pytest.raises(P.ConfigInvalida, match="no son opciones"):
        P.aprobar_perfil(cfg, settings_para(tmp_path), aprobado_por="rev_prueba")


@pytest.mark.browser
def test_preparar_rechaza_codigo_sin_traducir(sitio, tmp_path):
    """Si Monitor llama a `preparar` directamente con el código crudo, no llega al formulario."""
    st = settings_para(tmp_path)
    cfg = P.cargar_formulario(CFG, url=sitio.base + "/06.html")
    P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
    info = P.info_perfil(cfg, st)
    datos = {"perfil": cfg["perfil"], "sha": info["perfil_sha256"], "firma": info["firma"],
             "formulario_id": cfg["formulario_id"], "url": cfg["url"]}
    sol = solicitud(datos, valores={"nombre": "Zzx Prueba", "modo": "POR_DIA"})
    resp, salida = ejecutar(json.dumps(sol), settings=st, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia)
    assert resp["estado"] == "rechazado_solicitud_invalida" and [e["codigo"] for e in resp["errores"]] == ["SOLICITUD_MAL_FORMADA"]
    ok = solicitud(datos, valores={"nombre": "Zzx Prueba", "modo": "dia"})
    resp2, _ = ejecutar(json.dumps(ok), settings=st, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia)
    assert resp2["estado"] != "rechazado_solicitud_invalida", resp2
