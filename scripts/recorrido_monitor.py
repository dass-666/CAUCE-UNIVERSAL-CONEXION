"""Recorrido visible Monitor -> conector con datos FICTICIOS (no envía nada).

Sirve lab_forms/ en 127.0.0.1, aprueba el perfil del laboratorio (paso humano simulado), "selecciona" un expediente
ficticio y abre una ventana de navegador visible rellenada y verificada. Úselo con `--headed`/pantalla real.
  python scripts/recorrido_monitor.py [--espera-s 120] [--captura evidence/recorrido_monitor.png]
La captura es solo para esta demostración con datos ficticios; el modo `preparar` nunca guarda capturas.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import shutil
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cauce_form_connector import puente_monitor as P  # noqa: E402
from cauce_form_connector.settings import get_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--espera-s", type=int, default=120)
    ap.add_argument("--captura", help="PNG de la ventana tras el relleno (solo datos ficticios)")
    ap.add_argument("--formulario", default=str(ROOT / "config/formularios/lab_validaciones.json"))
    ap.add_argument("--expediente", default=str(ROOT / "sample_data/expediente_monitor_sintetico.json"))
    a = ap.parse_args()
    os.environ.setdefault("CAUCE_PROFILES_DIR", str(ROOT / "evidence" / "out" / "perfiles"))
    if "CAUCE_FUENTES_DIR" not in os.environ:   # fuente de identificadores FICTICIA, solo para esta demostración
        fuentes = ROOT / "evidence" / "out" / "fuentes"
        fuentes.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "sample_data" / "fuente_identificadores_sintetica.json", fuentes / "identificadores.json")
        os.environ["CAUCE_FUENTES_DIR"] = str(fuentes)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "lab_forms"))
    handler.log_message = lambda *args: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    st = get_settings()
    cfg = P.cargar_formulario(a.formulario, puerto=srv.server_address[1])
    exp = json.loads(Path(a.expediente).read_text(encoding="utf-8"))
    print("1) Aprobación humana del perfil (simulada con rev_demo)…")
    P.aprobar_perfil(cfg, st, aprobado_por="rev_demo")
    gancho = None
    if a.captura:
        hecho = {"v": False}

        def gancho(s):
            if not hecho["v"]:
                hecho["v"] = True
                Path(a.captura).parent.mkdir(parents=True, exist_ok=True)
                s.page.screenshot(path=a.captura, full_page=True)
        print(f"   (captura de demostración → {a.captura})")
    print("2) Monitor selecciona expediente y lanza el conector; revise la ventana y ciérrela (no se envía)…")
    r = P.autorrellenar(cfg, exp, st, espera_revision_s=a.espera_s, gancho_revision=gancho)
    print(P.texto_informe(r["informe"]))
    srv.shutdown()
    return r["salida"]


if __name__ == "__main__":
    raise SystemExit(main())
