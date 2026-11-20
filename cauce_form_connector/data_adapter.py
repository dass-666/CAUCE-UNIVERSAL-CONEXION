"""Adaptador de entrada de datos: JSON, CSV y Excel (.xlsx) -> lista de filas.

Cada fila es un `dict[str, str]` columna -> valor. Todos los valores se
convierten a texto (las fechas de Excel a ISO `AAAA-MM-DD`) porque se van a
escribir en campos HTML.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

Rows = list[dict[str, str]]


class DataError(ValueError):
    pass


def _to_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _normalize(rows: list[dict[str, Any]]) -> Rows:
    out: Rows = []
    for r in rows:
        if not isinstance(r, dict):
            raise DataError("Cada registro debe ser un objeto columna -> valor.")
        row = {str(k).strip(): _to_text(v) for k, v in r.items() if k is not None and str(k).strip()}
        if any(row.values()):
            out.append(row)
    if not out:
        raise DataError("El archivo no contiene filas con datos.")
    return out


def load_json(content: bytes | str) -> Rows:
    data = json.loads(content.decode("utf-8-sig") if isinstance(content, bytes) else content)
    if isinstance(data, dict):
        # Admite {"registros": [...]} o un único objeto
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if len(lists) == 1 else [data]
    if not isinstance(data, list):
        raise DataError("El JSON debe ser una lista de objetos.")
    return _normalize(data)


def load_csv(content: bytes | str) -> Rows:
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return _normalize(list(csv.DictReader(io.StringIO(text), dialect=dialect)))


def load_xlsx(content: bytes, sheet: str | None = None) -> Rows:
    from openpyxl import load_workbook  # importación diferida: solo si se usa Excel

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    try:
        header = [(_to_text(h) or f"columna{i + 1}") for i, h in enumerate(next(it))]
    except StopIteration:
        raise DataError("La hoja de Excel está vacía.")
    rows = [dict(zip(header, r)) for r in it]
    wb.close()
    return _normalize(rows)


def load_data(filename: str, content: bytes) -> Rows:
    ext = Path(filename).suffix.lower()
    if ext == ".json":
        return load_json(content)
    if ext in (".csv", ".tsv", ".txt"):
        return load_csv(content)
    if ext in (".xlsx", ".xlsm"):
        return load_xlsx(content)
    raise DataError(f"Formato no admitido: {ext}. Use .json, .csv o .xlsx")


def columns_of(rows: Rows) -> list[str]:
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return cols
