"""Analizador HTML estático y detector de formularios (sin navegador)."""
from pathlib import Path

from cauce_form_connector.html_analyzer import analyze_html

LAB = Path(__file__).resolve().parents[1] / "lab_forms"


def read(name):
    return (LAB / name).read_text(encoding="utf-8")


def by_name(analysis):
    return {f.name: f for f in analysis.all_fields}


def test_traditional_form_fields_types_labels_and_validations():
    a = analyze_html(read("01_tradicional.html"))
    assert len(a.forms) == 1 and a.forms[0].id == "registro"
    f = by_name(a)
    assert f["nombre"].label == "Nombre" and f["nombre"].required and f["nombre"].maxlength == 60
    assert f["email"].type == "email" and f["email"].autocomplete == "email"
    assert f["telefono"].pattern == "[0-9 +]{8,16}"
    assert f["fecha_nacimiento"].type == "date" and f["fecha_nacimiento"].min == "1900-01-01"
    assert [o.label for o in f["pais"].options] == ["— Seleccione —", "México", "España", "Colombia", "Argentina"]
    assert f["mensaje"].tag == "textarea"
    assert f["acepta_terminos"].type == "checkbox" and f["acepta_terminos"].label == "Acepto los términos de uso"
    # Los radios se agrupan en un solo campo con opciones y la leyenda como etiqueta
    assert f["genero"].type == "radio" and f["genero"].label == "Género"
    assert [o.value for o in f["genero"].options] == ["F", "M", "X"]
    assert f["token_interno"].type == "hidden" and not f["token_interno"].fillable
    assert a.forms[0].submit_buttons == ["Enviar registro"]
    assert not a.safety.blocked


def test_confirmation_pairs_are_detected():
    a = analyze_html(read("03_validaciones.html"))
    f = by_name(a)
    assert f["email_confirmacion"].confirms == "alta::email"
    assert f["telefono_confirmacion"].confirms == "alta::telefono"
    assert f["email_confirmacion"].blocks_paste
    assert f["codigo_postal"].pattern == r"\d{5}"
    assert not f["ciudad"].confirms


def test_confirmation_heuristic_without_hints():
    html = """<form><label for=a>Correo</label><input id=a name=correo type=email>
      <label for=b>Repetir correo</label><input id=b name=correo2 type=email>
      <label for=c>Dirección</label><input id=c name=direccion>
      <label for=d>Dirección (línea 2)</label><input id=d name=direccion2></form>"""
    f = by_name(analyze_html(html))
    assert f["correo2"].confirms.endswith("::correo")
    assert f["direccion2"].confirms == ""   # una segunda línea NO es una confirmación


def test_static_analysis_cannot_see_js_fields_and_warns():
    a = analyze_html(read("02_dinamico.html"))
    assert a.all_fields == []
    assert any("JavaScript" in w for w in a.warnings)


def test_captcha_page_is_flagged_as_human_only():
    a = analyze_html(read("04_captcha_simulado.html"))
    assert a.safety.blocked and "CAPTCHA" in a.safety.reasons[0]


def test_meta_no_automation_and_orphan_fields():
    html = '<meta name="automation" content="none"><div><label for=x>Nombre</label><input id=x></div>'
    a = analyze_html(html)
    assert a.safety.blocked
    assert a.forms[0].key == "sin_form" and a.all_fields[0].label == "Nombre"


def test_label_strategies():
    html = """<form>
      <label>Ciudad <input name=ciudad></label>
      <span id=lb>Código postal</span><input name=cp aria-labelledby=lb>
      <div><span>Provincia</span><input name=prov></div>
    </form>"""
    f = by_name(analyze_html(html))
    assert f["ciudad"].label == "Ciudad"
    assert f["cp"].label == "Código postal"
    assert f["prov"].label == "Provincia"


def test_fingerprint_does_not_contain_html():
    a = analyze_html("<form><input name=secreto value='dato personal'></form>")
    assert a.source_label.startswith("html:") and "dato" not in a.source_label
