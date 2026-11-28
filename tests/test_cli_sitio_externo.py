"""CLI `autorrellenar` con sitios externos: en cada ejecución se exige lista de permitidos, declaración y una
referencia real de autorización (no la de demostración). No se abre ninguna página externa."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cauce_form_connector.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[1]
EXP = str(ROOT / "sample_data/expediente_monitor_sintetico.json")


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    hosts = tmp_path / "hosts.json"
    hosts.write_text(json.dumps({"allowed_hosts": ["portal.ejemplo.invalid"]}), encoding="utf-8")
    monkeypatch.setenv("CAUCE_ALLOWED_HOSTS_FILE", str(hosts))
    monkeypatch.setenv("CAUCE_PROFILES_DIR", str(tmp_path / "p"))
    import cauce_form_connector.page_inspector as PI
    monkeypatch.setattr(PI.BrowserSession, "start", lambda self: (_ for _ in ()).throw(AssertionError("no debía abrirse")))
    cfg = json.loads((ROOT / "config/formularios/lab_validaciones.json").read_text(encoding="utf-8"))
    cfg["url"] = "https://portal.ejemplo.invalid/alta"
    ruta = tmp_path / "cfg.json"
    ruta.write_text(json.dumps(cfg), encoding="utf-8")
    return str(ruta), tmp_path


@pytest.mark.parametrize("extra", [[], ["--authorized", "--autorizacion", "aut_demo"],
                                   ["--authorized", "--autorizacion", "AUT-mala"]])
def test_sitio_externo_exige_autorizacion_real(entorno, extra, capsys):
    cfg, _ = entorno
    assert cli_main(["autorrellenar", "--formulario", cfg, "--expediente", EXP, "--espera-s", "0", *extra]) == 3
    assert "URL no autorizada" in capsys.readouterr().err


def test_dominio_fuera_de_la_lista_da_codigo_3_y_no_6(entorno, tmp_path, capsys):
    cfg, _ = entorno
    otro = json.loads(Path(cfg).read_text(encoding="utf-8"))
    otro["url"] = "https://otro.ejemplo.invalid/alta"
    p = tmp_path / "otro.json"
    p.write_text(json.dumps(otro), encoding="utf-8")
    assert cli_main(["autorrellenar", "--formulario", str(p), "--expediente", EXP, "--authorized",
                     "--autorizacion", "aut_portal_2026", "--espera-s", "0"]) == 3
    assert "lista de permitidos" in capsys.readouterr().err


def test_error_de_configuracion_muestra_el_motivo(tmp_path, capsys):
    cfg = json.loads((ROOT / "config/formularios/lab_cita_monitor.json").read_text(encoding="utf-8"))
    cfg.pop("equivalencias")
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    assert cli_main(["autorrellenar", "--formulario", str(p), "--expediente", EXP, "--puerto", "1"]) == 6
    assert "no tiene tabla de equivalencias" in capsys.readouterr().err
