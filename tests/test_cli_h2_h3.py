"""CLI `fill` (H2 y H3). Archivo aparte: lanza su propio navegador y no puede convivir con la sesión de
Playwright de los otros módulos (la API síncrona no admite dos en el mismo hilo)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import cauce_form_connector.cli as cli
from cauce_form_connector.config_store import ProfileStore
from cauce_form_connector.html_analyzer import analyze_html
from cauce_form_connector.matcher import match_fields
from cauce_form_connector.page_inspector import BrowserSession

HTML_AMBIGUO = "<form id=f><label for=t>Teléfono</label><input id=t name=tel type=tel></form>"
DATOS_AMBIGUOS = [{"Teléfono casa": "5511112222", "Teléfono oficina": "5533334444"}]

@pytest.mark.browser
def test_h2_cli_detiene_el_llenado_ante_correspondencia_ambigua(tmp_path, monkeypatch, capsys):
    html, datos = tmp_path / "f.html", tmp_path / "d.json"
    html.write_text(HTML_AMBIGUO, encoding="utf-8")
    datos.write_text(json.dumps(DATOS_AMBIGUOS), encoding="utf-8")
    a = analyze_html(HTML_AMBIGUO)
    assert [m.status for m in match_fields(a.all_fields, list(DATOS_AMBIGUOS[0]))] == ["revisar"]   # precondición
    llamadas = []
    monkeypatch.setattr(cli, "fill_form", lambda *a, **k: llamadas.append(1))
    rc = cli.main(["fill", "--html", str(html), "--data", str(datos)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 4 and llamadas == []                                   # no se llegó a escribir
    assert out == {"codigo": "CORRESPONDENCIA_REQUIERE_REVISION", "campos": ["f::tel"], "escrito": 0}
    assert "5511112222" not in json.dumps(out)


@pytest.mark.browser
def test_h2_la_aprobacion_expresa_si_permite_rellenar(tmp_path, monkeypatch, capsys):
    html, datos, mapa = tmp_path / "f.html", tmp_path / "d.json", tmp_path / "m.json"
    html.write_text(HTML_AMBIGUO, encoding="utf-8")
    datos.write_text(json.dumps(DATOS_AMBIGUOS), encoding="utf-8")
    mapa.write_text(json.dumps({"f::tel": "Teléfono casa"}), encoding="utf-8")
    rc = cli.main(["fill", "--html", str(html), "--data", str(datos), "--mapping", str(mapa)])
    rep = json.loads(capsys.readouterr().out)
    assert rc == 0 and rep["ok"] and rep["results"][0]["expected"] == "5511112222"


# ------------------------------------------------------------------ H3: perfiles desactualizados o ajenos
def _perfil_para(html_path: Path, tmp_path, monkeypatch, mapping):
    monkeypatch.setenv("CAUCE_PROFILES_DIR", str(tmp_path / "perfiles"))
    with BrowserSession(headless=True) as s:
        s.open_url(html_path.resolve().as_uri())
        a = s.inspect()
    ProfileStore(tmp_path / "perfiles").save("p", a, mapping)
    return a


@pytest.mark.browser
@pytest.mark.parametrize("caso", ["estructura", "sitio"])
def test_h3_cli_con_perfil_desactualizado_o_ajeno_no_escribe(tmp_path, monkeypatch, capsys, caso):
    html, datos = tmp_path / "f.html", tmp_path / "d.json"
    html.write_text(HTML_AMBIGUO, encoding="utf-8")
    datos.write_text(json.dumps(DATOS_AMBIGUOS), encoding="utf-8")
    _perfil_para(html, tmp_path, monkeypatch, {"f::tel": "Teléfono casa"})
    destino = html
    if caso == "estructura":
        html.write_text(HTML_AMBIGUO.replace("Teléfono<", "Documento<"), encoding="utf-8")
    else:
        destino = tmp_path / "otro.html"
        destino.write_text(HTML_AMBIGUO, encoding="utf-8")
    llamadas = []
    monkeypatch.setattr(cli, "fill_form", lambda *a, **k: llamadas.append(1))
    rc = cli.main(["fill", "--html", str(destino), "--data", str(datos), "--profile", "p"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 5 and llamadas == [] and out["codigo"] == "PERFIL_NO_COINCIDE" and out["escrito"] == 0


@pytest.mark.browser
def test_h3_cli_con_perfil_vigente_no_agrega_sugerencias(tmp_path, monkeypatch, capsys):
    html, datos = tmp_path / "f.html", tmp_path / "d.json"
    html.write_text(HTML_AMBIGUO, encoding="utf-8")
    datos.write_text(json.dumps(DATOS_AMBIGUOS), encoding="utf-8")
    _perfil_para(html, tmp_path, monkeypatch, {"f::tel": "Teléfono casa"})
    visto = {}

    def falso(session, mapping, row, **k):
        visto.update(mapping=mapping, tardios=k["allow_late_fields"])
        from cauce_form_connector.models import FillReport
        return FillReport(ok=True, stopped_before_submit=True)

    monkeypatch.setattr(cli, "fill_form", falso)
    assert cli.main(["fill", "--html", str(html), "--data", str(datos), "--profile", "p"]) == 0
    assert visto == {"mapping": {"f::tel": "Teléfono casa"}, "tardios": False}   # solo lo aprobado


