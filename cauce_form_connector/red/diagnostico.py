"""Diagnóstico de red NATIVO de Forms con Cauce Network Engine RC3 (contrato ``docs/CONTRATO_SESIONES_FORMS.md``).

Basado en el consumidor de referencia de RC3 (``cauce/session_consumer.py`` @ f93dd58), NO en el de RC2, con estas
diferencias, todas cubiertas por pruebas:

1. Latidos en un hilo propio (cada ``intervalo_s``), nunca en el hilo de Playwright ni en el de la interfaz.
2. Cada latido lleva la operación ACTUAL (``idle``/``in_progress``): si el aviso «en curso» no llegó, llega en el
   siguiente latido. Repetir un LATIDO es inocuo; la acción de la persona nunca se repite.
3. La acción de la persona espera al motor como mucho ``AVISO_MAX_S`` (3 s) para avisar «en curso». Si el motor no
   responde en ese plazo, la acción sigue (el motor solo observa) y se cuenta como aviso no entregado.
4. Un resultado desconocido deja una «marca por entregar»: se envía ``status=error`` con ``operation=in_progress``
   (el motor marca ``requires_human``) hasta que el motor confirme la marca, aunque esté apagado horas.
5. ``puesto.json`` recuerda el id de sesión: tras reiniciar Forms se sigue con el mismo id (si no, 409).
6. La marca «revisar a mano» vista una vez no se olvida en pantalla por un 401, 403, 429, motor apagado o cierre.
7. Forms NUNCA llama a ``resolve``: solo observa, en la MISMA sesión, que el operador la resolvió.

Estados del motor que ve la persona (``motor``):
``no_configurado`` · ``conectado`` · ``no_disponible`` (sin diagnóstico; NO significa sin internet) ·
``credencial_invalida`` (401) · ``credencial_incorrecta`` (403: no es de ámbito session o la sesión es de otra) ·
``limitado`` (429) · ``sesion_ocupada`` (409: la credencial ya tiene otra sesión viva cuyo id se perdió).

Conectividad (``conectividad``): ``verificada`` · ``degradada`` · ``perdida`` · ``desconocida`` ·
``desactualizada`` (la última respuesta del motor es antigua) · ``sin_diagnostico`` (motor no disponible).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from .cliente_ne import CauceAPIError, CauceSessionClient
from .identidad import Puesto

AVISO_MAX_S = 3.0             # lo máximo que una acción de la persona espera al motor
INTERVALO_MIN_S = 0.55        # el motor rechaza latidos a menos de 0,5 s (429)
PAUSA_429_S = 2.0

MOTOR_NO_CONFIGURADO = "no_configurado"
MOTOR_CONECTADO = "conectado"
MOTOR_NO_DISPONIBLE = "no_disponible"
MOTOR_CREDENCIAL_INVALIDA = "credencial_invalida"
MOTOR_CREDENCIAL_INCORRECTA = "credencial_incorrecta"
MOTOR_LIMITADO = "limitado"
MOTOR_SESION_OCUPADA = "sesion_ocupada"
MOTOR_CONFIGURACION_INVALIDA = "configuracion_invalida"
MOTOR_DETENIDO = (MOTOR_CREDENCIAL_INVALIDA, MOTOR_CREDENCIAL_INCORRECTA, MOTOR_CONFIGURACION_INVALIDA)

_RED = {"ok": "verificada", "degraded": "degradada", "down": "perdida", "unknown": "desconocida"}

TEXTO_MOTOR = {
    MOTOR_NO_CONFIGURADO: "Network Engine no configurado: Forms funciona sin diagnóstico de red.",
    MOTOR_CONECTADO: "Network Engine conectado.",
    MOTOR_NO_DISPONIBLE: "Network Engine no disponible: no hay diagnóstico de red (esto NO indica falta de internet).",
    MOTOR_CREDENCIAL_INVALIDA: "La credencial de Network Engine no es válida o fue revocada: avise al administrador.",
    MOTOR_CREDENCIAL_INCORRECTA: "La credencial no es de ámbito «session» o no corresponde a esta sesión: avise al administrador.",
    MOTOR_LIMITADO: "Network Engine pidió espaciar los latidos: se reintenta en unos segundos.",
    MOTOR_SESION_OCUPADA: "Network Engine ya tiene otra sesión viva con esta credencial: avise al administrador.",
    MOTOR_CONFIGURACION_INVALIDA: "La configuración de Network Engine en Forms no es válida (credencial ilegible o "
                                  "etiqueta de puesto incorrecta): Forms funciona sin diagnóstico de red.",
}


class DiagnosticoRed:
    def __init__(self, cliente: Optional[CauceSessionClient], puesto: Optional[Puesto], conexion: Optional[str] = None,
                 intervalo_s: float = 7.0, latido_max_s: float = 30.0, desactualizado_s: Optional[float] = None,
                 reloj: Callable[[], float] = time.monotonic,
                 al_resolver: Optional[Callable[[str], None]] = None,
                 al_cambiar: Optional[Callable[[dict], None]] = None,
                 hay_inciertas: Optional[Callable[[], bool]] = None,
                 al_ver_marca: Optional[Callable[[str], None]] = None):
        self.c = cliente
        self.puesto = puesto
        self.conexion = conexion
        self.intervalo_s = float(intervalo_s)
        self.latido_max_s = float(latido_max_s)
        self.desactualizado_s = float(desactualizado_s if desactualizado_s is not None else max(20.0, 2.5 * intervalo_s))
        self.reloj = reloj
        self.al_resolver = al_resolver           # el operador resolvió la marca de ESTA sesión (positivo, no ausencia)
        self.al_cambiar = al_cambiar             # para auditoría: (motor, fase, requiere_persona) cambió
        self.hay_inciertas = hay_inciertas       # el registro local de operaciones tiene algo sin revisar
        self.al_ver_marca = al_ver_marca         # el motor muestra requires_human en esta sesión
        self._envio = threading.Lock()           # un latido a la vez
        self._st = threading.Lock()              # protege el estado visible
        self._parar = threading.Event()
        self._hilo: Optional[threading.Thread] = None
        self._ultimo_envio = float("-inf")
        self._espera_hasta = 0.0
        self._op = "idle"
        self._marca_por_entregar = False
        self._navegador_ok, self._navegador_codigo = True, None
        self._rh_visto_en: Optional[str] = None  # id de sesión en la que se vio requires_human
        self.avisos_no_entregados = 0
        self.latidos = 0
        self.ultima_respuesta: Optional[float] = None
        self._v: dict[str, Any] = {"motor": MOTOR_NO_CONFIGURADO if cliente is None else MOTOR_NO_DISPONIBLE,
                                   "fase": None, "red": None, "requiere_persona": False, "motivo": None,
                                   "codigo": None, "restaurada": False}
        if cliente is not None and puesto is not None and puesto.sesion_ne:
            cliente.session_id = puesto.sesion_ne

    # ------------------------------------------------------------------ estado visible
    @property
    def configurado(self) -> bool:
        return self.c is not None

    def estado(self) -> dict[str, Any]:
        with self._st:
            v = dict(self._v)
            ultima = self.ultima_respuesta
            op, marca = self._op, self._marca_por_entregar
        edad = None if ultima is None else round(self.reloj() - ultima, 1)
        if v["motor"] == MOTOR_CONECTADO and edad is not None and edad > self.desactualizado_s:
            conectividad = "desactualizada"
        elif v["motor"] in (MOTOR_CONECTADO, MOTOR_LIMITADO) and v["red"] is not None:
            conectividad = _RED.get(v["red"], "desconocida")
            if v["motor"] == MOTOR_LIMITADO and edad is not None and edad > self.desactualizado_s:
                conectividad = "desactualizada"
        else:
            conectividad = "sin_diagnostico"
        return {"motor": v["motor"], "motor_texto": TEXTO_MOTOR[v["motor"]], "conectividad": conectividad,
                "fase": v["fase"] if v["motor"] in (MOTOR_CONECTADO, MOTOR_LIMITADO) else None,
                "ultima_fase": v["fase"], "etiqueta": self.puesto.etiqueta if self.puesto else None,
                "sesion_id": self.c.session_id if self.c else None, "operacion": op,
                "requiere_persona": v["requiere_persona"], "motivo": v["motivo"], "codigo": v["codigo"],
                "restaurada": v["restaurada"], "ultima_respuesta_s": edad,
                "avisos_no_entregados": self.avisos_no_entregados, "marca_por_entregar": marca,
                "latidos": self.latidos}

    def _fijar(self, **kw: Any) -> None:
        with self._st:
            antes = (self._v["motor"], self._v["fase"], self._v["requiere_persona"])
            # una marca «revisar a mano» ya vista no se olvida porque el motor deje de responder o rechace la credencial
            if kw.get("motor", self._v["motor"]) != MOTOR_CONECTADO and self._v["requiere_persona"]:
                kw["requiere_persona"] = True
                kw.setdefault("motivo", self._v["motivo"])
                kw["motivo"] = kw["motivo"] or self._v["motivo"]
            self._v.update(kw)
            despues = (self._v["motor"], self._v["fase"], self._v["requiere_persona"])
        if antes != despues and self.al_cambiar:
            try:
                self.al_cambiar(self.estado())
            except Exception:  # noqa: BLE001  la auditoría nunca rompe el diagnóstico
                pass

    # ------------------------------------------------------------------ ciclo de vida
    def iniciar(self) -> None:
        if self.c is None or self._hilo is not None:
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, name="cauce-ne-latidos", daemon=True)
        self._hilo.start()

    def _bucle(self) -> None:
        while not self._parar.is_set():
            self.latir()
            self._parar.wait(self.intervalo_s)

    def detener(self, cerrar: bool = False, espera_s: float = 6.0) -> None:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=espera_s)
            self._hilo = None
        if cerrar and self.c is not None and self.c.session_id and self._v["motor"] not in MOTOR_DETENIDO:
            # Cerrar NO borra una revisión pendiente: el motor la guarda («pending_human_review») y la hereda el
            # siguiente registro de esta credencial o de esta etiqueta. Forms la sigue mostrando por su registro local.
            acquired = self._envio.acquire(timeout=espera_s)
            try:
                self.c.close()
                if self.puesto:
                    self.puesto.recordar_sesion(None)
                self._fijar(fase="cerrada")
            except (CauceAPIError, OSError, ValueError):
                pass
            finally:
                if acquired:
                    self._envio.release()

    # ------------------------------------------------------------------ operación de la persona
    def operacion_inicio(self) -> bool:
        """Justo ANTES de la acción confirmada. Devuelve si el aviso «en curso» llegó. Espera como mucho AVISO_MAX_S."""
        with self._st:
            self._op = "in_progress"
        if self.c is None:
            return False
        ok = self.latir(espera_max_s=AVISO_MAX_S, obligatorio=True)
        if not ok:
            self.avisos_no_entregados += 1
        return ok

    def operacion_fin(self, resultado_seguro: bool) -> bool:
        """Justo DESPUÉS. ``resultado_seguro`` solo si hay acuse verificado o consta que no se pulsó nada.

        Si no es seguro (incierto, interrumpido, error a medias), se deja una marca por entregar: el motor la recibe
        como ``error`` con la operación en curso y la convierte en ``requires_human``. Nada se reintenta."""
        with self._st:
            if resultado_seguro:
                self._op = "idle"
            else:
                self._marca_por_entregar = True
        if self.c is None:
            return False
        ok = self.latir(espera_max_s=AVISO_MAX_S, obligatorio=True)
        if not ok:
            self.avisos_no_entregados += 1
        return ok

    def exigir_marca(self) -> None:
        """Hay operaciones inciertas en el registro local (p. ej. tras reiniciar Forms): el motor debe saberlo."""
        with self._st:
            self._marca_por_entregar = True

    def navegador(self, ok: bool, codigo: Optional[str] = None) -> None:
        with self._st:
            self._navegador_ok, self._navegador_codigo = ok, (None if ok else (codigo or "other"))

    # ------------------------------------------------------------------ latido
    def latir(self, espera_max_s: Optional[float] = None, obligatorio: bool = False) -> bool:
        """Un latido (o registro). Devuelve True si el motor respondió con la vista de la sesión."""
        if self.c is None or self._v["motor"] in MOTOR_DETENIDO:
            return False
        timeout = espera_max_s
        if not self._envio.acquire(timeout=espera_max_s if espera_max_s is not None else -1):
            return False
        try:
            ahora = self.reloj()
            if ahora < self._espera_hasta:
                if not obligatorio:
                    return False
                falta = self._espera_hasta - ahora
                if espera_max_s is not None and falta > espera_max_s:
                    return False
                time.sleep(falta)
            falta = self._ultimo_envio + INTERVALO_MIN_S - self.reloj()
            if falta > 0:
                if not obligatorio and falta > 0.2:
                    return False
                time.sleep(falta)
            return self._latir_sin_lock(timeout)
        finally:
            self._envio.release()

    def _cuerpo(self) -> tuple[bool, Optional[str], str]:
        with self._st:
            if self._marca_por_entregar:
                return False, "other", "in_progress"
            if not self._navegador_ok:
                return False, self._navegador_codigo, self._op
            return True, None, self._op

    def _latir_sin_lock(self, timeout: Optional[float]) -> bool:
        ok, codigo, op = self._cuerpo()
        self._ultimo_envio = self.reloj()
        try:
            if not self.c.session_id:
                v = self.c.register(self.puesto.etiqueta if self.puesto else "forms-sin-puesto",
                                    connection_id=self.conexion, heartbeat_timeout_s=self.latido_max_s)
                if self.puesto:
                    self.puesto.recordar_sesion(self.c.session_id)
                self._ultimo_envio = self.reloj()
                # tras registrar se envía enseguida el estado real (operación y marca), sin esperar al siguiente ciclo
                time.sleep(INTERVALO_MIN_S)
                self._ultimo_envio = self.reloj()
                ok, codigo, op = self._cuerpo()
            v = self.c.heartbeat(ok, codigo, op, timeout=timeout)
        except CauceAPIError as e:
            return self._error_api(e, timeout)
        except (OSError, ValueError) as e:      # motor apagado, rechazo, tiempo agotado, JSON ilegible
            self._fijar(motor=MOTOR_NO_DISPONIBLE, codigo=type(e).__name__)
            return False
        self.latidos += 1
        self._desde_vista(v, op)
        return True

    def _desde_vista(self, v: dict, op_enviada: str) -> None:
        rh = bool(v.get("requires_human"))
        sid = v.get("id")
        if rh:
            self._rh_visto_en = sid
            if self.al_ver_marca and sid:
                try:
                    self.al_ver_marca(sid)
                except Exception:  # noqa: BLE001
                    pass
            with self._st:
                if self._marca_por_entregar:
                    self._marca_por_entregar = False      # el motor ya tiene la marca: dejar de insistir
                    self._op = "idle"
        resuelta = (not rh and self._rh_visto_en is not None and self._rh_visto_en == sid)
        with self._st:
            self.ultima_respuesta = self.reloj()
        self._fijar(motor=MOTOR_CONECTADO, fase=v.get("phase") or "incierta",
                    red=(v.get("network") or {}).get("state"), requiere_persona=rh, motivo=v.get("human_reason"),
                    codigo=None, restaurada=bool(v.get("restored")))
        if resuelta:
            self._rh_visto_en = None
            if self.al_resolver:
                try:
                    self.al_resolver(sid)
                except Exception:  # noqa: BLE001
                    pass
        # Si el motor no tiene la marca (nueva sesión tras 404, reinicio sin persistencia, o nunca recibió «en curso»)
        # pero el registro local sí tiene operaciones sin revisar, se le vuelve a entregar: el centro de revisiones de
        # Monitor debe verla. La AUSENCIA de marca en el motor nunca da nada por revisado.
        if not rh and self.hay_inciertas is not None:
            try:
                pendiente = bool(self.hay_inciertas())
            except Exception:  # noqa: BLE001
                pendiente = True
            if pendiente:
                with self._st:
                    self._marca_por_entregar = True

    def _error_api(self, e: CauceAPIError, timeout: Optional[float]) -> bool:
        if e.status == 404 and e.code == "unknown_session":
            # el motor ya no la conoce (cerrada, caducada o sin persistencia): registrarla de nuevo; la marca local sigue
            self.c.session_id = None
            if self.puesto:
                self.puesto.recordar_sesion(None)
            self._rh_visto_en = None
            return self._latir_sin_lock(timeout)
        if e.status == 401:
            self._fijar(motor=MOTOR_CREDENCIAL_INVALIDA, codigo=e.code)
        elif e.status == 403:
            self._fijar(motor=MOTOR_CREDENCIAL_INCORRECTA, codigo=e.code)
        elif e.status == 429:
            self._espera_hasta = self.reloj() + PAUSA_429_S
            self._fijar(motor=MOTOR_LIMITADO, codigo=e.code)
        elif e.status == 409 and e.code == "credential_has_session":
            self._fijar(motor=MOTOR_SESION_OCUPADA, codigo=e.code)
        else:                                    # 5xx (p. ej. 503 credentials_locked) u otro: sin información
            self._fijar(motor=MOTOR_NO_DISPONIBLE, codigo=e.code)
        return False

    def reanudar(self) -> None:
        """Tras cambiar la credencial (acción del administrador). Nunca automático."""
        self._fijar(motor=MOTOR_NO_DISPONIBLE, codigo=None)
