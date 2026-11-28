"""Línea de comandos. Es también la vía de integración más simple para otros
sistemas (p. ej. Cauce Monitor): entrada por argumentos, salida JSON por stdout.

Ejemplos:
  python -m cauce_form_connector ui
  python -m cauce_form_connector analyze --html lab_forms/01_tradicional.html
  python -m cauce_form_connector analyze --url http://127.0.0.1:8765/lab/02_dinamico.html
  python -m cauce_form_connector propose --html lab_forms/03_validaciones.html --data sample_data/personas_ficticias.csv
  python -m cauce_form_connector fill --html lab_forms/03_validaciones.html --data sample_data/personas_ficticias.json \
      --row 0 --profile mi_perfil --screenshot salida.png

Integración con Cauce Monitor (contrato cauce.conector.v1; una solicitud por proceso):
  python -m cauce_form_connector aprobar-perfil --url http://127.0.0.1:8765/lab/03_validaciones.html \
      --perfil mi_perfil --formulario-id frm_demo --aprobado-por rev_demo --campos campos_aprobados.json
  python -m cauce_form_connector preparar < solicitud.json > respuesta.json   # NUNCA envía el formulario

Códigos de salida de `fill`: 0 relleno verificado, 2 con diferencias, 3 URL no autorizada, 4 correspondencias que
requieren revisión (no se escribió nada), 5 perfil o estructura no coinciden (no se escribió nada).
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
from .engine import fill_form, safe_cli_mapping
from .html_analyzer import analyze_html
from .matcher import match_fields
from .page_inspector import BrowserSession
from .settings import get_settings
from .url_policy import UrlNotAllowed, UrlPolicy


def _open(session: BrowserSession, args: argparse.Namespace, policy: UrlPolicy) -> None:
    if args.html:
        # Un archivo local se abre por file:// para que carguen sus CSS/JS relativos
        uri = policy.check(Path(args.html).resolve().as_uri(), False)
        session.open_url(uri, policy.guard(False))
    else:
        session.open_url(policy.check(args.url, args.authorized), policy.guard(args.authorized))


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
    sub.add_parser("preparar", help="contrato cauce.conector.v1: una solicitud JSON por stdin, una respuesta JSON "
                                    "por stdout; deja el formulario listo para revisión humana y no lo envía")
    p_ap = sub.add_parser("aprobar-perfil", help="guardar un perfil APROBADO para `preparar` y mostrar su SHA-256")
    p_ap.add_argument("--url", required=True, help="URL exacta del formulario (la misma que irá en la solicitud)")
    p_ap.add_argument("--authorized", action="store_true")
    p_ap.add_argument("--perfil", required=True)
    p_ap.add_argument("--formulario-id", required=True, help="frm_...")
    p_ap.add_argument("--aprobado-por", required=True, help="rev_... (referencia opaca de quien revisó)")
    p_ap.add_argument("--campos", required=True,
                      help="archivo JSON {nombre_logico: clave_de_campo} revisado por una persona")
    p_fi = sub.add_parser("fill", help="rellenar carácter por carácter y verificar (no envía)")
    source(p_fi)
    p_fi.add_argument("--data", required=True)
    p_fi.add_argument("--row", type=int, default=0)
    p_fi.add_argument("--profile", help="usar la correspondencia guardada en profiles/<nombre>.json")
    p_fi.add_argument("--mapping", help="archivo JSON {field_key: columna}")
    p_fi.add_argument("--delay-ms", type=int)
    p_fi.add_argument("--screenshot")
    p_fi.add_argument("--headed", action="store_true", help="mostrar la ventana del navegador")

    p_op = sub.add_parser("operaciones", help="OPERADOR: ver y revisar operaciones con resultado desconocido")
    sop = p_op.add_subparsers(dest="accion", required=True)
    sop.add_parser("listar", help="operaciones recientes y pendientes (sin datos personales)")
    p_rv = sop.add_parser("revisar", help="dar por revisada una operación incierta (o GENERAL) tras comprobarla")
    p_rv.add_argument("id", help="op_xxxxxxxxxxxx o GENERAL")
    p_rv.add_argument("--nota", required=True, help="qué comprobó (mín. 5 caracteres; no escriba datos personales)")
    p_rv.add_argument("--confirmar", required=True, help="repita el id exacto (o GENERAL)")
    p_rv.add_argument("--por", default="operador", help="quién revisa (rol o iniciales, sin datos personales)")

    args = ap.parse_args(argv)
    settings = get_settings()

    if args.cmd == "operaciones":
        return _operaciones(args, settings)

    if args.cmd == "preparar":
        from .integracion import principal
        return principal()

    if args.cmd == "ui":
        from .web.app import main as ui_main
        sys.argv = [sys.argv[0]]
        ui_main()
        return 0

    policy = UrlPolicy.load(settings.allowed_hosts_file)
    audit = AuditLog(settings.audit_path)
    try:
        if args.cmd == "aprobar-perfil":
            return _aprobar_perfil(args, settings, policy)
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

        explicit: dict[str, Any] = {}
        if args.profile:
            store = ProfileStore(settings.profiles_dir)
            profile = store.load(args.profile)
            rec = store.reconcile(profile, analysis)
            # H3: un perfil solo se usa si es del mismo sitio y la estructura no cambió; no se rellena nada si no
            if not rec["same_structure"] or profile.get("source_label") != analysis.source_label:
                _emit({"codigo": "PERFIL_NO_COINCIDE", "campos_faltan": rec["missing_fields"],
                       "campos_nuevos": rec["new_fields"], "escrito": 0})
                return 5
            explicit.update(rec["mapping"])
        if args.mapping:
            explicit.update(json.loads(Path(args.mapping).read_text(encoding="utf-8")))
        # H2: con perfil solo cuenta lo aprobado; sin perfil, solo propuestas «auto» o aprobadas expresamente
        mapping, pendientes = safe_cli_mapping(proposals, explicit, use_proposals=not args.profile)
        if pendientes:
            _emit({"codigo": "CORRESPONDENCIA_REQUIERE_REVISION", "campos": pendientes, "escrito": 0})
            return 4
        report = fill_form(session, mapping, rows[args.row], delay_ms=args.delay_ms, audit=audit,
                           screenshot_path=args.screenshot, allow_late_fields=not args.profile)
        _emit(report.to_dict())
        return 0 if report.ok else 2
    except UrlNotAllowed as exc:
        print(f"URL no autorizada: {exc}", file=sys.stderr)
        return 3
    finally:
        if "session" in locals() and session is not None:
            session.close()


def _aprobar_perfil(args: argparse.Namespace, settings: Any, policy: UrlPolicy) -> int:
    import re
    if not (re.fullmatch(r"frm_[a-z0-9_]{1,40}", args.formulario_id) and re.fullmatch(r"rev_[a-z0-9_]{1,40}", args.aprobado_por)
            and re.fullmatch(r"[a-z0-9_]{1,40}", args.perfil)):
        print("Identificadores con formato inválido (frm_..., rev_..., perfil en minúsculas/guion bajo).", file=sys.stderr)
        return 6
    campos = json.loads(Path(args.campos).read_text(encoding="utf-8"))
    if not isinstance(campos, dict) or not all(re.fullmatch(r"[a-z][a-z0-9_]{0,39}", k) and isinstance(v, str)
                                               for k, v in campos.items()):
        print("--campos debe ser {nombre_logico: clave_de_campo}.", file=sys.stderr)
        return 6
    session = BrowserSession(settings, headless=True)
    try:
        session.start()
        session.open_url(policy.check(args.url, args.authorized), policy.guard(args.authorized))
        analysis = session.inspect()
    finally:
        session.close()
    if analysis.safety.blocked:
        print("La página exige intervención humana; no se aprueba un perfil sobre ella.", file=sys.stderr)
        return 4
    path, digest = ProfileStore(settings.profiles_dir).save_integration(
        args.perfil, analysis, campos, formulario_id=args.formulario_id, url=args.url.split("?")[0].split("#")[0],
        aprobado_por_ref=args.aprobado_por)
    from .signature import structure_signature_v2
    _emit({"perfil": args.perfil, "archivo": str(path), "perfil_sha256": digest,
           "firma_estructura_esperada": structure_signature_v2(analysis.all_fields), "campos": sorted(campos)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def _operaciones(args: argparse.Namespace, settings: Any) -> int:
    """Revisión de un OPERADOR desde la línea de órdenes (no desde la interfaz de la persona).

    No toca Network Engine: sus marcas «revisar a mano» las resuelve el operador con sus propios mecanismos
    (``cauce revisiones resolver`` o el centro de revisiones de Monitor). Esta orden solo cierra el registro LOCAL de
    Forms, con nota obligatoria y confirmación escrita, y lo deja auditado. Nada se reenvía."""
    from .envio import _publica
    from .operaciones import RegistroOperaciones
    reg = RegistroOperaciones(settings.data_dir)
    audit = AuditLog(settings.audit_path)
    if args.accion == "listar":
        _emit({"revision_general": reg.general, "abiertas": [_publica(o) for o in reg.abiertas()],
               "recientes": [_publica(o) for o in reg.lista(20)]})
        return 0
    try:
        o = reg.revisar(args.id, args.nota, args.confirmar, args.por)
    except (LookupError, ValueError) as exc:
        print(f"No se revisó nada: {exc}", file=sys.stderr)
        return 1
    audit.log("operation_reviewed", detail=args.id, estado=o["estado"])
    _emit({"revisada": args.id})
    return 0
