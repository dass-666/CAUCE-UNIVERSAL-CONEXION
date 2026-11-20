"""Orquestador: analiza, rellena por pasadas y verifica. Nunca envía.

Relleno por pasadas: tras cada interacción con un desplegable, casilla o radio
se vuelve a escanear la página, porque muchos formularios muestran campos
nuevos según lo elegido. Los campos que aparecen durante el relleno y que NO
estaban en la revisión humana solo se rellenan si la correspondencia
automática tiene confianza alta; si no, se informan como pendientes.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .audit import AuditLog
from .matcher import AUTO_THRESHOLD, match_fields
from .models import CHOICE_TYPES, FieldDescriptor, FieldResult, FillReport
from .page_inspector import BrowserSession
from .sequential_typer import FillError, SequentialTyper
from .validator import check_confirmations, verify_field

MAX_PASSES = 12


def fill_form(session: BrowserSession, mapping: dict[str, Optional[str]], row: dict[str, str],
              *, delay_ms: Optional[int] = None, audit: Optional[AuditLog] = None,
              form_key: Optional[str] = None, allow_late_fields: bool = True,
              screenshot_path: Optional[str] = None) -> FillReport:
    """Rellena la página cargada en `session` con `row` según `mapping` {field_key: columna}.

    No pulsa ningún botón de envío ni la tecla Enter en campos de una línea.
    """
    audit = audit or AuditLog(None)
    t0 = time.monotonic()
    typer = SequentialTyper(session.page, session.settings.typing_delay_ms if delay_ms is None else delay_ms)

    analysis = session.inspect()
    report = FillReport(ok=False, stopped_before_submit=True)
    session.submit_armed = False        # un envío posterior exige ESTE relleno revisado (H1)
    if analysis.safety.blocked:
        report.blocked, report.blocked_reasons = True, analysis.safety.reasons
        audit.log("fill_blocked", reasons=analysis.safety.reasons, source_label=analysis.source_label)
        return report

    columns = list(row.keys())
    used_cols = {c for c in mapping.values() if c}
    done: dict[str, FieldResult] = {}
    all_fields: dict[str, FieldDescriptor] = {}
    late_pending: list[FieldResult] = []

    for _ in range(MAX_PASSES):
        forms = [fm for fm in analysis.forms if form_key is None or fm.key == form_key]
        fields = [f for fm in forms for f in fm.fields]
        for f in fields:
            all_fields[f.key] = f
        rescan = False
        progressed = False
        for f in fields:
            if f.key in done or not f.fillable:
                continue
            late = f.key not in mapping
            col = mapping.get(f.key)
            if late:
                if not allow_late_fields:
                    continue
                if f.confirms and f.confirms in mapping:
                    col = mapping[f.confirms]
                else:
                    m = next(iter(match_fields([f], [c for c in columns if c not in used_cols])), None)
                    if m is None or not m.column or m.confidence < AUTO_THRESHOLD:
                        if f.visible and f.key not in {r.field_key for r in late_pending}:
                            late_pending.append(FieldResult(f.key, f.label, f.type, "omitido", "omitido",
                                                            detail="campo aparecido durante el relleno sin "
                                                                   "correspondencia segura: revisar",
                                                            discovered_late=True))
                        continue
                    col = m.column
                    used_cols.add(col)
                mapping[f.key] = col
            if not col:
                continue
            if not f.visible:
                continue  # puede hacerse visible más adelante
            value = row.get(col, "")
            res = FieldResult(f.key, f.label, f.type, "omitido", "omitido", expected=value, discovered_late=late)
            if value == "" and f.type != "checkbox":
                res.detail = "la fila no tiene dato para esta columna"
                done[f.key] = res
                continue
            try:
                out = typer.apply(f, value)
                res.method, res.keystrokes = out["method"], out["keystrokes"]
                res.expected = out["applied"]
                if out.get("insert_text_fallbacks"):
                    res.detail = (f"{out['insert_text_fallbacks']} carácter(es) enviados con insertText "
                                  "(sin keydown/keyup): navegador sin CDP")
                verify_field(session.page, f, res, out["applied"])
            except FillError as exc:
                res.status, res.detail = "error", str(exc)
            except Exception as exc:  # error de Playwright: campo tapado, desaparecido, etc.
                res.status, res.detail = "error", f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
            done[f.key] = res
            progressed = True
            audit.log("field_filled", field_key=f.key, type=f.type, method=res.method,
                      status=res.status, chars=res.keystrokes)
            if f.type in CHOICE_TYPES:
                if session.settle() > 0:
                    rescan = True
                    break
        if rescan:
            analysis = session.inspect()
            continue
        if not progressed:
            break
        analysis = session.inspect()

    # Verificación final: la página pudo reformatear valores al perder el foco
    final_fields = {f.key: f for f in session.inspect().all_fields}
    for key, res in done.items():
        if res.method != "omitido" and key in final_fields:
            verify_field(session.page, final_fields[key], res, res.expected)

    late_keys = {r.field_key for r in late_pending}
    report.results = list(done.values()) + [r for r in late_pending if r.field_key not in done]
    report.confirmations = check_confirmations(list(all_fields.values()), done)
    report.submit_buttons = [b for fm in analysis.forms for b in fm.submit_buttons]
    filled = [r for r in done.values() if r.status != "omitido"]  # incluye errores
    report.ok = bool(filled) and all(r.status in ("ok", "ok_normalizado") for r in filled) \
        and all(c["match"] for c in report.confirmations) and not (late_keys - set(done))
    report.duration_ms = int((time.monotonic() - t0) * 1000)
    session.observe_submits()
    session.submit_armed = True
    if screenshot_path:
        report.screenshot = session.screenshot(screenshot_path)
    audit.log("fill_done", ok=report.ok, count=len(filled), duration_ms=report.duration_ms,
              stopped_before_submit=True, confirmations_ok=all(c["match"] for c in report.confirmations),
              submit_buttons=report.submit_buttons)
    return report


def safe_cli_mapping(proposals: list[Any], explicit: dict[str, Optional[str]], *,
                     use_proposals: bool = True) -> tuple[dict[str, Optional[str]], list[str]]:
    """H2: correspondencias que el CLI puede aplicar sin revisión humana nueva.

    - Solo propuestas con estado `auto` (si `use_proposals`) más las aprobadas expresamente (`explicit`).
    - Una propuesta `revisar` con columna que no esté aprobada expresamente es AMBIGUA: se devuelve en
      `pendientes` y el llamador debe detenerse antes de escribir nada.
    """
    mapping: dict[str, Optional[str]] = {}
    pendientes: list[str] = []
    if use_proposals:
        for m in proposals:
            if not m.column:
                continue
            if m.status == "auto":
                mapping[m.field_key] = m.column
            elif m.status == "revisar" and m.field_key not in explicit:
                pendientes.append(m.field_key)
    mapping.update(explicit)
    return mapping, sorted(pendientes)


def submit_with_human_confirmation(session: BrowserSession, confirmation_phrase: str,
                                   audit: Optional[AuditLog] = None, button_text: str = "") -> dict[str, Any]:
    """Pulsa el botón de envío SOLO si una persona escribió exactamente "ENVIAR" (interfaz local, no integración).

    Cambios respecto de v0.1.0 (H1):
    - La frase debe ser exacta (antes se aceptaba `" ENVIAR\n"` por usar `strip()`).
    - Exige un relleno completado en esta página y es de UN SOLO USO: tras el clic hay que volver a rellenar.
    - No existe en modo de integración (`session.integration_mode`).
    - Distingue tres hechos: `clic_solicitado` (siempre True si llegó a pulsar), `envio_observado` (el navegador
      disparó el evento `submit`; si la validación HTML5 lo bloqueó, es False) y `confirmado_recepcion`
      (SIEMPRE False: el conector no puede saber si el sitio recibió el formulario; eso exige un folio del portal).
    - `submitted` se conserva por compatibilidad, pero ahora significa «envío observado», no «se hizo clic».
    """
    audit = audit or AuditLog(None)
    if confirmation_phrase != "ENVIAR":
        raise PermissionError("Para enviar, una persona debe escribir exactamente ENVIAR.")
    if session.integration_mode:
        raise PermissionError("Modo de integración: el conector no envía formularios.")
    if not session.submit_armed:
        raise PermissionError("No hay un relleno revisado pendiente de envío (aprobación de un solo uso).")
    analysis = session.inspect()
    if analysis.safety.blocked:
        raise PermissionError("La página requiere intervención humana directa: envíe desde el navegador.")
    loc = session.page.locator("button[type=submit], input[type=submit], form button:not([type])")
    if button_text:
        loc = loc.filter(has_text=button_text)
    if loc.count() == 0:
        raise LookupError("No se encontró un botón de envío.")
    session.observe_submits()
    session.submit_armed = False        # se consume antes del clic: ni un fallo permite reintentar con la misma aprobación
    before = len(session.submit_events)
    loc.first.click()
    session.settle()
    observed = len(session.submit_events) > before
    audit.log("human_submit", ok=observed, submit_observed=observed, source_label=analysis.source_label)
    return {"clic_solicitado": True, "envio_observado": observed, "confirmado_recepcion": False,
            "submitted": observed, "url_after": session.page.url.split("?")[0],
            "nota": "El conector no confirma la recepción: eso requiere el comprobante del portal."}
