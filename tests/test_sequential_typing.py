"""Evidencia de escritura secuencial: cada carácter genera sus propios eventos
de teclado y el conector nunca asigna `.value` directamente en campos de texto."""
import pytest

from cauce_form_connector.engine import fill_form
from cauce_form_connector.matcher import match_fields
from cauce_form_connector.models import FieldDescriptor
from cauce_form_connector.sequential_typer import SequentialTyper, choose_option, normalize_date, to_bool
from cauce_form_connector.models import FieldOption
from conftest import keylog, value_sets

pytestmark = pytest.mark.browser


def text_field(key, sel, tag="input", typ="text"):
    return FieldDescriptor(key=key, selector=sel, tag=tag, type=typ)


def test_every_character_produces_keydown_input_keyup(session, lab_url):
    session.open_url(lab_url("01_tradicional.html"))
    t = SequentialTyper(session.page, delay_ms=0)
    value = "José Ñandú @ 2026"
    n = t.type_text(text_field("k", "#nombre"), value)
    log = keylog(session)["nombre"]
    assert n == len(value)
    assert session.page.input_value("#nombre") == value
    # Un keydown, un keyup y un input por carácter, incluidos ñ, é y ú (vía CDP)
    assert log["keydown"] == len(value) and log["keyup"] == len(value)
    assert log["input"] == len(value) and log["inputTypes"] == {"insertText": len(value)}
    assert "".join(log["keys"]) == value
    assert log["paste"] == 0
    assert value_sets(session).get("nombre", 0) == 0, "no se debe asignar .value directamente"


def test_existing_content_is_cleared_with_keyboard(session, lab_url):
    session.open_url(lab_url("01_tradicional.html"))
    t = SequentialTyper(session.page, delay_ms=0)
    f = text_field("k", "#apellidos")
    t.type_text(f, "Primero")
    t.type_text(f, "Segundo")
    assert session.page.input_value("#apellidos") == "Segundo"
    log = keylog(session)["apellidos"]
    assert log["inputTypes"].get("deleteContentBackward") == 1
    assert value_sets(session).get("apellidos", 0) == 0


def test_newlines_never_press_enter_in_single_line_inputs(session, lab_url):
    session.open_url(lab_url("01_tradicional.html"))
    t = SequentialTyper(session.page, delay_ms=0)
    t.type_text(text_field("k", "#nombre"), "Ana\nMaría")
    assert session.page.input_value("#nombre") == "Ana María"
    assert keylog(session)["nombre"]["enter"] == 0
    assert session.page.evaluate("document.body.dataset.enviado") is None
    # En textarea los saltos de línea sí se conservan
    t.type_text(text_field("m", "#mensaje", tag="textarea", typ="textarea"), "L1\nL2")
    assert session.page.input_value("#mensaje") == "L1\nL2"


def test_typing_delay_is_respected(session, lab_url):
    import time
    session.open_url(lab_url("01_tradicional.html"))
    t = SequentialTyper(session.page, delay_ms=40)
    t0 = time.monotonic()
    t.type_text(text_field("k", "#nombre"), "abcdefghij")
    assert time.monotonic() - t0 >= 9 * 0.040


def test_whole_lab1_fill_has_no_programmatic_value_sets(session, lab_url, rows):
    session.open_url(lab_url("01_tradicional.html"))
    a = session.inspect()
    mapping = {m.field_key: m.column for m in match_fields(a.all_fields, list(rows[0]))}
    rep = fill_form(session, mapping, rows[0], delay_ms=0)
    log = keylog(session)
    sets = value_sets(session)
    for r in rep.results:
        if r.method == "teclado_secuencial":
            name = r.field_key.split("::")[1]
            # Enter (salto de línea del textarea) se cuenta aparte porque su key no es 1 carácter
            assert log[name]["keydown"] + log[name]["enter"] == r.keystrokes == len(r.expected), name
            assert sets.get(name, 0) == 0, name


def test_helpers():
    assert normalize_date("14/03/1988") == "1988-03-14"
    assert to_bool("Sí") is True and to_bool("no") is False and to_bool("quizá") is None
    opts = [FieldOption("MX", "México"), FieldOption("ES", "España")]
    assert choose_option(opts, "mexico").value == "MX"
    assert choose_option(opts, "ES").value == "ES"
    assert choose_option(opts, "Perú") is None
