"""Envío PROTEGIDO de una acción confirmada por una persona (bloque C).

Orden estricto (``enviar_protegido``), cada paso con prueba:

1. Comprobaciones previas de ``submit_with_human_confirmation`` (frase exacta, relleno armado de un solo uso, sin
   CAPTCHA, botón presente) y del registro (marca general, mismo expediente abierto o ya confirmado).
2. La aprobación se CONSUME y la operación se guarda en disco como ``en_curso`` ANTES del clic. Si el registro no se
   puede escribir, no se pulsa nada.
3. Aviso «en curso» a Network Engine (si está configurado), esperando como mucho 3 s. Si no llega, se envía igual
   (el motor solo observa) y queda anotado.
4. UN clic. Nunca otro. Ni si falla, ni si el portal tarda, ni si vuelve la red.
5. Resultado:
   - ``confirmada`` solo si el PORTAL PROPIO DE LABORATORIO confirma por su API (``/api/acuse/<token>``) el folio que
     mostró la página de acuse. Un clic o un evento ``submit`` observados NO son una recepción.
   - ``no_enviada`` solo si consta que no salió nada: sin evento ``submit``, sin petición POST/PUT/PATCH y sin navegar.
   - ``rechazada`` si el portal propio respondió explícitamente que no lo recibió (página ``cauce-rechazo``) y su API
     lo corrobora.
   - ``incierta`` en cualquier otro caso: excepción, navegador cerrado, corte, tiempo agotado, portal ajeno sin acuse
     verificable, verificación fallida.
6. Aviso final a Network Engine: ``idle`` si el resultado es seguro; si no, ``error`` + ``in_progress`` (el motor lo
   marca ``requires_human``) hasta que lo confirme.

La verificación HTTP del acuse solo se hace contra 127.0.0.1/localhost (el portal propio). Nunca contra terceros.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import quote, urljoin, urlsplit

from .audit import AuditLog
from .operaciones import (CONFIRMADA, INCIERTA, NO_ENVIADA, RECHAZADA, OperacionBloqueada,
                          RegistroOperaciones)
from .page_inspector import BrowserSession

ACUSE_ESPERA_S = 15.0
VERIFICACION_TIMEOUT_S = 5.0
_LOCAL = ("127.0.0.1", "localhost")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_FOLIO_RE = re.compile(r"^[A-Z0-9-]{4,32}$")

LEER_PORTAL_JS = """() => {
  const m = (n) => { const e = document.querySelector(`meta[name="${n}"]`); return e ? e.getAttribute('content') : null; };
  const t = document.querySelector('input[name="cauce_token_formulario"]');
  return {laboratorio: m('cauce-portal-laboratorio') === '1', api: m('cauce-acuse-api'), token: t ? t.value : null};
}"""
LEER_ACUSE_JS = """() => {
  const m = (n) => { const e = document.querySelector(`meta[name="${n}"]`); return e ? e.getAttribute('content') : null; };
  return {folio: m('cauce-acuse'), token: m('cauce-acuse-token'), rechazo: m('cauce-rechazo')};
}"""


def _es_local(url: str) -> bool:
    u = urlsplit(url)
    return u.scheme == "http" and u.hostname in _LOCAL


def verificar_acuse(api_base: str, token: str, timeout: float = VERIFICACION_TIMEOUT_S) -> dict[str, Any]:
    """Consulta de SOLO LECTURA al portal propio. Devuelve {"recibido": bool|None, "folio": str|None, "error": str|None}."""
    if not _es_local(api_base) or not _TOKEN_RE.match(token or ""):
        return {"recibido": None, "folio": None, "error": "verificacion_no_permitida"}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(api_base.rstrip("/") + "/" + quote(token), timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as e:
        return {"recibido": None, "folio": None, "error": type(e).__name__}
    folio = d.get("folio") if isinstance(d.get("folio"), str) and _FOLIO_RE.match(d.get("folio") or "") else None
    if d.get("recibido") is True and folio:
        return {"recibido": True, "folio": folio, "error": None}
    if d.get("recibido") is False:
        return {"recibido": False, "folio": None, "error": None}
    return {"recibido": None, "folio": None, "error": "respuesta_no_valida"}


def _esperar_acuse(session: BrowserSession, limite_s: float, documentos: list) -> dict[str, Any]:
    """Espera la página de resultado SIN bloquearse: ``page.evaluate`` se queda esperando mientras una navegación está
    pendiente (medido: 14 s con el portal lento), así que solo se lee la página cuando ya llegó la respuesta del
    documento principal. Mientras, se espera con ``wait_for_timeout`` (que sí atiende los eventos del navegador)."""
    t0 = time.monotonic()
    ultimo: dict[str, Any] = {}
    while time.monotonic() - t0 < limite_s:
        if session.page is None or session.page.is_closed():
            return {"cerrada": True}
        if documentos:
            try:
                ultimo = session.page.evaluate(LEER_ACUSE_JS) or {}
            except Exception:  # noqa: BLE001  navegación en marcha o página cerrada
                if session.page is None or session.page.is_closed():
                    return {"cerrada": True}
                ultimo = {}
            if ultimo.get("folio") or ultimo.get("rechazo"):
                return ultimo
        try:
            session.page.wait_for_timeout(250)
        except Exception:  # noqa: BLE001
            return {"cerrada": True}
    return ultimo or {}


def enviar_protegido(session: BrowserSession, confirmation_phrase: str, registro: RegistroOperaciones,
                     huella: str, audit: Optional[AuditLog] = None, diagnostico: Any = None,
                     button_text: str = "", acuse_espera_s: float = ACUSE_ESPERA_S) -> dict[str, Any]:
    audit = audit or AuditLog(None)
    # 1. comprobaciones previas (mismas reglas H1 que submit_with_human_confirmation)
    if confirmation_phrase != "ENVIAR":
        raise PermissionError("Para enviar, una persona debe escribir exactamente ENVIAR.")
    if session.integration_mode:
        raise PermissionError("Modo de integración: el conector no envía formularios.")
    if not session.submit_armed:
        raise PermissionError("No hay un relleno revisado pendiente de envío (aprobación de un solo uso).")
    registro.comprobar(huella)                      # lanza OperacionBloqueada (subclase de PermissionError)
    analysis = session.inspect()
    if analysis.safety.blocked:
        raise PermissionError("La página requiere intervención humana directa: envíe desde el navegador.")
    loc = session.page.locator("button[type=submit], input[type=submit], form button:not([type])")
    if button_text:
        loc = loc.filter(has_text=button_text)
    if loc.count() == 0:
        raise LookupError("No se encontró un botón de envío.")
    portal = session.page.evaluate(LEER_PORTAL_JS) or {}
    url_form = session.page.url
    api_base = urljoin(url_form, portal["api"]) if portal.get("laboratorio") and portal.get("api") else None
    if api_base and not _es_local(api_base):
        api_base = None                              # nunca se verifica contra un tercero
    token = portal.get("token") if api_base and _TOKEN_RE.match(portal.get("token") or "") else None

    # 2. consumir la aprobación y guardar «en curso» ANTES del clic
    session.observe_submits()
    session.submit_armed = False
    sesion_ne = getattr(getattr(diagnostico, "c", None), "session_id", None) if diagnostico else None
    try:
        op = registro.iniciar(huella, analysis.source_label, token, sesion_ne, api_base if token else None)
    except OSError:
        audit.log("human_submit_refused", codigo="registro_no_escribible")
        raise PermissionError("No se pudo guardar el registro de la operación: por seguridad no se envía nada.")
    audit.log("operation_started", detail=op["id"], source_label=analysis.source_label)

    # 3. aviso «en curso» (como mucho 3 s; el motor solo observa)
    aviso = diagnostico.operacion_inicio() if diagnostico is not None and diagnostico.configurado else None
    registro.actualizar(op["id"], aviso_en_curso=aviso)

    # 4. UN clic
    escrituras: list[str] = []
    navego = []
    documentos: list[int] = []                       # respuestas del documento principal tras el clic

    def _req(r) -> None:
        if r.method in ("POST", "PUT", "PATCH", "DELETE"):
            escrituras.append(r.method)

    def _nav(fr) -> None:
        if fr == session.page.main_frame:
            navego.append(1)

    def _resp(r) -> None:
        try:
            if r.request.resource_type == "document" and r.frame == session.page.main_frame:
                documentos.append(r.status)
        except Exception:  # noqa: BLE001
            pass

    before = len(session.submit_events)
    estado, motivo, folio, excepcion = INCIERTA, "resultado_desconocido", None, None
    try:
        session.page.on("request", _req)
        session.page.on("framenavigated", _nav)
        session.page.on("response", _resp)
        loc.first.click(timeout=10_000, no_wait_after=True)   # el clic no espera a la navegación: la espera es nuestra
        # pausa corta que atiende eventos (submit, request); NO se usa settle(): evaluate se bloquea si hay navegación
        session.page.wait_for_timeout(400)
        observed = len(session.submit_events) > before
        registro.actualizar(op["id"], envio_observado=observed)
        # 5. resultado
        if not observed and not escrituras and not navego:
            estado, motivo = NO_ENVIADA, "sin_envio_observado"   # p. ej. la validación del navegador lo bloqueó
        elif api_base and token:
            pagina = _esperar_acuse(session, acuse_espera_s, documentos)
            if pagina.get("cerrada"):
                motivo = "navegador_cerrado_durante_envio"
            else:
                v = verificar_acuse(api_base, token)
                if v["recibido"] is True and (not pagina.get("folio") or pagina["folio"] == v["folio"]):
                    estado, motivo, folio = CONFIRMADA, "acuse_verificado", v["folio"]
                elif v["recibido"] is False and pagina.get("rechazo"):
                    estado, motivo = RECHAZADA, "portal_rechazo_" + re.sub(r"[^a-z_]", "", pagina["rechazo"])[:30]
                elif v["recibido"] is False:
                    motivo = "portal_sin_constancia"      # puede llegar tarde: no se da por no recibido
                else:
                    motivo = "verificacion_fallida_" + str(v["error"])[:30]
        else:
            motivo = "envio_observado_sin_acuse_verificable" if observed else "peticion_sin_acuse_verificable"
    except Exception as e:  # noqa: BLE001  navegador cerrado, página colgada, tiempo agotado: resultado desconocido
        excepcion = e
        motivo = "interrumpida_" + type(e).__name__[:40]
    finally:
        for ev, fn in (("request", _req), ("framenavigated", _nav), ("response", _resp)):
            try:
                session.page.remove_listener(ev, fn)
            except Exception:  # noqa: BLE001
                pass
    cerrada = registro.cerrar(op["id"], estado, motivo, folio)
    seguro = estado in (CONFIRMADA, NO_ENVIADA, RECHAZADA)
    # 6. aviso final al motor (nunca bloquea más de 3 s)
    if diagnostico is not None and diagnostico.configurado:
        diagnostico.operacion_fin(resultado_seguro=seguro)
    audit.log("human_submit", ok=estado == CONFIRMADA, submit_observed=bool(cerrada.get("envio_observado")),
              estado=estado, codigo=motivo, detail=op["id"], source_label=analysis.source_label)
    out = {"clic_solicitado": True, "envio_observado": bool(cerrada.get("envio_observado")),
           "confirmado_recepcion": estado == CONFIRMADA, "submitted": bool(cerrada.get("envio_observado")),
           "operacion": _publica(cerrada), "url_after": _url_segura(session),
           "nota": {CONFIRMADA: "Recepción confirmada por el acuse verificado del portal de laboratorio.",
                    NO_ENVIADA: "No salió ningún envío (p. ej. la página lo bloqueó). Puede volver a rellenar.",
                    RECHAZADA: "El portal respondió que no lo recibió. No se reintenta solo.",
                    INCIERTA: "Resultado desconocido: NO se reintenta. Debe revisarse (acuse del portal o un operador)."}[estado]}
    if excepcion is not None:
        out["error"] = type(excepcion).__name__
    return out


def _url_segura(session: BrowserSession) -> str:
    try:
        return session.page.url.split("?")[0]
    except Exception:  # noqa: BLE001
        return ""


def _publica(o: dict) -> dict:
    """Vista de una operación para la interfaz (sin huella ni token)."""
    return {k: o.get(k) for k in ("id", "creada", "actualizada", "estado", "motivo", "origen", "folio", "sesion_ne",
                                  "aviso_en_curso", "envio_observado", "revision")} | \
           {"verificable": bool(o.get("token_portal"))}


def verificar_operacion(registro: RegistroOperaciones, oid: str) -> dict[str, Any]:
    """Acción EXPLÍCITA de la persona: comprobar en el portal propio si una operación incierta llegó.

    Solo puede llevarla a ``confirmada`` (con folio). «No consta» la deja ``incierta``: un envío puede llegar tarde y la
    ausencia de constancia no autoriza a repetir. Nunca reenvía nada."""
    o = registro.get(oid)
    if o is None:
        raise LookupError("Operación inexistente.")
    if o["estado"] != INCIERTA:
        return {"operacion": _publica(o), "cambio": False}
    if not o.get("token_portal") or not o.get("api_acuse"):
        return {"operacion": _publica(o), "cambio": False, "motivo": "no_verificable"}
    v = verificar_acuse(o["api_acuse"], o["token_portal"])
    if v["recibido"] is True:
        return {"operacion": _publica(registro.confirmar_por_acuse(oid, v["folio"])), "cambio": True}
    return {"operacion": _publica(o), "cambio": False,
            "motivo": "portal_sin_constancia" if v["recibido"] is False else f"verificacion_fallida_{v['error']}"}


__all__ = ["enviar_protegido", "verificar_acuse", "verificar_operacion", "OperacionBloqueada"]
