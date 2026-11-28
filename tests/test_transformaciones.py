"""La verificación describe qué transformó la página sin mostrar valores (datos SINTÉTICOS)."""
from cauce_form_connector.models import FieldDescriptor
from cauce_form_connector.validator import compare, transformacion


def _f(tipo="text", **kw):
    return FieldDescriptor(**{**dict(key="k", selector="#k", tag="input", type=tipo), **kw})


def test_correo_en_campo_de_texto_tolera_mayusculas_y_lo_dice():
    estado, detalle = compare(_f("text"), "Zzx.Prueba@Ejemplo.invalid", "zzx.prueba@ejemplo.invalid")
    assert estado == "ok_normalizado" and "minúsculas" in detalle and "zzx" not in detalle.lower()


def test_transformaciones_sin_datos():
    assert transformacion("Abc Def", "ABC DEF") == "convirtió el texto a mayúsculas"
    assert transformacion("Abc Def", "AbcDef") == "cambió los espacios internos"
    assert transformacion("Abc", "") == "dejó el campo vacío"
    d = transformacion("Zzx Prueba", "Otro")
    assert "Zzx" not in d and "Otro" not in d and "10 → 4" in d
