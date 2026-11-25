"""Combos editables (cuadro de texto + lista): emulación de Ext JS 3.4 (DOM y comportamiento de ComboBox), combo ARIA y
protecciones. Datos ficticios. No se probó con la biblioteca Ext JS real (sin red ni copia local en el entorno de desarrollo): ver docs/LIMITACIONES.md."""
from __future__ import annotations

import pytest

from cauce_form_connector.combo import discover_catalogs, exact_matches, protected_reason
from cauce_form_connector.engine import fill_form
from cauce_form_connector.models import FieldDescriptor
from cauce_form_connector.page_inspector import BrowserSession

from integ_helpers import LAB, Sitio, settings_para

pytestmark = pytest.mark.browser
HTML = (LAB / "05_combos_extjs.html").read_text(encoding="utf-8")
F = "ext_form::"


@pytest.fixture(scope="module")
def sitio():
    s = Sitio()
    s.paginas["/c.html"] = HTML
    yield s
    s.cerrar()


@pytest.fixture()
def sesion(tmp_path, sitio):
    st = settings_para(tmp_path)
    s = BrowserSession(st, headless=True)
    s.start()
    yield s
    s.close()


def abrir(s, sitio, query=""):
    s.open_url(sitio.base + "/c.html" + query)
    return {f.key: f for f in s.inspect().all_fields}


def llenar(s, datos: dict[str, str], **kw):
    mapping = {F + k: k for k in datos}
    return fill_form(s, mapping, dict(datos), delay_ms=0, allow_late_fields=False, **kw)


def res(rep, campo):
    return next(r for r in rep.results if r.field_key == F + campo)


def hidden(s, nombre):
    return s.page.locator(f"#{nombre}").input_value()


def test_deteccion_de_combos_sin_reglas_por_url(sesion, sitio):
    campos = abrir(sesion, sitio)
    assert {k for k, f in campos.items() if f.widget == "combo"} == {F + "pais", F + "estado", F + "municipio", F + "region"}
    pais = campos[F + "pais"]
    assert pais.value_selector == "#pais" and pais.trigger_selector and "ext-comp" not in pais.selector   # sin id autogenerado
    assert campos[F + "municipio"].value_selector == ""                                                  # ARIA sin campo oculto
    assert campos[F + "region"].readonly and not campos[F + "region"].fillable                            # combo no editable = bloqueado
    assert F + "pais#2" not in campos and not [k for k in campos if "pais" in k and k != F + "pais"]     # el oculto no es un campo aparte


@pytest.mark.parametrize("query", ["", "?remote=1"])
def test_ocho_campos_con_datos_de_monitor_sin_equivalencias(sesion, sitio, query):
    abrir(sesion, sitio, query)
    datos = {"nombre": "Mariana Zzejemplo", "correo": "zz@example.com", "pais": "México", "estado": "Jalisco",
             "municipio": "Guadalajara", "tipo": "alta", "cp": "44100", "notas": "Prueba ficticia"}
    rep = llenar(sesion, datos)
    assert rep.ok, [(r.field_key, r.status, r.detail) for r in rep.results]
    assert len(rep.results) == 8 and all(r.status == "ok" for r in rep.results)
    # Valor INTERNO verificado, no solo el texto visible
    assert hidden(sesion, "pais") == "MX" and hidden(sesion, "estado") == "JAL"
    assert res(rep, "pais").selection == "opcion" and res(rep, "pais").internal == "MX"
    assert res(rep, "pais").keystrokes == len("México") and res(rep, "pais").method == "combo_opcion"   # tecleo secuencial + clic en la opción
    # «México (antiguo)» también empieza igual, pero solo una opción es exacta
    assert sesion.page.locator("#municipio").input_value() == "Guadalajara"
    assert sesion.page.locator("#resultado").inner_text() == ""                                      # nunca se envía


def test_texto_libre_se_conserva_o_se_marca_pendiente(sesion, sitio):
    abrir(sesion, sitio)
    rep = llenar(sesion, {"municipio": "Colonia Nueva", "estado": "Atlántida"})
    mun, est = res(rep, "municipio"), res(rep, "estado")
    assert mun.status == "ok" and mun.selection == "texto_libre" and mun.options_seen == []   # sin lista: texto libre conservado
    assert est.status == "ok" and est.selection == "texto_libre" and hidden(sesion, "estado") == "Atlántida"   # forceSelection=false lo admite


def test_forceselection_con_dato_invalido_queda_pendiente(sesion, sitio):
    abrir(sesion, sitio)
    rep = llenar(sesion, {"pais": "Atlántida"})
    r = res(rep, "pais")
    assert r.status == "pendiente_revision" and not rep.ok
    assert sesion.page.locator("#pais").input_value() == ""          # el control no registró nada: no se declara éxito


def test_opciones_sin_coincidencia_exacta_quedan_pendientes_con_catalogo(sesion, sitio):
    abrir(sesion, sitio)
    rep = llenar(sesion, {"pais": "Mé", "estado": "Jal"})
    for campo in ("pais", "estado"):
        r = res(rep, campo)
        assert r.status == "pendiente_revision" and r.pending and r.options_seen, campo
    assert "México" in res(rep, "pais").options_seen and "Jalisco" in res(rep, "estado").options_seen
    assert hidden(sesion, "pais") == ""                              # no se adivinó ninguna opción
    assert not rep.ok


def test_equivalencia_aprobada_resuelve_el_dato(sesion, sitio):
    campos = abrir(sesion, sitio)
    rep = llenar(sesion, {"estado": "DF"}, equivalences={F + "estado": {"df": "Ciudad de México"}})
    r = res(rep, "estado")
    assert r.status == "ok" and r.equivalence_used and r.expected == "Ciudad de México"
    assert hidden(sesion, "estado") == "CDMX" and campos[F + "estado"].widget == "combo"


def test_typeahead_no_engana_a_la_verificacion(sesion, sitio):
    abrir(sesion, sitio, "?typeahead=1")
    rep = llenar(sesion, {"estado": "Nuevo León", "pais": "España"})
    assert rep.ok and hidden(sesion, "estado") == "NL" and hidden(sesion, "pais") == "ES"


def test_catalogo_se_descubre_sin_escribir(sesion, sitio):
    campos = abrir(sesion, sitio)
    cat = discover_catalogs(sesion, list(campos.values()))
    assert cat[F + "pais"] == ["México", "Estados Unidos", "España", "Colombia", "México (antiguo)"]
    assert cat[F + "estado"] == ["Jalisco", "Nuevo León", "Yucatán", "Ciudad de México"]
    assert F + "region" not in cat                                    # bloqueado: no se abre
    assert hidden(sesion, "pais") == "" and sesion.page.locator("#nombre").input_value() == ""
    assert sesion.page.locator("#resultado").inner_text() == ""


def test_protecciones_autenticacion_consentimiento_oculto_y_bloqueado(sesion, sitio):
    campos = abrir(sesion, sitio)
    rep = fill_form(sesion, {F + "clave": "clave", F + "nombre": "nombre", F + "region": "region", F + "acepta": "acepta"},
                    {"clave": "zz-secreto", "nombre": "Zzprueba", "region": "Norte", "acepta": "si"}, delay_ms=0,
                    allow_late_fields=False)
    assert sesion.page.locator("#clave").input_value() == ""         # autenticación: nunca
    assert "autenticación" in res(rep, "clave").detail and res(rep, "clave").status == "omitido"
    assert sesion.page.locator("#nombre").input_value() == "Zzprueba"
    assert hidden(sesion, "region") == ""                              # combo no editable: no se toca
    assert sesion.page.locator('input[name="token_csrf"]').input_value() == "zz-no-tocar"   # oculto intacto
    # Consentimiento: solo con correspondencia aprobada (aquí sí la había); la estrategia automática no lo marca
    assert protected_reason(campos[F + "acepta"], automatic=True) and not protected_reason(campos[F + "acepta"])
    assert sesion.page.locator("#resultado").inner_text() == ""


def test_exact_matches_y_proteccion_unitaria():
    ops = [{"label": "México", "value": ""}, {"label": "México (antiguo)", "value": ""}, {"label": "Mexico", "value": ""}]
    assert len(exact_matches(ops, "méxico")) == 2 and exact_matches(ops, "Mé") == []
    f = FieldDescriptor(key="a::otp", selector="#otp", tag="input", type="text", name="codigo_otp")
    assert protected_reason(f)


def test_preparar_con_combos_usa_valores_de_monitor_y_equivalencias_del_perfil(tmp_path, sitio):
    """Contrato v1 de punta a punta: sin envío, combos tecleados, equivalencia aprobada guardada en el perfil."""
    import json

    from cauce_form_connector import integracion as I
    from cauce_form_connector.config_store import ProfileStore
    from cauce_form_connector.signature import structure_signature_v2
    from integ_helpers import fabrica_espia, solicitud
    st = settings_para(tmp_path)
    url = sitio.base + "/c.html"
    with BrowserSession(st, headless=True) as s:
        s.open_url(url)
        an = s.inspect()
    campos = {"nombre": F + "nombre", "pais": F + "pais", "estado": F + "estado", "municipio": F + "municipio"}
    ruta, sha = ProfileStore(st.profiles_dir).save_integration(
        "perfil_combos", an, campos, formulario_id="frm_combos", url=url, aprobado_por_ref="rev_lab",
        equivalencias={F + "estado": {"DF": "Ciudad de México"}})
    info = {"perfil": "perfil_combos", "sha": sha, "firma": structure_signature_v2(an.all_fields),
            "formulario_id": "frm_combos", "url": url}
    sol = solicitud(info, valores={"nombre": "Zzprueba Uno", "pais": "España", "estado": "DF", "municipio": "Tonalá"})
    resp, salida = I.ejecutar(json.dumps(sol), settings=st, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia)
    assert (salida, resp["estado"]) == (7, "revision_caducada"), resp        # espera_revision_max_s = 0: no hay revisión, sí relleno
    assert all(c["estado"] == "ok" for c in resp["campos"]) and len(resp["campos"]) == 4
    assert resp["envio"]["realizado_por_conector"] is False
    from integ_helpers import Espia
    assert Espia.ultimo.valores["pais"] == "ES" and Espia.ultimo.valores["estado"] == "CDMX"
    assert "España" not in json.dumps(resp) and "Tonal" not in json.dumps(resp)      # sin valores en la respuesta
