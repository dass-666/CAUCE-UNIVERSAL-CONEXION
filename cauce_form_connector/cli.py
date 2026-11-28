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
    p_au = sub.add_parser("autorrellenar", help="Monitor -> conector: expediente + perfil de formulario -> ventana visible "
                                                "rellenada y verificada, sin enviar")
    p_au.add_argument("--formulario", required=True, help="perfil de formulario configurable (config/formularios/*.json)")
    p_au.add_argument("--expediente", required=True, help="JSON del expediente (el conector solo lee los campos del perfil)")
    p_au.add_argument("--url", help="URL del formulario (sustituye a la del perfil)")
    p_au.add_argument("--puerto", type=int, help="puerto local para formularios de laboratorio ({puerto})")
    p_au.add_argument("--aprobar-perfil", metavar="REV_ID", help="paso humano: aprobar/actualizar el perfil (rev_xxx) "
                                                                 "tras revisar los campos del perfil de formulario")
    p_au.add_argument("--authorized", action="store_true", help="declaro autorización sobre la URL (sitios externos)")
    p_au.add_argument("--autorizacion", metavar="AUT_REF", help="(opcional) referencia de autorización del sitio (aut_xxx); "
                                                               "si se omite, se registra aut_lista_dominios_local")
    p_au.add_argument("--manuales-stdin", action="store_true", help="leer por stdin un JSON {campo: valor} con los datos "
                      "de captura manual (@manual) de ESTA ejecución; no se guardan ni se imprimen")
    p_au.add_argument("--espera-s", type=int, default=300, help="segundos que se mantiene la ventana para la revisión")
    p_au.add_argument("--json", action="store_true", help="salida JSON (respuesta del contrato + informe) en vez de texto")
    p_az = sub.add_parser("analizar-formulario", help="SOLO LECTURA: analiza un formulario autorizado y genera informe "
                                                      "técnico, perfil borrador y vista previa para revisión humana")
    g_az = p_az.add_mutually_exclusive_group(required=True)
    g_az.add_argument("--url", help="URL autorizada del formulario")
    g_az.add_argument("--html", help="archivo .html local (pruebas)")
    p_az.add_argument("--authorized", action="store_true", help="declaro autorización sobre la URL (sitios externos)")
    p_az.add_argument("--expediente", default="sample_data/expediente_monitor_sintetico.json",
                      help="expediente SINTÉTICO con el modelo de Monitor (solo se mira si hay dato, nunca se muestra)")
    p_az.add_argument("--salida", help="carpeta de resultados (por defecto informes/analisis_<formulario>_<fecha>)")
    p_az.add_argument("--nombre", default="", help="nombre legible del formulario")
    p_az.add_argument("--sondeo", choices=("pasivo", "clic"), default="pasivo",
                      help="pasivo (por defecto): solo lee la página, sin clics ni teclas. clic: además pulsa con el ratón "
                           "los controles que la lectura no resolvió (sin teclado, escrituras bloqueadas, corta si algo cambia)")
    p_az.add_argument("--perfil-previo", help="perfil ya revisado de esta misma URL: la vista previa conserva sus rutas, "
                                              "formatos de fecha y equivalencias para no repetir la configuración")
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
    p_rv.add_argument("--resultado", choices=("recibido", "no_recibido", "sin_determinar"),
                      help="obligatorio para una operación: recibido (sigue bloqueado), no_recibido (comprobado que no "
                           "llegó: se podrá enviar otra vez) o sin_determinar (quita la alerta, sigue bloqueado)")
    p_es = sub.add_parser("estado", help="OPERADOR: respaldar o restaurar la carpeta de estado de Forms")
    ses = p_es.add_subparsers(dest="accion", required=True)
    p_rs = ses.add_parser("respaldar", help="copia protegida y verificable de registro, clave, puesto y configuración")
    p_rs.add_argument("--destino", required=True, help="carpeta donde dejar el respaldo (se protege como la de estado)")
    p_vr = ses.add_parser("verificar", help="comprobar la integridad de un respaldo sin restaurarlo")
    p_vr.add_argument("respaldo", help="archivo respaldo-forms-*.zip")
    p_rt = ses.add_parser("restaurar", help="restaurar un respaldo (nada se envía hasta revisar GENERAL)")
    p_rt.add_argument("respaldo", help="archivo respaldo-forms-*.zip")
    p_rt.add_argument("--confirmar", required=True, help="escriba exactamente: RESTAURAR")

    args = ap.parse_args(argv)
    settings = get_settings()

    if args.cmd == "operaciones":
        return _operaciones(args, settings)
    if args.cmd == "estado":
        from .respaldo import orden_estado
        return orden_estado(args, settings)

    if args.cmd == "preparar":
        from .integracion import principal
        return principal()

    if args.cmd == "autorrellenar":
        return _autorrellenar(args, settings)

    if args.cmd == "analizar-formulario":
        return _analizar_formulario(args, settings)

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


def _autorrellenar(args: argparse.Namespace, settings: Any) -> int:
    import re
    from urllib.parse import urlsplit
    from . import puente_monitor as P
    from .url_policy import LOCAL_HOSTS
    try:
        cfg = P.cargar_formulario(args.formulario, url=args.url, puerto=args.puerto)
        expediente = json.loads(Path(args.expediente).read_text(encoding="utf-8"))
        # Sitios externos: en CADA ejecución el dominio debe estar en la lista de permitidos y declararse con
        # --authorized. La referencia --autorizacion es opcional; si se da, debe tener formato aut_xxx y no ser la de
        # demostración. Sin ella, el registro indica que la autorización viene de la lista local de dominios.
        p = urlsplit(cfg["url"])
        externo = p.scheme in ("http", "https") and (p.hostname or "").lower() not in LOCAL_HOSTS
        UrlPolicy.load(settings.allowed_hosts_file).check(cfg["url"], args.authorized)
        aut_ref = args.autorizacion or "aut_lista_dominios_local"
        if externo and not (args.authorized and re.fullmatch(r"aut_[a-z0-9_]{1,40}", aut_ref) and aut_ref != "aut_demo"):
            print("URL no autorizada: para un sitio externo use --authorized (dominio en la lista de permitidos); "
                  "--autorizacion, si se indica, debe ser aut_xxx y no aut_demo.", file=sys.stderr)
            return 3
        if args.aprobar_perfil:
            info = P.aprobar_perfil(cfg, settings, aprobado_por=args.aprobar_perfil, autorizado=args.authorized)
            print(f"Perfil aprobado: {info['perfil']} (sha256 {info['perfil_sha256'][:12]}…, firma {info['firma']})", file=sys.stderr)
            for logico, cob in info.get("equivalencias", {}).items():
                print(f"  Tabla «{logico}» aprobada: {len(cob['codigos_cubiertos'])} códigos"
                      + (f"; sin fila (los completa la persona): {', '.join(cob['codigos_sin_fila'])}" if cob["codigos_sin_fila"] else ""),
                      file=sys.stderr)
        extra: dict[str, Any] = {}
        if externo:
            aprobado = (P.info_perfil(cfg, settings) or {}).get("aprobado_por")
            extra = {"autorizacion_ref": aut_ref, "otorgada_por": aprobado or "rev_sin_aprobar"}
        manuales = None
        if args.manuales_stdin:
            manuales = json.loads(sys.stdin.read() or "{}")
            if not isinstance(manuales, dict) or not all(isinstance(v, str) for v in manuales.values()):
                raise P.ConfigInvalida("Los datos de captura manual deben ser un objeto JSON {campo: texto}.")
        r = P.autorrellenar(cfg, expediente, settings, espera_revision_s=args.espera_s, manuales=manuales, **extra)
    except UrlNotAllowed as exc:          # antes que ValueError: UrlNotAllowed hereda de ValueError
        print(f"URL no autorizada: {exc}", file=sys.stderr)
        return 3
    except P.ConfigInvalida as exc:       # mensajes propios, sin datos personales: se muestran para poder corregir
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 6
    except (OSError, ValueError) as exc:
        print(f"Error de configuración: {type(exc).__name__}", file=sys.stderr)
        return 6
    if args.json:
        _emit({"salida": r["salida"], "salida_contrato": r["salida_contrato"], "respuesta": r["respuesta"],
               "informe": r["informe"]})
    else:
        print(P.texto_informe(r["informe"]))
    return r["salida"]


def _analizar_formulario(args: argparse.Namespace, settings: Any) -> int:
    """Códigos: 0 informe generado, 3 URL no autorizada, 4 informe generado pero la página exige intervención
    humana (CAPTCHA/uso humano), 6 error de entrada."""
    from datetime import datetime
    from . import analizador_formularios as A
    url = Path(args.html).resolve().as_uri() if args.html else args.url
    expediente = None
    if args.expediente:
        try:
            expediente = json.loads(Path(args.expediente).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"No se pudo leer el expediente: {type(exc).__name__}", file=sys.stderr)
            return 6
    try:
        inf = A.analizar_url(url, autorizado=args.authorized, settings=settings, expediente=expediente, nombre=args.nombre,
                             sondeo=args.sondeo)
    except UrlNotAllowed as exc:
        print(f"URL no autorizada: {exc}", file=sys.stderr)
        return 3
    carpeta = args.salida or str(Path("informes") / ("analisis_" + inf["perfil_borrador"]["perfil"].removeprefix("perfil_")
                                                     + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")))
    if args.perfil_previo:
        try:
            previo = json.loads(Path(args.perfil_previo).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previo = None
        if A.adjuntar_perfil_previo(inf, previo):
            print("Se conservó la configuración del perfil anterior de esta URL (revísela en la vista previa).")
        else:
            print("Aviso: el perfil anterior no es de esta URL o no se pudo leer; no se usó.")
    rutas = A.guardar(inf, carpeta)
    r = inf["resumen"]
    print(f"Formulario: {inf['url']}")
    print(f"Campos: {r['campos']} · automáticos: {r['automatico']} · revisar: {r['revisar']} · humano: {r['humano']} · "
          f"ignorados: {r['ignorado']}")
    if inf["bloqueo_humano"]["blocked"]:
        print("ATENCIÓN: la página exige intervención humana; el perfil no se podrá aprobar.")
    for a in inf["advertencias"]:
        print(f"Aviso: {a}")
    print("Resultados (no se envió nada):")
    for k, v in rutas.items():
        print(f"  {k}: {v}")
    return 4 if inf["bloqueo_humano"]["blocked"] else 0


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
    if args.id != "GENERAL" and not args.resultado:
        print("No se revisó nada: indique --resultado recibido | no_recibido | sin_determinar", file=sys.stderr)
        return 1
    try:
        o = reg.revisar(args.id, args.nota, args.confirmar, args.por, args.resultado or "sin_determinar")
    except (LookupError, ValueError) as exc:
        print(f"No se revisó nada: {exc}", file=sys.stderr)
        return 1
    audit.log("operation_reviewed", detail=args.id, estado=o["estado"], codigo=args.resultado)
    _emit({"revisada": args.id, "resultado": args.resultado})
    return 0
