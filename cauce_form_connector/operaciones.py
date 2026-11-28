"""Registro local y durable de operaciones confirmadas por una persona (protección contra repeticiones).

Reglas (todas con pruebas en ``tests/test_operaciones.py`` y ``tests/test_envio_protegido.py``):

1. Una acción confirmada se ejecuta COMO MÁXIMO UNA VEZ. El registro se escribe en disco (fsync + reemplazo atómico)
   en estado ``en_curso`` ANTES del clic. Si no se puede escribir, no se pulsa nada.
2. Nada se reenvía solo. Ningún código de Forms vuelve a pulsar por su cuenta.
3. Resultado desconocido → ``incierta``. Solo dejan de estar abiertas por EVIDENCIA o por una PERSONA:
   - ``confirmada``: el portal propio de laboratorio confirmó por su API el acuse (folio) de ESE envío;
   - ``revisada``: un operador la revisó (``cauce-forms operaciones revisar`` con nota y confirmación escrita), o
     Forms observó, en la MISMA sesión de Network Engine, que el operador resolvió la marca (``resolve``).
   Recuperar internet, reiniciar, cerrar la sesión, revocar la credencial o que el motor «olvide» la marca NO cuentan.
4. Un reinicio de Forms convierte toda operación ``en_curso`` en ``incierta`` (``forms_reiniciado_durante_operacion``).
5. Archivo dañado: se usa la copia ``.bak`` y TODO lo abierto pasa a revisión; si tampoco vale, se aparta el archivo
   (``.corrupt-<fecha>``, nunca se borra) y una marca GENERAL bloquea nuevos envíos hasta que un operador la revise.
6. Un expediente con una operación ``en_curso``, ``incierta`` o ``confirmada`` no se puede volver a enviar.
7. Un clic observado NO es una recepción: ``envio_observado`` solo es un indicio; ``confirmada`` exige el acuse.

Qué se guarda (lista blanca, sin datos personales): id de operación, fechas, estado, motivo, huella del expediente
(HMAC-SHA256 con una clave aleatoria local, truncado: no permite recuperar los datos y sin la clave no permite
compararlos), origen sin consulta (host + ruta), token opaco del portal de laboratorio, folio del acuse, id de la
sesión de Network Engine y, si la hubo, la revisión (quién, nota saneada, cuándo). Nunca valores, cookies ni
credenciales.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .audit import redact

ESQUEMA = "cauce.forms.operaciones/1"
ARCHIVO = "operaciones.json"
CLAVE = "huella.key"

EN_CURSO, CONFIRMADA, INCIERTA, NO_ENVIADA, RECHAZADA, REVISADA = (
    "en_curso", "confirmada", "incierta", "no_enviada", "rechazada", "revisada")
ESTADOS = (EN_CURSO, CONFIRMADA, INCIERTA, NO_ENVIADA, RECHAZADA, REVISADA)
ABIERTOS = (EN_CURSO, INCIERTA)                       # requieren evidencia o revisión humana
BLOQUEAN_REPETICION = (EN_CURSO, INCIERTA, CONFIRMADA)  # el expediente no se vuelve a enviar
MAX_CERRADAS = 500                                    # las abiertas nunca se descartan

CAMPOS = {"id", "creada", "actualizada", "estado", "motivo", "huella", "origen", "token_portal", "folio",
          "sesion_ne", "marca_ne", "revision", "aviso_en_curso", "envio_observado", "api_acuse"}
_ID_RE = re.compile(r"^op_[0-9a-f]{12}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_FOLIO_RE = re.compile(r"^[A-Z0-9-]{4,32}$")
_SID_RE = re.compile(r"^bs_[0-9a-f]{12}$")
_API_RE = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d{1,5})?/[A-Za-z0-9/_-]{0,80}$")


class OperacionBloqueada(PermissionError):
    def __init__(self, codigo: str, mensaje: str, operacion: Optional[str] = None):
        super().__init__(mensaje)
        self.codigo, self.operacion = codigo, operacion


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resumen(ops: list, general: Optional[dict]) -> str:
    return hashlib.sha256(json.dumps({"o": ops, "g": general}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _limpia(o: Any) -> Optional[dict]:
    """Valida una operación leída del disco. Lo que no encaje se descarta (y el archivo cuenta como dañado)."""
    if not isinstance(o, dict) or not _ID_RE.match(str(o.get("id", ""))) or o.get("estado") not in ESTADOS:
        return None
    if set(o) - CAMPOS:
        return None
    return dict(o)


class RegistroOperaciones:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir)
        self.path = self.dir / ARCHIVO
        self.bak = self.dir / (ARCHIVO + ".bak")
        self._lock = threading.RLock()
        self.ops: list[dict] = []
        self.general: Optional[dict] = None      # marca general: bloquea todo envío hasta revisión humana
        self.estado_carga = "nuevo"              # nuevo | ok | respaldo | ilegible
        self.eventos: list[dict] = []            # para la auditoría del llamador
        self._clave = self._leer_clave()
        self._cargar()

    # ------------------------------------------------------------------ clave de huella
    def _leer_clave(self) -> bytes:
        p = self.dir / CLAVE
        try:
            k = p.read_bytes()
            if len(k) == 32:
                return k
        except OSError:
            pass
        k = secrets.token_bytes(32)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(k)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            pass                                     # sin disco: la huella vale solo en este proceso
        return k

    def huella(self, fila: dict[str, Any]) -> str:
        """Huella del expediente: HMAC-SHA256 (clave local aleatoria) de los valores ordenados por columna."""
        canon = json.dumps({str(k): str(v) for k, v in sorted(fila.items())}, ensure_ascii=False, sort_keys=True)
        return "h_" + hmac.new(self._clave, canon.encode("utf-8"), hashlib.sha256).hexdigest()[:24]

    # ------------------------------------------------------------------ persistencia
    def _leer(self, p: Path) -> Optional[dict]:
        try:
            d = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(d, dict) or d.get("esquema") != ESQUEMA or not isinstance(d.get("operaciones"), list):
            return None
        ops = [_limpia(o) for o in d["operaciones"]]
        g = d.get("general")
        if None in ops or (g is not None and not isinstance(g, dict)):
            return None
        if d.get("sha256") != _resumen(ops, g):
            return None                              # editado a mano o escrito a medias: no vale
        return {"operaciones": ops, "general": g}

    def _cargar(self) -> None:
        if not self.path.exists() and not self.bak.exists():
            return
        doc = self._leer(self.path)
        if doc is not None:
            self.estado_carga = "ok"
        else:
            doc = self._leer(self.bak)
            if doc is not None:
                self.estado_carga = "respaldo"
            else:
                self.estado_carga = "ilegible"
        if self.estado_carga in ("respaldo", "ilegible") and self.path.exists():
            marca = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            try:
                shutil.copy2(self.path, self.path.with_name(f"{ARCHIVO}.corrupt-{marca}"))
            except OSError:
                pass
        if doc is None:
            self.general = {"motivo": "registro_ilegible", "desde": _ahora()}
            self.eventos.append({"event": "operaciones_registro_ilegible"})
            self._guardar()
            return
        self.ops, self.general = doc["operaciones"], doc["general"]
        cambio = False
        for o in self.ops:
            if o["estado"] == EN_CURSO:
                # Forms se cerró con un envío a medias: nunca se da por bueno ni se repite.
                o.update(estado=INCIERTA, motivo="forms_reiniciado_durante_operacion", actualizada=_ahora())
                self.eventos.append({"event": "operacion_incierta", "estado": INCIERTA,
                                     "codigo": "forms_reiniciado_durante_operacion"})
                cambio = True
        if self.estado_carga == "respaldo":
            # el respaldo es anterior al último cambio: lo que pasó después es desconocido
            self.general = {"motivo": "registro_recuperado_de_respaldo", "desde": _ahora()}
            self.eventos.append({"event": "operaciones_recuperadas_de_respaldo"})
            cambio = True
        if cambio:
            self._guardar()

    def _guardar(self) -> None:
        """temporal + fsync + reemplazo atómico; la versión buena anterior pasa a .bak. Lanza OSError si falla."""
        with self._lock:
            cerradas = [o for o in self.ops if o["estado"] not in ABIERTOS]
            if len(cerradas) > MAX_CERRADAS:
                quitar = {o["id"] for o in cerradas[: len(cerradas) - MAX_CERRADAS]}
                self.ops = [o for o in self.ops if o["id"] not in quitar]
            doc = {"esquema": ESQUEMA, "operaciones": self.ops, "general": self.general,
                   "sha256": _resumen(self.ops, self.general)}
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{ARCHIVO}.{os.getpid()}.{threading.get_ident()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, ensure_ascii=False)
                    fh.flush()
                    os.fsync(fh.fileno())
                if self.path.exists() and self._leer(self.path) is not None:
                    shutil.copy2(self.path, self.bak)
                os.replace(tmp, self.path)
            finally:
                if tmp.exists():
                    tmp.unlink()

    # ------------------------------------------------------------------ consultas
    def get(self, oid: str) -> Optional[dict]:
        with self._lock:
            return next((dict(o) for o in self.ops if o["id"] == oid), None)

    def abiertas(self) -> list[dict]:
        with self._lock:
            return [dict(o) for o in self.ops if o["estado"] in ABIERTOS]

    def hay_abiertas(self) -> bool:
        with self._lock:
            return self.general is not None or any(o["estado"] in ABIERTOS for o in self.ops)

    def lista(self, limite: int = 50) -> list[dict]:
        with self._lock:
            return [dict(o) for o in self.ops[-limite:]][::-1]

    def comprobar(self, huella: str) -> None:
        """Lanza ``OperacionBloqueada`` si NO se puede enviar este expediente ahora."""
        with self._lock:
            if self.general is not None:
                raise OperacionBloqueada("revision_general_pendiente",
                                         "El registro de operaciones necesita revisión de un operador "
                                         f"({self.general['motivo']}): no se envía nada hasta entonces.")
            for o in self.ops:
                if o.get("huella") == huella and o["estado"] in BLOQUEAN_REPETICION:
                    texto = {EN_CURSO: "ya hay un envío en curso de este expediente",
                             INCIERTA: "un envío anterior de este expediente tiene resultado desconocido y debe revisarse",
                             CONFIRMADA: "este expediente ya consta como recibido por el portal"}[o["estado"]]
                    raise OperacionBloqueada(f"expediente_{o['estado']}", f"No se envía: {texto} ({o['id']}).", o["id"])

    # ------------------------------------------------------------------ transiciones
    def iniciar(self, huella: str, origen: str, token_portal: Optional[str], sesion_ne: Optional[str],
                api_acuse: Optional[str] = None) -> dict:
        """Crea la operación ``en_curso`` y la guarda en disco. Lanza OSError si no puede (entonces NO se envía)."""
        with self._lock:
            self.comprobar(huella)
            o = {"id": "op_" + secrets.token_hex(6), "creada": _ahora(), "actualizada": _ahora(), "estado": EN_CURSO,
                 "motivo": None, "huella": huella, "origen": str(origen)[:200],
                 "token_portal": token_portal if token_portal and _TOKEN_RE.match(token_portal) else None,
                 "folio": None, "sesion_ne": sesion_ne if sesion_ne and _SID_RE.match(sesion_ne) else None,
                 "marca_ne": None, "revision": None, "aviso_en_curso": None, "envio_observado": None,
                 # solo la API del portal PROPIO (127.0.0.1/localhost), para verificar el acuse más tarde
                 "api_acuse": api_acuse if api_acuse and _API_RE.match(api_acuse) else None}
            self.ops.append(o)
            try:
                self._guardar()
            except OSError:
                self.ops.remove(o)
                raise
            return dict(o)

    def actualizar(self, oid: str, **campos: Any) -> dict:
        with self._lock:
            o = next(x for x in self.ops if x["id"] == oid)
            for k, v in campos.items():
                if k in ("aviso_en_curso", "envio_observado"):
                    o[k] = None if v is None else bool(v)
            o["actualizada"] = _ahora()
            self._guardar_sin_lanzar()
            return dict(o)

    def cerrar(self, oid: str, estado: str, motivo: Optional[str] = None, folio: Optional[str] = None) -> dict:
        """Resultado del envío. Solo admite pasar de ``en_curso`` a un estado final o a ``incierta``."""
        if estado not in (CONFIRMADA, INCIERTA, NO_ENVIADA, RECHAZADA):
            raise ValueError(estado)
        with self._lock:
            o = next(x for x in self.ops if x["id"] == oid)
            if o["estado"] != EN_CURSO:
                raise ValueError(f"la operación {oid} ya no está en curso ({o['estado']})")
            if estado == CONFIRMADA and not (folio and _FOLIO_RE.match(folio)):
                raise ValueError("confirmada exige el folio verificado del portal")
            o.update(estado=estado, motivo=(motivo or None) and str(motivo)[:80], actualizada=_ahora(),
                     folio=folio if folio and _FOLIO_RE.match(folio) else o.get("folio"))
            self._guardar_sin_lanzar()
            return dict(o)

    def confirmar_por_acuse(self, oid: str, folio: str) -> dict:
        """Una operación ``incierta`` pasa a ``confirmada`` SOLO con el folio que el portal confirmó por su API."""
        if not (folio and _FOLIO_RE.match(folio)):
            raise ValueError("folio no válido")
        with self._lock:
            o = next(x for x in self.ops if x["id"] == oid)
            if o["estado"] != INCIERTA:
                raise ValueError(f"solo una operación incierta se confirma por acuse ({o['estado']})")
            o.update(estado=CONFIRMADA, folio=folio, motivo="acuse_verificado_despues", actualizada=_ahora())
            self._guardar_sin_lanzar()
            return dict(o)

    def marca_vista_en(self, sid: str) -> None:
        """Network Engine mostró ``requires_human`` en la sesión ``sid``: las inciertas sin marca quedan ligadas a ella."""
        if not _SID_RE.match(sid or ""):
            return
        with self._lock:
            cambio = False
            for o in self.ops:
                if o["estado"] == INCIERTA and o.get("marca_ne") is None:
                    o["marca_ne"], cambio = sid, True
            if cambio:
                self._guardar_sin_lanzar()

    def resuelta_por_operador_ne(self, sid: str) -> list[str]:
        """El operador resolvió la marca de la sesión ``sid`` en Network Engine (señal POSITIVA, misma sesión)."""
        out = []
        with self._lock:
            for o in self.ops:
                if o["estado"] == INCIERTA and o.get("marca_ne") == sid:
                    o.update(estado=REVISADA, actualizada=_ahora(),
                             revision={"por": "operador_network_engine", "nota": "marca resuelta en Network Engine",
                                       "en": _ahora()})
                    out.append(o["id"])
            if out:
                self._guardar_sin_lanzar()
        return out

    def revisar(self, oid: str, nota: str, confirmar: str, por: str = "operador") -> dict:
        """Revisión de un OPERADOR (línea de órdenes, no la interfaz de la persona). Exige nota y repetir el id."""
        if not isinstance(nota, str) or len(nota.strip()) < 5:
            raise ValueError("Escriba qué revisó (nota de al menos 5 caracteres).")
        with self._lock:
            if oid == "GENERAL":
                if self.general is None:
                    raise LookupError("No hay marca general pendiente.")
                if confirmar != "GENERAL":
                    raise ValueError("Para confirmar escriba exactamente: GENERAL")
                self.general = None
                self._guardar()
                return {"id": "GENERAL", "estado": REVISADA}
            o = next((x for x in self.ops if x["id"] == oid), None)
            if o is None:
                raise LookupError("Operación inexistente.")
            if o["estado"] not in ABIERTOS:
                raise ValueError(f"La operación no está pendiente de revisión ({o['estado']}).")
            if confirmar != oid:
                raise ValueError(f"Para confirmar escriba exactamente: {oid}")
            o.update(estado=REVISADA, actualizada=_ahora(),
                     revision={"por": str(por)[:40], "nota": redact(nota.strip())[:300], "en": _ahora()})
            self._guardar()
            return dict(o)

    def _guardar_sin_lanzar(self) -> None:
        try:
            self._guardar()
        except OSError:
            # El cambio sigue en memoria; al no poder escribir, una reapertura verá «en_curso» → «incierta» (seguro).
            self.eventos.append({"event": "operaciones_escritura_fallida"})
