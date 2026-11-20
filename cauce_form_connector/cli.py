"""Línea de comandos. Es también la vía de integración más simple para otros
sistemas (p. ej. Cauce Monitor): entrada por argumentos, salida JSON por stdout.

Ejemplos:
  python -m cauce_form_connector ui
  python -m cauce_form_connector analyze --html lab_forms/01_tradicional.html
  python -m cauce_form_connector analyze --url http://127.0.0.1:8765/lab/02_dinamico.html
  python -m cauce_form_connector propose --html lab_forms/03_validaciones.html --data sample_data/personas_ficticias.csv
  python -m cauce_form_connector fill --html lab_forms/03_validaciones.html --data sample_data/personas_ficticias.json \
      --row 0 --profile mi_perfil --screenshot salida.png
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .config_store import ProfileStore
from .data_adapter import columns_of, load_data
from .engine import fill_form
from .html_analyzer import analyze_html
from .matcher import match_fields
from .page_inspector import BrowserSession
from .settings import get_settings
from .url_policy import UrlNotAllowed, UrlPolicy


def _open(session: BrowserSession, args: argparse.Namespace, policy: UrlPolicy) -> None:
    if args.html:
        # Un archivo local se abre por file:// para que carguen sus CSS/JS relativos
        session.open_url(Path(args.html).resolve().as_uri())
    else:
        session.open_url(policy.check(args.url, args.authorized))


def _emit(obj: Any) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cauce_form_connector", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ui", help="abrir la interfaz local de revisión")

    def source(p: argparse.ArgumentParser) -> None:
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--html", help="archivo .html local")
        g.add_argument("--url", help="URL autorizada")
        p.add_argument("--authorized", action="store_true", help="declaro autorización sobre la URL")
        p.add_argument("--static", action="store_true", help="análisis estático, sin navegador (solo --html)")

    p_an = sub.add_parser("analyze", help="detectar formularios y campos")
    source(p_an)
    p_pr = sub.add_parser("propose", help="proponer correspondencias con un archivo de datos")
    source(p_pr)
    p_pr.add_argument("--data", required=True)
    p_fi = sub.add_parser("fill", help="rellenar carácter por carácter y verificar (no envía)")
    source(p_fi)
    p_fi.add_argument("--data", required=True)
    p_fi.add_argument("--row", type=int, default=0)
    p_fi.add_argument("--profile", help="usar la correspondencia guardada en profiles/<nombre>.json")
    p_fi.add_argument("--mapping", help="archivo JSON {field_key: columna}")
    p_fi.add_argument("--delay-ms", type=int)
    p_fi.add_argument("--screenshot")
    p_fi.add_argument("--headed", action="store_true", help="mostrar la ventana del navegador")

    args = ap.parse_args(argv)
    settings = get_settings()

    if args.cmd == "ui":
        from .web.app import main as ui_main
        sys.argv = [sys.argv[0]]
        ui_main()
        return 0

    policy = UrlPolicy.load(settings.allowed_hosts_file)
    audit = AuditLog(settings.audit_path)
    try:
        if args.cmd in ("analyze", "propose") and args.static:
            if not args.html:
                ap.error("--static solo funciona con --html")
            analysis = analyze_html(Path(args.html).read_text(encoding="utf-8"))
            session = None
        else:
            session = BrowserSession(settings, headless=not getattr(args, "headed", False) if args.cmd == "fill" else True)
            session.start()
            _open(session, args, policy)
            analysis = session.inspect()

        if args.cmd == "analyze":
            _emit(analysis.to_dict())
            return 0

        rows = load_data(args.data, Path(args.data).read_bytes())
        proposals = match_fields(analysis.all_fields, columns_of(rows))
        if args.cmd == "propose":
            _emit([m.to_dict() for m in proposals])
            return 0

        mapping = {m.field_key: m.column for m in proposals}
        if args.profile:
            rec = ProfileStore(settings.profiles_dir).reconcile(
                ProfileStore(settings.profiles_dir).load(args.profile), analysis)
            mapping.update(rec["mapping"])
        if args.mapping:
            mapping.update(json.loads(Path(args.mapping).read_text(encoding="utf-8")))
        report = fill_form(session, mapping, rows[args.row], delay_ms=args.delay_ms, audit=audit,
                           screenshot_path=args.screenshot)
        _emit(report.to_dict())
        return 0 if report.ok else 2
    except UrlNotAllowed as exc:
        print(f"URL no autorizada: {exc}", file=sys.stderr)
        return 3
    finally:
        if "session" in locals() and session is not None:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
