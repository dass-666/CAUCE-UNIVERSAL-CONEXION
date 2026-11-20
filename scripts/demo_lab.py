"""Demostración verificable sobre los formularios de laboratorio.

Ejecuta el conector sobre los 4 formularios de laboratorio con datos
ficticios y genera en `evidence/out/`:
- informe_demo.md: tabla por formulario con valor esperado, valor registrado,
  número de caracteres enviados y eventos de teclado que la PÁGINA recibió.
- informe_demo.json: lo mismo en formato máquina.
- capturas PNG de cada formulario rellenado (antes de enviar).

Uso:
    python scripts/demo_lab.py            # sin ventana
    python scripts/demo_lab.py --headed   # viendo el navegador (pausa entre teclas visible)
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import socket
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cauce_form_connector.data_adapter import load_json  # noqa: E402
from cauce_form_connector.engine import fill_form  # noqa: E402
from cauce_form_connector.instrumentation import KEYLOG_JS  # noqa: E402
from cauce_form_connector.matcher import match_fields  # noqa: E402
from cauce_form_connector.page_inspector import BrowserSession  # noqa: E402

CASES = [
    ("01_tradicional.html", 0, "Formulario HTML tradicional"),
    ("02_dinamico.html", 0, "Formulario dinámico (persona física)"),
    ("02_dinamico.html", 1, "Formulario dinámico (empresa)"),
    ("03_validaciones.html", 0, "Validaciones y confirmaciones de correo y teléfono"),
    ("04_captcha_simulado.html", 0, "Página con verificación humana (debe bloquearse)"),
]


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve_lab() -> tuple[str, http.server.ThreadingHTTPServer]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port),
                                          functools.partial(_Quiet, directory=str(ROOT / "lab_forms")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", srv


def md_escape(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", "⏎")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--delay-ms", type=int, default=None)
    args = ap.parse_args()

    out = ROOT / "evidence" / "out"
    out.mkdir(parents=True, exist_ok=True)
    rows = load_json((ROOT / "sample_data" / "personas_ficticias.json").read_bytes())
    base, srv = serve_lab()
    delay = args.delay_ms if args.delay_ms is not None else (60 if args.headed else 0)

    summary = []
    lines = [f"# Demostración del conector · {datetime.now():%Y-%m-%d %H:%M}", "",
             "Datos 100 % ficticios (`sample_data/personas_ficticias.json`). Formularios propios servidos en "
             "127.0.0.1. La columna **Eventos recibidos** la mide la propia página con un script de "
             "instrumentación: cuenta keydown/keyup por carácter y asignaciones directas de `.value`.", ""]
    session = BrowserSession(headless=not args.headed)
    session.add_init_script(KEYLOG_JS)
    session.start()
    try:
        for i, (lab, row_idx, title) in enumerate(CASES, 1):
            row = rows[row_idx]
            session.open_url(f"{base}/{lab}")
            analysis = session.inspect()
            mapping = {m.field_key: m.column for m in match_fields(analysis.all_fields, list(row))}
            shot = out / f"{i:02d}_{Path(lab).stem}_fila{row_idx + 1}.png"
            rep = fill_form(session, mapping, row, delay_ms=delay, screenshot_path=str(shot))
            log = session.page.evaluate("window.__keylog || {}")
            sets = session.page.evaluate("window.__valueSets || {}")
            submitted = session.page.evaluate("document.body.dataset.enviado") == "si"

            lines += [f"## {i}. {title}", "", f"Archivo: `lab_forms/{lab}` · fila {row_idx + 1} · "
                      f"resultado global: **{'OK' if rep.ok else ('BLOQUEADO' if rep.blocked else 'CON INCIDENCIAS')}** · "
                      f"enviado: **{'sí' if submitted else 'no'}** · {rep.duration_ms} ms", ""]
            if rep.blocked:
                lines += ["Motivo: " + " ".join(rep.blocked_reasons), "",
                          "No se escribió nada en la página.", ""]
            else:
                lines += ["| Campo | Método | Esperado | Registrado | Estado | Caracteres enviados | Eventos recibidos por la página |",
                          "|---|---|---|---|---|---|---|"]
                for r in rep.results:
                    name = r.field_key.split("::")[1]
                    k = log.get(name, {})
                    if r.method == "teclado_secuencial":
                        ev = (f"keydown {k.get('keydown', 0) + k.get('enter', 0)}, keyup {k.get('keyup', 0) + k.get('enter', 0)}, "
                              f"input {k.get('input', 0)}, pegar {k.get('paste', 0)}, `.value=` {sets.get(name, 0)}")
                    else:
                        ev = "— (control sin texto)"
                    late = " *(apareció al rellenar)*" if r.discovered_late else ""
                    lines.append(f"| {md_escape(r.label or name)}{late} | {r.method} | {md_escape(r.expected)} | "
                                 f"{md_escape(r.actual)} | {r.status} | {r.keystrokes or ''} | {ev} |")
                if rep.confirmations:
                    lines += ["", "Confirmaciones: " + "; ".join(
                        f"{c['label']} → **{c['detail']}**" for c in rep.confirmations)]
                if rep.submit_buttons:
                    lines += ["", f"Botón de envío detectado y **no pulsado**: {', '.join(rep.submit_buttons)}"]
                lines += ["", f"Captura: `{shot.relative_to(ROOT)}`", ""]
            summary.append({"lab": lab, "row": row_idx, "title": title, "submitted": submitted,
                            "keylog": log, "value_sets": sets, "report": rep.to_dict()})
    finally:
        session.close()
        srv.shutdown()

    lines += ["## Nota sobre `.value=`", "",
              "En los laboratorios 2 y 3 la propia página asigna `.value` (re-render del componente "
              "controlado y máscara de teléfono). Esos contadores son de la página, no del conector: en el "
              "laboratorio 1, que no tiene scripts que toquen los campos, el contador es 0 en todos los "
              "campos de texto. Ver `tests/test_sequential_typing.py`."]
    (out / "informe_demo.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "informe_demo.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = all(s["report"]["ok"] for s in summary if not s["lab"].startswith("04")) \
        and all(s["report"]["blocked"] for s in summary if s["lab"].startswith("04")) \
        and not any(s["submitted"] for s in summary)
    print(f"Informe: {out / 'informe_demo.md'}")
    print("DEMO SUPERADA" if ok else "DEMO CON FALLOS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
