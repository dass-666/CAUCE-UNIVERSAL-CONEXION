"""Correcciones H1–H4 de la auditoría (v0.1.0 -> v0.2 candidata) y comprobación del contrato vendido de Monitor."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import cauce_form_connector.cli as cli
from cauce_form_connector import contrato_v1 as C
from cauce_form_connector.config_store import ProfileStore, structure_signature
from cauce_form_connector.engine import fill_form, safe_cli_mapping, submit_with_human_confirmation
from cauce_form_connector.html_analyzer import analyze_html
from cauce_form_connector.matcher import match_fields
from cauce_form_connector.page_inspector import BrowserSession
from cauce_form_connector.signature import normalize_label, structure_signature_v2


# ------------------------------------------------------------------ H4: firma v2
HTML_BASE = ('<form id=f><label for=e>Correo</label><input id=e name=email type=email required maxlength=80>'
             '<label for=n>Nombre</label><input id=n name=nombre></form>')


def firma(html):
    return structure_signature_v2(analyze_html(html).all_fields)


def test_h4_cambio_de_etiqueta_correo_a_documento_cambia_la_firma():
    cambiado = HTML_BASE.replace(">Correo<", ">Documento<")
    a, b = analyze_html(HTML_BASE).all_fields, analyze_html(cambiado).all_fields
    assert structure_signature(a) == structure_signature(b)          # la firma v1 NO lo veía (el defecto)
    assert structure_signature_v2(a) != structure_signature_v2(b)    # la v2 sí


@pytest.mark.parametrize("antes,despues", [
    ("required ", ""), ("maxlength=80", "maxlength=40"), ("type=email", "type=text"), ('name=email', 'name=correo'),
    ("<input id=n name=nombre>", "<input id=n name=nombre pattern='[a-z]+'>"),
    ("<input id=n name=nombre>", "<input id=n name=nombre autocomplete=name>"),
])
def test_h4_restricciones_tipo_y_nombre_cambian_la_firma(antes, despues):
    assert antes in HTML_BASE and firma(HTML_BASE) != firma(HTML_BASE.replace(antes, despues))


def test_h4_opciones_cuentan_pero_el_estado_seleccionado_no():
    sel = "<form><select name=p><option value=MX>México<option value=ES>España</select></form>"
    assert firma(sel) != firma(sel.replace("España", "Chile"))
    assert firma(sel) == firma(sel.replace("<option value=MX>", "<option value=MX selected>"))


def test_h4_firma_estable_ante_orden_acentos_y_espacios_y_formato_del_contrato():
    assert normalize_label("  Correo   ELECTRÓNICO: * ") == "correo electronico"
    invertido = ('<form id=f><label for=n>Nombre</label><input id=n name=nombre><label for=e>Correo</label>'
                 '<input id=e name=email type=email required maxlength=80></form>')
    assert firma(HTML_BASE) == firma(invertido)
    assert C._RE_OPACO.match(firma(HTML_BASE)) and firma(HTML_BASE).startswith("v2:") and len(firma(HTML_BASE)) == 19
    assert C.cargar_esquema("solicitud")["properties"]["formulario"]["properties"]["firma_estructura_esperada"]["pattern"] == "^v2:[0-9a-f]{16}$"


def test_h4_perfil_anterior_se_lee_pero_se_avisa(tmp_path):
    a = analyze_html(HTML_BASE)
    ruta = ProfileStore(tmp_path).save("viejo", a, {"f::nombre": "Nombre"})
    d = json.loads(ruta.read_text(encoding="utf-8"))
    d.pop("structure_signature_v2")                                   # perfil escrito por la v0.1.0
    ruta.write_text(json.dumps(d), encoding="utf-8")
    prof = ProfileStore(tmp_path).load("viejo")
    rec = ProfileStore.reconcile(prof, a)
    assert rec["same_structure"] and rec["legacy_signature"] is True
    assert ProfileStore.reconcile(ProfileStore(tmp_path).load("viejo"), a)["mapping"] == {"f::nombre": "Nombre"}
    nuevo = ProfileStore(tmp_path).save("nuevo", a, {})
    assert ProfileStore.reconcile(json.loads(nuevo.read_text(encoding="utf-8")), a)["legacy_signature"] is False


def test_h4_la_interfaz_detecta_ahora_el_cambio_de_etiqueta(tmp_path):
    store = ProfileStore(tmp_path)
    store.save("p", analyze_html(HTML_BASE), {"f::email": "Correo"})
    rec = store.reconcile(store.load("p"), analyze_html(HTML_BASE.replace(">Correo<", ">Documento<")))
    assert rec["same_structure"] is False


# ------------------------------------------------------------------ H1: envío
pytestmark_browser = pytest.mark.browser


def preparar(session, lab_url, rows, nombre="01_tradicional.html"):
    session.open_url(lab_url(nombre))
    a = session.inspect()
    mapping = {m.field_key: m.column for m in match_fields(a.all_fields, list(rows[0]))}
    return fill_form(session, mapping, rows[0], delay_ms=0)


@pytest.mark.browser
def test_h1_no_se_puede_enviar_sin_un_relleno_revisado(session, lab_url):
    session.open_url(lab_url("01_tradicional.html"))
    session.inspect()
    with pytest.raises(PermissionError):
        submit_with_human_confirmation(session, "ENVIAR")
    assert session.page.evaluate("document.body.dataset.enviado") is None


@pytest.mark.browser
def test_h1_la_misma_aprobacion_no_sirve_dos_veces_y_la_frase_es_exacta(session, lab_url, rows):
    preparar(session, lab_url, rows)
    for mala in (" ENVIAR\n", "enviar", "ENVIAR ", ""):
        with pytest.raises(PermissionError):
            submit_with_human_confirmation(session, mala)
    out = submit_with_human_confirmation(session, "ENVIAR")
    assert out["clic_solicitado"] and out["envio_observado"] and out["submitted"]
    assert out["confirmado_recepcion"] is False                       # nunca se afirma la recepción
    with pytest.raises(PermissionError):                              # segunda llamada con la misma aprobación
        submit_with_human_confirmation(session, "ENVIAR")


@pytest.mark.browser
def test_h1_clic_sin_envio_observado_no_se_informa_como_enviado(session, lab_url, rows):
    preparar(session, lab_url, rows)
    session.page.fill("#nombre", "")                                  # campo obligatorio vacío: el navegador bloquea
    out = submit_with_human_confirmation(session, "ENVIAR")
    assert out["clic_solicitado"] is True and out["envio_observado"] is False and out["submitted"] is False
    assert session.page.evaluate("document.body.dataset.enviado") is None


@pytest.mark.browser
def test_h1_un_nuevo_relleno_rearma_la_aprobacion_y_el_modo_integracion_no_envia(session, lab_url, rows):
    preparar(session, lab_url, rows)
    submit_with_human_confirmation(session, "ENVIAR")
    preparar(session, lab_url, rows)                                  # abre la página de nuevo y rellena
    session.integration_mode = True
    try:
        with pytest.raises(PermissionError, match="integración"):
            submit_with_human_confirmation(session, "ENVIAR")
    finally:
        session.integration_mode = False


@pytest.mark.browser
def test_h1_relleno_bloqueado_por_captcha_no_arma_el_envio(session, lab_url, rows):
    preparar(session, lab_url, rows, "04_captcha_simulado.html")
    assert session.submit_armed is False
    with pytest.raises(PermissionError):
        submit_with_human_confirmation(session, "ENVIAR")


# ------------------------------------------------------------------ H2: correspondencias «revisar»




def test_h2_safe_cli_mapping_unidad():
    from cauce_form_connector.models import Mapping
    props = [Mapping("f::a", "A", 0.97, "x", "auto"), Mapping("f::b", "B", 0.6, "x", "revisar"),
             Mapping("f::c", None, 0.0, "x", "sin_datos")]
    assert safe_cli_mapping(props, {}) == ({"f::a": "A"}, ["f::b"])
    assert safe_cli_mapping(props, {"f::b": "B2"}) == ({"f::a": "A", "f::b": "B2"}, [])      # aprobada expresamente
    assert safe_cli_mapping(props, {"f::a": "A"}, use_proposals=False) == ({"f::a": "A"}, [])  # con perfil: solo lo aprobado


def test_h3_huella_del_perfil_es_sha256_de_sus_bytes(tmp_path):
    a = analyze_html(HTML_BASE)
    ruta, digest = ProfileStore(tmp_path).save_integration("p", a, {"nombre": "f::nombre"}, formulario_id="frm_x",
                                                           url="http://127.0.0.1:1/f.html", aprobado_por_ref="rev_x")
    assert digest == hashlib.sha256(ruta.read_bytes()).hexdigest() == ProfileStore.sha256_of_file(ruta)
    with pytest.raises(ValueError):
        ProfileStore(tmp_path).save_integration("q", a, {"x": "f::no_existe"}, formulario_id="frm_x",
                                                url="http://127.0.0.1:1/f.html", aprobado_por_ref="rev_x")


# ------------------------------------------------------------------ contrato vendido de Monitor
def test_contrato_vendido_coincide_con_el_commit_fijado():
    base = Path(C.__file__).parent
    pin = dict(l.split(": ", 1) for l in (base / "PIN.txt").read_text().splitlines())
    assert hashlib.sha256((base / "respuesta.schema.json").read_bytes()).hexdigest() == pin["sha256 respuesta.schema.json"]
    assert hashlib.sha256((base / "solicitud.schema.json").read_bytes()).hexdigest() == pin["sha256 solicitud.schema.json"]
    assert pin["commit"] == "9e7f88d"


def test_contrato_vectores_de_la_huella_de_monitor():
    """Vectores calculados con `monitoreo.conector.contrato.huella_preparacion` de Monitor (commit 9e7f88d)."""
    sid, firma_ = "sol_00112233445566778899aabbccddeeff", "v2:0123456789abcdef"
    campos = {"nombre": "Ana Ejemplo", "email": "ana@example.com"}
    assert C.huella_preparacion(sid, firma_, campos) == "sha256:" + VECTOR_HUELLA


VECTOR_HUELLA = "b9de8b23ab28a089"
