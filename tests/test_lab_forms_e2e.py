"""Pruebas de extremo a extremo sobre los formularios de laboratorio."""
import pytest

from cauce_form_connector.engine import fill_form, submit_with_human_confirmation
from cauce_form_connector.matcher import match_fields
from conftest import keylog

pytestmark = pytest.mark.browser


def run(session, url, row, overrides=None, **kw):
    session.open_url(url)
    a = session.inspect()
    mapping = {m.field_key: m.column for m in match_fields(a.all_fields, list(row))}
    mapping.update(overrides or {})
    return a, fill_form(session, mapping, row, delay_ms=0, **kw)


def results(rep):
    return {r.field_key.split("::")[1]: r for r in rep.results}


def not_submitted(session):
    return session.page.evaluate("document.body.dataset.enviado") is None


def test_lab1_traditional(session, lab_url, rows):
    a, rep = run(session, lab_url("01_tradicional.html"), rows[0])
    r = results(rep)
    assert rep.ok, [x.to_dict() for x in rep.results]
    assert r["pais"].actual == "MX" and r["pais"].method == "select_option"
    assert r["genero"].actual == "M" and r["genero"].method == "check"
    assert r["fecha_nacimiento"].actual == "1988-03-14"
    assert r["acepta_terminos"].actual == "true"
    assert r["mensaje"].actual == rows[0]["Mensaje"]
    assert rep.submit_buttons == ["Enviar registro"]
    assert rep.stopped_before_submit and not_submitted(session)
    assert "token_interno" not in r


def test_lab2_dynamic_person(session, lab_url, rows):
    a, rep = run(session, lab_url("02_dinamico.html"), rows[0])
    # El análisis espera al JavaScript: el formulario se crea 600 ms después de cargar
    assert {f.name for f in a.all_fields} >= {"tipo_solicitante", "email", "comentarios"}
    r = results(rep)
    assert rep.ok, [x.to_dict() for x in rep.results]
    # Campos que solo aparecen al elegir "persona", descubiertos y rellenados en otra pasada
    for k in ("nombre", "apellidos", "documento"):
        assert r[k].discovered_late and r[k].status == "ok", r[k]
    assert "empresa" not in r
    # El componente "controlado" re-renderiza desde su estado al perder el foco:
    # el valor se conserva porque se escribió con eventos reales
    assert session.page.input_value("#nombre") == "José Ángel"
    assert not_submitted(session)


def test_lab2_dynamic_company(session, lab_url, rows):
    a, rep = run(session, lab_url("02_dinamico.html"), rows[1])
    r = results(rep)
    assert rep.ok, [x.to_dict() for x in rep.results]
    assert r["empresa"].actual == "Tostadores Ficticios S.A. de C.V." and r["empresa"].discovered_late
    assert r["cargo"].actual == "Directora de compras"
    assert "nombre" not in r


def test_lab2_direct_value_assignment_would_be_lost(session, lab_url):
    """Contraprueba: asignar .value sin eventos (lo que NO hace el conector) se pierde."""
    session.open_url(lab_url("02_dinamico.html"))
    session.page.wait_for_selector("#email")
    session.page.eval_on_selector("#email", "el => { el.value = 'directo@example.com'; el.focus(); el.blur(); }")
    assert session.page.input_value("#email") == ""


def test_lab3_validations_and_confirmations(session, lab_url, rows):
    a, rep = run(session, lab_url("03_validaciones.html"), rows[0])
    r = results(rep)
    assert rep.ok, [x.to_dict() for x in rep.results]
    assert {c["confirmacion"].split("::")[1]: c["match"] for c in rep.confirmations} == {
        "email_confirmacion": True, "telefono_confirmacion": True}
    # La máscara de la página da formato al teléfono; el validador lo reconoce
    assert r["telefono"].actual == "55 1234 5678" and r["telefono"].status == "ok_normalizado"
    # La página contó las pulsaciones del campo de confirmación (pegar está bloqueado)
    assert session.page.text_content("#cnt_email_confirmacion") == f"Pulsaciones: {len(rows[0]['Correo electrónico'])}"
    assert keylog(session)["email_confirmacion"]["paste"] == 0
    # Todas las validaciones de la página pasan: el botón de envío se habilita, pero NO se pulsa
    assert session.page.is_enabled("#enviar")
    assert not_submitted(session)


def test_lab3_mismatched_confirmation_is_reported(session, lab_url, rows):
    row = dict(rows[0], **{"Correo alternativo": "otro@example.com"})
    _, rep = run(session, lab_url("03_validaciones.html"), row,
                 overrides={"alta::email_confirmacion": "Correo alternativo"})
    conf = {c["confirmacion"].split("::")[1]: c for c in rep.confirmations}
    assert conf["email_confirmacion"]["match"] is False
    r = results(rep)
    assert r["email_confirmacion"].status == "invalido"
    assert "No coincide" in r["email_confirmacion"].page_error
    assert not rep.ok
    assert not session.page.is_enabled("#enviar")


def test_lab3_invalid_data_detected(session, lab_url, rows):
    row = dict(rows[0], **{"Código postal": "123"})
    _, rep = run(session, lab_url("03_validaciones.html"), row)
    r = results(rep)
    assert r["codigo_postal"].status == "invalido" and not rep.ok


def test_maxlength_truncation_is_detected(session, lab_url, rows):
    row = dict(rows[0], CURP="X" * 25)
    _, rep = run(session, lab_url("02_dinamico.html"), row)
    r = results(rep)
    assert r["documento"].status == "diferente" and "maxlength" in r["documento"].detail


def test_unknown_select_option_is_an_error(session, lab_url, rows):
    row = dict(rows[0], **{"País": "Atlántida"})
    _, rep = run(session, lab_url("01_tradicional.html"), row)
    r = results(rep)
    assert r["pais"].status == "error" and "opción" in r["pais"].detail
    assert not rep.ok


def test_captcha_page_is_not_filled(session, lab_url, rows):
    _, rep = run(session, lab_url("04_captcha_simulado.html"), rows[0])
    assert rep.blocked and rep.results == []
    assert session.page.input_value("#email") == ""
    with pytest.raises(PermissionError):
        submit_with_human_confirmation(session, "ENVIAR")


def test_submit_requires_exact_human_phrase(session, lab_url, rows):
    _, rep = run(session, lab_url("01_tradicional.html"), rows[0])
    with pytest.raises(PermissionError):
        submit_with_human_confirmation(session, "si")
    assert not_submitted(session)
    out = submit_with_human_confirmation(session, "ENVIAR")
    assert out["submitted"] and session.page.evaluate("document.body.dataset.enviado") == "si"


def test_pasted_html_has_no_network(session):
    html = """<form id=f><label for=n>Nombre</label><input id=n name=n>
      <img src="https://example.com/rastreo.png"></form>
      <script>fetch('https://example.com/x').then(()=>document.body.dataset.red='si').catch(()=>document.body.dataset.red='bloqueada')</script>"""
    session.open_html(html)
    a = session.inspect()
    assert [f.name for f in a.all_fields] == ["n"]
    session.page.wait_for_function("document.body.dataset.red !== undefined")
    assert session.page.evaluate("document.body.dataset.red") == "bloqueada"
