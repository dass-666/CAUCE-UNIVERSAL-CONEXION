"""Modo de integración `cauce.conector.v1`: UNA solicitud por proceso, JSON por stdin, JSON por stdout.

Implementa el contrato de Cauce Monitor (commit 9e7f88d, ver `contrato_v1/PIN.txt` y
`docs/CONTRATO_CAUCE_CONECTOR_V1.md`). Reglas de este modo, todas comprobadas por pruebas:

- No hay envío: ni se importa `submit_with_human_confirmation` ni existe forma de pedirlo. Si una persona pulsa el
  botón del formulario en la ventana visible, solo se OBSERVA (indicio, no confirmación de recepción).
- Solo se escriben campos del perfil APROBADO cuya huella SHA-256 coincide con la que fijó Monitor. Nada se completa
  con sugerencias del motor de correspondencias.
- Cada `solicitud_id` se consume una sola vez (archivo con el id, sin datos personales) ANTES de abrir el navegador.
- La salida nunca contiene valores personales ni texto de excepciones; stderr solo lleva códigos fijos.
- No se escribe ningún archivo con los datos (sin capturas, sin auditoría de valores, sin temporales propios).
- Registro de operaciones (consolidación): si la persona envía desde la ventana (botón, Intro o JavaScript de la
  página), queda una operación ``incierta`` en el registro durable de Forms, con el ``solicitud_id`` para cruzarla con
  Monitor (solo huella HMAC y id opacos, sin valores). Un expediente con una operación que bloquea (incierta,
  confirmada, revisada sin «no_recibido») o con revisión general pendiente NO se prepara otra vez: se responde
  ``detenido_intervencion_humana`` / ``INTERVENCION_HUMANA_REQUERIDA`` (el contrato 9e7f88d no tiene un código propio).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from . import contrato_v1 as C
from .audit import AuditLog
from .config_store import INTEGRATION_PROFILE_VERSION, ProfileStore
from .engine import fill_form
from .operaciones import OperacionBloqueada, RegistroOperaciones
from .page_inspector import BrowserSession
from .settings import Settings, get_settings
from .signature import diff_structure, structure_signature_v2
from .url_policy import UrlNotAllowed, UrlPolicy, safe_url_label

MAX_ENTRADA = 1_000_000
_VACIO = {"faltan": [], "nuevos": [], "modificados": []}
POLL_S = 0.2
# La activación transitoria del navegador dura ~5 s tras una pulsación; el propio tecleo del conector la produce.
# Un `submit` ocurrido durante esa ventana tras el relleno no se atribuye a una persona (sub-reporta; nunca sobre-reporta).
GRACIA_ACTIVACION_S = 6.0
_METODO = {"teclado_secuencial": "teclado_secuencial", "check": "check", "uncheck": "check",
           "select_option": "seleccion", "clic_lista_personalizada": "seleccion", "fill_nativo": "fecha_nativa", "omitido": "teclado_secuencial"}


class _Parar(Exception):
    """Salida controlada: estado + códigos del contrato (nunca texto con datos)."""

    def __init__(self, estado: str, *codigos: str, formulario: Optional[dict] = None):
        super().__init__(estado)
        self.estado, self.codigos, self.formulario = estado, codigos, formulario


def _misma_pagina(a: str, b: str) -> bool:
    x, y = urlsplit(a), urlsplit(b)
    return (x.scheme, x.hostname, x.port, x.path or "/") == (y.scheme, y.hostname, y.port, y.path or "/")


class _Corrida:
    def __init__(self, sol: dict, t0: float):
        self.sol, self.t0 = sol, t0
        self.formulario = {"origen": sol["formulario"]["url"], "firma_estructura": "",
                           "estructura_coincide": False, "cambios": dict(_VACIO)}

    def respuesta(self, estado: str, codigos: tuple[str, ...] = (), *, campos=None, confirmaciones=None,
                  revision=("no_realizada", 0), observado: bool = False, huella: str = "",
                  formulario: Optional[dict] = None) -> dict:
        return {"contrato": C.VERSION, "solicitud_id": self.sol["solicitud_id"], "estado": estado,
                "formulario": formulario or self.formulario, "campos": campos or [],
                "confirmaciones": confirmaciones or [], "errores": [{"codigo": c} for c in codigos],
                "revision_humana": {"requerida": True, "resultado": revision[0], "duracion_s": int(revision[1])},
                "envio": {"realizado_por_conector": False, "envio_humano_observado": observado},
                "huella_preparacion": huella,
                "duracion_ms": min(3_600_000, int((time.monotonic() - self.t0) * 1000))}


_ESTADO_POR_PARADA = {"solicitud": "rechazado_solicitud_invalida", "autorizacion": "rechazado_no_autorizado"}


def _consumir_id(state_dir: Path, solicitud_id: str, ahora: datetime) -> None:
    """Un solo uso por `solicitud_id`. El nombre ya está validado (`sol_[0-9a-f]{16,32}`). Solo guarda la hora."""
    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(state_dir / solicitud_id, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise _Parar("rechazado_solicitud_invalida", "SOLICITUD_REPETIDA")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(ahora.isoformat(timespec="seconds"))


def _listas_texto_aprobadas(perfil: dict) -> dict:
    from .models import FieldOption
    out: dict = {}
    for f in perfil.get("fields", []):
        if isinstance(f, dict) and f.get("type") == "custom-select" and f.get("tag") == "input" and f.get("key"):
            out[f["key"]] = [FieldOption(value=str(o.get("value", "")), label=str(o.get("label", "")), selected=False)
                             for o in f.get("options", []) if isinstance(o, dict) and o.get("value")]
    return out


def _cargar_perfil(sol: dict, settings: Settings) -> dict:
    """Perfil aprobado, con huella verificada sobre los MISMOS bytes que se interpretan (sin carrera)."""
    form = sol["formulario"]
    path = ProfileStore(settings.profiles_dir).path_of(form["perfil"])
    try:
        raw = path.read_bytes()
    except OSError:
        raise _Parar("rechazado_no_autorizado", "PERFIL_NO_COINCIDE")
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), form["perfil_sha256"]):
        raise _Parar("rechazado_no_autorizado", "PERFIL_NO_COINCIDE")          # perfil alterado o desactualizado
    try:
        perfil = json.loads(raw.decode("utf-8"))
        integ = perfil["integracion"]
        ok = (perfil.get("version") == INTEGRATION_PROFILE_VERSION and integ.get("aprobado") is True
              and integ["formulario_id"] == form["formulario_id"] and integ["url"] == form["url"]
              and perfil["structure_signature_v2"] == form["firma_estructura_esperada"]
              and isinstance(integ["campos"], dict) and isinstance(perfil["fields"], list))
    except (ValueError, KeyError, TypeError, AttributeError):
        ok = False
    if not ok:     # perfil v1 sin aprobar, de otro formulario/URL o con otra firma: se exige revisión previa
        raise _Parar("rechazado_no_autorizado", "PERFIL_NO_COINCIDE")
    if not set(sol["campos"]) <= set(integ["campos"]):
        raise _Parar("rechazado_solicitud_invalida", "CAMPO_NO_DECLARADO")
    # Campos con tabla de equivalencias aprobada: solo se admiten los valores de opción de esa tabla. Un código
    # de Monitor sin traducir (o cualquier otro texto) no llega al formulario, donde podría casar por parecido.
    equiv = integ.get("equivalencias") or {}
    for logico, valor in sol["campos"].items():
        if logico in equiv and valor not in set(equiv[logico].get("valores", {}).values()):
            raise _Parar("rechazado_solicitud_invalida", "SOLICITUD_MAL_FORMADA")
    return perfil


def _esperar_revision(sesion: BrowserSession, espera_s: int, desde: float,
                      gancho: Optional[Callable[[BrowserSession], None]] = None) -> tuple[str, float]:
    """(resultado, segundos). Observa; nunca hace clic. Resultados: envio_humano | cerrada_sin_envio | caducada | error.

    Un envío de la persona NO cierra la ventana: la persona sigue en ella (elige el día, confirma y lee el folio) sin
    volver a teclear los datos que Forms ya escribió. La revisión termina cuando la persona cierra la ventana o vence
    el plazo; si hubo envío humano, el resultado es `envio_humano` en ambos casos (también si el navegador se cierra
    entero o se cae después del envío: lo observado ya ocurrió)."""
    page = sesion.page
    estado = {"cerrada": False, "crash": False}
    page.on("close", lambda *_: estado.__setitem__("cerrada", True))
    page.on("crash", lambda *_: estado.__setitem__("crash", True))
    ini = time.monotonic()
    envio: Optional[float] = None
    while True:
        t = time.monotonic() - ini
        if envio is None and any(g and ts >= desde + GRACIA_ACTIVACION_S for g, ts in sesion.submit_events):
            envio = t                                                    # gesto de usuario real: se anota y se sigue
        if estado["crash"] or not sesion._browser.is_connected():
            return ("envio_humano", t) if envio is not None else ("error", t)
        if estado["cerrada"]:
            return ("envio_humano" if envio is not None else "cerrada_sin_envio"), t
        if t >= espera_s:
            return ("envio_humano" if envio is not None else "caducada"), float(espera_s)
        try:
            if gancho:
                gancho(sesion)             # solo pruebas: simula a la persona
            page.wait_for_timeout(int(POLL_S * 1000))
        except Exception:
            estado["cerrada"] = estado["cerrada"] or page.is_closed()
            if not estado["cerrada"] and not estado["crash"] and envio is None:
                return "error", time.monotonic() - ini


def _mostrar_verificacion(campos: list, confirmaciones: list, por_clave: dict, a_clave: dict) -> None:
    """Resultado de la verificación por stderr MIENTRAS la ventana sigue abierta. Sin valores: solo campo, estado y
    el tipo de transformación que aplicó la página (p. ej. «convirtió a minúsculas»)."""
    lineas = ["Verificación (la ventana sigue abierta; ciérrela usted al terminar de revisar):"]
    for c in campos:
        r = por_clave.get(a_clave[c["campo"]])
        nota = ""
        if r is not None and r.status == "error":     # FillError: mensaje propio sin datos; Playwright: solo el tipo
            motivo = r.detail.split(":")[0] if r.detail.startswith(("Error", "Timeout")) else r.detail
            nota = f" — no se pudo rellenar automáticamente ({motivo}); complételo usted en la ventana"
        elif c["estado"] in ("ok_normalizado", "diferente") and r is not None and r.detail:
            nota = f" — {r.detail}"
        elif c["estado"] == "invalido":     # mensaje del navegador o del control (sin valores), no el texto de la página
            nota = " — " + ((r.validation_message if r is not None else "") or "la página marca el campo como no válido")
        lineas.append(f"  [{'OK' if c['estado'].startswith('ok') else 'REVISAR'}] {c['campo']}: {c['estado']}{nota}")
    for c in confirmaciones:
        lineas.append(f"  [{'OK' if c['coinciden'] else 'REVISAR'}] confirmación de {c['campo']}: "
                      f"{'coincide' if c['coinciden'] else 'NO coincide'}")
    print("\n".join(lineas), file=sys.stderr, flush=True)


def ejecutar(texto: str, *, ahora: Optional[datetime] = None, settings: Optional[Settings] = None,
             state_dir: Optional[str | Path] = None,
             fabrica_sesion: Optional[Callable[[Settings], BrowserSession]] = None,
             gancho_revision: Optional[Callable[[BrowserSession], None]] = None) -> tuple[Optional[dict], int]:
    """Devuelve (respuesta o None, código de salida). `None` = solicitud ilegible: solo se devuelve el código 6."""
    t0 = time.monotonic()
    settings = settings or get_settings()
    reloj = (lambda: ahora) if ahora else (lambda: datetime.now(timezone.utc))
    state = Path(state_dir or os.environ.get("CAUCE_STATE_DIR", "state")) / "solicitudes_usadas"
    try:
        sol = json.loads(texto)
    except ValueError:
        return None, C.SALIDA["rechazado_solicitud_invalida"]
    if not isinstance(sol, dict) or C._validar_esquema(sol, C.cargar_esquema("solicitud")):
        return None, C.SALIDA["rechazado_solicitud_invalida"]       # sin respuesta: no hay nada fiable que citar
    run = _Corrida(sol, t0)
    sesion: Optional[BrowserSession] = None
    try:
        try:
            sem = C.validar_solicitud(sol, None, solo_local=False)
            if any(e.startswith("FORMULARIO_NO_AUTORIZADO") for e in sem):
                raise _Parar("rechazado_no_autorizado", "FORMULARIO_NO_AUTORIZADO")
            if sem:
                raise _Parar("rechazado_solicitud_invalida", "SOLICITUD_MAL_FORMADA")
            if reloj() >= C.ts(sol["caduca_en"]):
                raise _Parar("rechazado_solicitud_invalida", "SOLICITUD_CADUCADA")
            if C.date.fromisoformat(sol["autorizacion"]["vigente_hasta"]) < reloj().date():
                raise _Parar("rechazado_no_autorizado", "AUTORIZACION_CADUCADA")
            try:
                UrlPolicy.load(settings.allowed_hosts_file).check(sol["formulario"]["url"], True)
            except UrlNotAllowed:
                raise _Parar("rechazado_no_autorizado", "FORMULARIO_NO_AUTORIZADO")
            try:
                _consumir_id(state, sol["solicitud_id"], reloj())
            except OSError:
                raise _Parar("error_interno", "ERROR_INTERNO")      # sin registro de ids no se arriesga a repetir
            perfil = _cargar_perfil(sol, settings)
            run.formulario["firma_estructura"] = ""
            return _preparar(run, sol, perfil, settings, reloj, fabrica_sesion, gancho_revision)
        except _Parar as p:
            resp = run.respuesta(p.estado, p.codigos, formulario=p.formulario)
            return _cerrar(resp, sol)
    except Exception as exc:                  # fallo seguro: nada de texto de la excepción (podría llevar valores)
        print(f"ERROR_INTERNO {type(exc).__name__}", file=sys.stderr)
        return _cerrar(run.respuesta("error_interno", ("ERROR_INTERNO",)), sol)


def _cerrar(resp: dict, sol: dict) -> tuple[dict, int]:
    """Última barrera: la respuesta debe pasar las MISMAS reglas que aplicará Monitor; si no, error genérico."""
    salida = C.SALIDA[resp["estado"]]
    if C.validar_respuesta(resp, sol, salida):
        resp = {**resp, "estado": "error_interno", "campos": [], "confirmaciones": [], "huella_preparacion": "",
                "errores": [{"codigo": "ERROR_INTERNO"}],
                "envio": {"realizado_por_conector": False, "envio_humano_observado": False},
                "revision_humana": {"requerida": True, "resultado": "no_realizada", "duracion_s": 0},
                "formulario": {**resp["formulario"], "firma_estructura": "", "estructura_coincide": False,
                               "cambios": dict(_VACIO)}}
        salida = C.SALIDA["error_interno"]
    return resp, salida


def huella_preparacion_registro(registro: RegistroOperaciones, url: str, campos: dict) -> str:
    """Huella del expediente en el registro: formulario (host + ruta, sin consulta) + valores pedidos."""
    u = urlsplit(url)
    return registro.huella({"_formulario": f"{u.scheme}://{u.hostname}:{u.port or ''}{u.path or '/'}",
                            **{str(k): str(v) for k, v in campos.items()}})


def _preparar(run: _Corrida, sol: dict, perfil: dict, settings: Settings, reloj, fabrica, gancho) -> tuple[dict, int]:
    form, op = sol["formulario"], sol["opciones"]
    integ = perfil["integracion"]
    # Registro durable ANTES de abrir el navegador: si no se puede garantizar que un envío de la persona quede
    # anotado, no se prepara nada.
    try:
        registro = RegistroOperaciones(settings.data_dir)
    except OSError:
        raise _Parar("error_interno", "ERROR_INTERNO")
    if not registro._clave_persistida:
        raise _Parar("error_interno", "ERROR_INTERNO")
    huella_reg = huella_preparacion_registro(registro, form["url"], sol["campos"])
    try:
        registro.comprobar(huella_reg)
    except OperacionBloqueada as exc:
        print(f"OPERACION_PREVIA {exc.codigo}", file=sys.stderr)      # código fijo, sin datos
        raise _Parar("detenido_intervencion_humana", "INTERVENCION_HUMANA_REQUERIDA")
    anotada: dict[str, Any] = {}

    def _envio_de_la_persona(via: str) -> Optional[bool]:
        if anotada:
            return None if anotada.get("ok") else False
        try:
            o = registro.registrar_envio_humano(
                huella_reg, safe_url_label(form["url"]), ref_solicitud=sol["solicitud_id"],
                motivo="envio_humano_observado_preparar" if via == "submit" else "envio_humano_por_javascript_preparar")
            anotada.update(ok=True, id=o["id"])
            print(f"OPERACION_REGISTRADA {o['id']}", file=sys.stderr)
            return True
        except (OSError, ValueError):
            anotada.update(ok=False)
            print("OPERACION_NO_REGISTRADA", file=sys.stderr)
            return False
    sesion = (fabrica or (lambda s: BrowserSession(s, headless=s.headless)))(settings)
    sesion.integration_mode = True          # H1: no existe envío programático en esta sesión
    try:
        try:
            sesion.start()
        except Exception:
            raise _Parar("error_interno", "NAVEGADOR_NO_DISPONIBLE")
        policy = UrlPolicy.load(settings.allowed_hosts_file)
        try:
            sesion.open_url(form["url"], policy.guard(True))
            pagina_ok = _misma_pagina(sesion.page.url, form["url"])
        except Exception:
            pagina_ok = False
        if not pagina_ok:                   # navegación bloqueada, error de red o redirección inesperada
            raise _Parar("error_interno", "PAGINA_NO_DISPONIBLE")
        # Cuadros de texto que el perfil aprobado registró como listas: se tratan igual que cuando se aprobaron
        sesion.listas_texto = _listas_texto_aprobadas(perfil)
        analisis = sesion.inspect()
        actual = structure_signature_v2(analisis.all_fields)
        if analisis.safety.blocked:         # CAPTCHA o página «solo humanos»: no se escribe nada ni se intenta superar
            raise _Parar("detenido_intervencion_humana", "INTERVENCION_HUMANA_REQUERIDA")
        if actual != form["firma_estructura_esperada"]:
            cambios = diff_structure(perfil["fields"], analisis.all_fields)
            raise _Parar("detenido_estructura_cambiada", "ESTRUCTURA_CAMBIADA", formulario={
                "origen": form["url"], "firma_estructura": actual, "estructura_coincide": False, "cambios": cambios})
        run.formulario = {"origen": form["url"], "firma_estructura": actual, "estructura_coincide": True,
                          "cambios": dict(_VACIO)}
        if reloj() >= C.ts(sol["caduca_en"]):       # caducidad comprobada también justo antes de escribir
            raise _Parar("rechazado_solicitud_invalida", "SOLICITUD_CADUCADA")

        # Desde antes del relleno, todo envío de la persona (o escritura de la página al sitio) queda anotado
        # (conservador: una escritura automática de la página durante el tecleo también cuenta).
        sesion.on_submit_humano = _envio_de_la_persona
        # --- relleno: SOLO los campos aprobados y pedidos; los valores no se vuelven a tocar ni a imprimir ---
        a_clave = {l: integ["campos"][l] for l in sol["campos"]}
        mapping = {clave: logico for logico, clave in a_clave.items()}
        informe = fill_form(sesion, mapping, dict(sol["campos"]), delay_ms=op["pausa_tecla_ms"],
                            audit=AuditLog(None), allow_late_fields=False)
        if informe.blocked:
            raise _Parar("detenido_intervencion_humana", "INTERVENCION_HUMANA_REQUERIDA")
        por_clave = {r.field_key: r for r in informe.results}
        campos, hay_incidencia = [], False
        for logico in sorted(sol["campos"]):
            r = por_clave.get(a_clave[logico])
            estado = r.status if r and r.status in ("ok", "ok_normalizado", "diferente", "invalido") else "invalido"
            hay_incidencia |= estado not in ("ok", "ok_normalizado")
            campos.append({"campo": logico, "estado": estado, "metodo": _METODO.get(r.method if r else "", "teclado_secuencial"),
                           "caracteres": min(1000, r.keystrokes if r else 0)})
        logico_de = {clave: logico for logico, clave in a_clave.items()}
        confirmaciones = []
        for c in informe.confirmations:
            if c["confirmacion"] in logico_de:      # solo confirmaciones cuyo campo está aprobado y pedido
                confirmaciones.append({"campo": logico_de[c["confirmacion"]], "coinciden": bool(c["match"])})
                hay_incidencia |= not c["match"]
        fin_relleno = time.monotonic()
        huella = C.huella_preparacion(sol["solicitud_id"], form["firma_estructura_esperada"], sol["campos"])
        base = dict(campos=campos, confirmaciones=confirmaciones, huella=huella)
        _mostrar_verificacion(campos, confirmaciones, por_clave, a_clave)

        # --- revisión humana en la ventana visible; el conector solo observa. También con incidencias: la ventana
        # queda abierta (sin borrar ni tocar los valores) para que la persona inspeccione y la cierre ella misma. ---
        espera = op["espera_revision_max_s"]
        resultado, dur = ("caducada", 0) if espera == 0 else _esperar_revision(sesion, espera, fin_relleno, gancho)
        if resultado == "error":
            raise _Parar("error_interno", "ERROR_INTERNO")
        if hay_incidencia:
            return _cerrar(run.respuesta("preparado_con_incidencias", ("CAMPO_NO_VERIFICADO",),
                                         revision=(resultado, dur), observado=resultado == "envio_humano", **base), sol)
        if resultado == "envio_humano":
            r = run.respuesta("enviado_por_persona_observado", (), revision=("envio_humano", dur), observado=True, **base)
        elif resultado == "cerrada_sin_envio":
            r = run.respuesta("preparado_revisado_sin_envio", (), revision=("cerrada_sin_envio", dur), **base)
        else:
            r = run.respuesta("revision_caducada", ("REVISION_CADUCADA",), revision=("caducada", dur), **base)
        return _cerrar(r, sol)
    finally:
        sesion.close()


def principal(argumentos: Optional[list[str]] = None) -> int:
    """Punto de entrada del subcomando `preparar`. Lee UNA solicitud de stdin y escribe UNA respuesta en stdout."""
    import signal

    def _terminar(*_: Any) -> None:             # cancelación del operador o de Monitor: cerrar sin afirmar nada
        raise SystemExit(1)

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _terminar)
        except (ValueError, OSError):
            pass
    texto = sys.stdin.read(MAX_ENTRADA + 1)
    if len(texto) > MAX_ENTRADA:
        return C.SALIDA["rechazado_solicitud_invalida"]
    resp, salida = ejecutar(texto)
    if resp is not None:
        sys.stdout.write(json.dumps(resp, ensure_ascii=False))
        sys.stdout.flush()
    return salida
