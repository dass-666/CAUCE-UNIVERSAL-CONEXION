"""El ejemplo de integración con Cauce Monitor es reproducible, no envía y no guarda datos personales."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.browser


def test_example_runs_without_submit_and_without_personal_data(tmp_path):
    out = tmp_path / "registro.json"
    env = dict(os.environ, CAUCE_HEADLESS="1", CAUCE_TYPING_DELAY_MS="0",
               CAUCE_AUDIT_PATH=str(tmp_path / "audit.jsonl"))
    p = subprocess.run([sys.executable, "scripts/ejemplo_cauce_monitor.py", "--no-interactivo", "--salida", str(out)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "EJEMPLO SUPERADO" in p.stdout
    reg = json.loads(out.read_text(encoding="utf-8"))
    assert reg["estado"] == "pendiente_confirmacion_humana" and reg["confirmado_por_humano"] is False
    assert reg["envio_en_auditoria"] is False
    assert all(c["coinciden"] for c in reg["confirmaciones"]) and len(reg["confirmaciones"]) == 2
    persisted = out.read_text(encoding="utf-8") + (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    sys.path.insert(0, str(ROOT / "scripts"))
    from ejemplo_cauce_monitor import FILA_FICTICIA
    for v in FILA_FICTICIA.values():
        if len(v) >= 4:
            assert v not in persisted
