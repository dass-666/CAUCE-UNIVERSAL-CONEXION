from pathlib import Path

from cauce_form_connector import lanzador


def test_paquete_python_store(monkeypatch):
    monkeypatch.setattr(lanzador.sys, "base_prefix",
                        r"C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0")
    assert lanzador.paquete_python_store() == "PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0"


def test_sin_python_store(monkeypatch, tmp_path):
    for attr in ("base_prefix", "_base_executable", "executable"):
        monkeypatch.setattr(lanzador.sys, attr, r"C:\Python313\python.exe", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert lanzador.paquete_python_store() == ""
    assert lanzador.localappdata_real() == str(tmp_path)


def test_usa_carpeta_redirigida_existente(monkeypatch, tmp_path):
    paquete = "PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0"
    real = tmp_path / "Packages" / paquete / "LocalCache" / "Local"
    (real / "CauceForms").mkdir(parents=True)
    monkeypatch.setattr(lanzador, "paquete_python_store", lambda: paquete)
    monkeypatch.setattr(lanzador.os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert Path(lanzador.localappdata_real()) == real
