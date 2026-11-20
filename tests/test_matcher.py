"""Motor de correspondencia."""
from cauce_form_connector.html_analyzer import analyze_html
from cauce_form_connector.matcher import apply_manual, concept_of_text, match_fields
from cauce_form_connector.models import FieldDescriptor


def fd(key, **kw):
    return FieldDescriptor(key=key, selector="#" + key, tag="input", type=kw.pop("type", "text"), **kw)


def as_dict(ms):
    return {m.field_key: m for m in ms}


def test_concepts_in_spanish_and_english():
    assert concept_of_text("Correo electrónico") == "email"
    assert concept_of_text("E-mail") == "email"
    assert concept_of_text("Teléfono móvil") == "telefono"
    assert concept_of_text("Nombre de la empresa") == "empresa"
    assert concept_of_text("lastName") == "apellido"
    assert concept_of_text("Código postal") == "codigo_postal"


def test_autocomplete_and_concepts_produce_auto_status():
    fields = [fd("f::a", name="a", autocomplete="given-name", label="Su primer dato"),
              fd("f::b", name="b", type="email"), fd("f::c", name="c", label="Móvil", type="tel")]
    m = as_dict(match_fields(fields, ["Nombre", "Correo", "Celular"]))
    assert m["f::a"].column == "Nombre" and m["f::a"].status == "auto" and m["f::a"].confidence >= 0.95
    assert m["f::b"].column == "Correo"
    assert m["f::c"].column == "Celular"


def test_exact_name_match():
    m = match_fields([fd("f::fecha_alta", name="fecha_alta", label="X")], ["fecha_alta", "otra"])
    assert m[0].column == "fecha_alta" and m[0].confidence == 0.97


def test_unknown_field_has_no_data():
    m = match_fields([fd("f::zz", name="zz", label="Color favorito")], ["Nombre", "Correo"])
    assert m[0].column is None and m[0].status == "sin_datos"


def test_each_column_used_once_and_ambiguity_flagged():
    fields = [fd("f::n1", name="n1", label="Nombre"), fd("f::n2", name="n2", label="Nombre")]
    m = as_dict(match_fields(fields, ["Nombre"]))
    cols = [x.column for x in m.values() if x.column]
    assert cols == ["Nombre"]


def test_medium_similarity_requires_review():
    m = match_fields([fd("f::obs", name="obs", label="Notas internas")], ["Notas"])
    assert m[0].column == "Notas" and m[0].status == "revisar"


def test_confirmation_field_reuses_original_column():
    html = """<form id=f><label for=e>Correo</label><input id=e name=e type=email>
       <label for=e2>Confirme su correo</label><input id=e2 name=e2 type=email data-confirm=e></form>"""
    a = analyze_html(html)
    m = as_dict(match_fields(a.all_fields, ["Correo electrónico"]))
    assert m["f::e"].column == m["f::e2"].column == "Correo electrónico"
    assert "confirmación" in m["f::e2"].reason


def test_manual_override():
    ms = match_fields([fd("f::a", name="a", label="Nombre")], ["Nombre", "Alias"])
    out = apply_manual(ms, {"f::a": "Alias"})
    assert out[0].column == "Alias" and out[0].status == "manual"
    out = apply_manual(ms, {"f::a": None})
    assert out[0].column is None and out[0].status == "ignorado"


class FakeAI:
    def __init__(self):
        self.called = 0

    def suggest(self, field, columns):
        self.called += 1
        return "Color"


def test_optional_resolver_is_only_a_suggestion():
    ai = FakeAI()
    m = match_fields([fd("f::x", name="x", label="Tono preferido")], ["Color"], resolver=ai)
    assert ai.called == 1 and m[0].column == "Color" and m[0].status == "revisar"


def test_lab_forms_map_with_sample_columns(rows):
    from pathlib import Path
    lab = Path(__file__).resolve().parents[1] / "lab_forms" / "01_tradicional.html"
    a = analyze_html(lab.read_text(encoding="utf-8"))
    m = {k.split("::")[1]: v for k, v in as_dict(match_fields(a.all_fields, list(rows[0]))).items()}
    expected = {"nombre": "Nombre", "apellidos": "Apellidos", "email": "Correo electrónico",
                "telefono": "Teléfono", "fecha_nacimiento": "Fecha de nacimiento", "pais": "País",
                "genero": "Género", "mensaje": "Mensaje", "acepta_terminos": "Acepta términos"}
    for k, col in expected.items():
        assert m[k].column == col, (k, m[k])
        assert m[k].status == "auto"
