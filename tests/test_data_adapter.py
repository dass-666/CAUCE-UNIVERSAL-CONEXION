"""Adaptador de datos."""
import io
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from cauce_form_connector.data_adapter import DataError, columns_of, load_data

DATA = Path(__file__).resolve().parents[1] / "sample_data"


@pytest.mark.parametrize("name", ["personas_ficticias.json", "personas_ficticias.csv", "personas_ficticias.xlsx"])
def test_sample_files_load_identically(name):
    rows = load_data(name, (DATA / name).read_bytes())
    assert len(rows) == 2
    assert rows[0]["Nombre"] == "José Ángel" and rows[0]["Teléfono"] == "5512345678"
    assert rows[1]["Correo electrónico"] == "lucia.fernandez@example.org"
    assert columns_of(rows)[0] == "Nombre"


def test_csv_with_comma_and_bom():
    rows = load_data("x.csv", "\ufeffnombre,email\nAna,ana@example.com\n".encode())
    assert rows == [{"nombre": "Ana", "email": "ana@example.com"}]


def test_excel_dates_and_numbers_become_text():
    wb = Workbook()
    ws = wb.active
    ws.append(["Fecha", "CP", "Activo"])
    ws.append([date(1990, 5, 1), 6700.0, True])
    buf = io.BytesIO()
    wb.save(buf)
    rows = load_data("d.xlsx", buf.getvalue())
    assert rows == [{"Fecha": "1990-05-01", "CP": "6700", "Activo": "true"}]


def test_json_wrapper_object():
    rows = load_data("d.json", b'{"registros": [{"a": 1}]}')
    assert rows == [{"a": "1"}]


@pytest.mark.parametrize("name,content", [("a.json", b"[]"), ("a.csv", b"a,b\n"), ("a.pdf", b"x")])
def test_invalid_inputs(name, content):
    with pytest.raises(DataError):
        load_data(name, content)
