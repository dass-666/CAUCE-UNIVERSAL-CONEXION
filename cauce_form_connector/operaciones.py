"""Registro local y durable de operaciones confirmadas por una persona (protección contra repeticiones).

Reglas (todas con pruebas en ``tests/test_operaciones.py`` y ``tests/test_envio_protegido.py``):

1. Una acción confirmada se ejecuta COMO MÁXIMO UNA VEZ. El registro se escribe en disco (fsync + reemplazo atómico)
   en estado ``en_curso`` ANTES del clic. Si no se puede escribir, no se pulsa nada.
2. Nada se reenvía solo. Ningún código de Forms vuelve a pulsar por su cuenta.
3. Resultado desconocido → ``incierta``. Solo dejan de estar abiertas por EVIDENCIA o por una PERSONA:
   - ``confirmada``: el portal propio de laboratorio confirmó por su API el acuse (folio) de ESE envío;
   - ``revisada``: un operador la revisó EN EL PUESTO (``cauce-forms operaciones revisar`` con nota, resultado y
     confirmación escrita).
   Una revisión de red en Network Engine (``resolve`` de la marca o ``GET /sessions/{id}/reviews`` de E10) solo AÑADE
   información (``revision_red``): nunca cambia el estado ni desbloquea (defecto A1 de E10).
   Recuperar internet, reiniciar, cerrar la sesión, revocar la credencial o que el motor «olvide» la marca NO cuentan.
4. Un reinicio de Forms convierte toda operación ``en_curso`` en ``incierta`` (``forms_reiniciado_durante_operacion``).
5. Archivo dañado: se usa la copia ``.bak`` y TODO lo abierto pasa a revisión; si tampoco vale, se aparta el archivo
   (``.corrupt-<fecha>``, nunca se borra) y una marca GENERAL bloquea nuevos envíos hasta que un operador la revise.
6. Un expediente con una operación ``en_curso``, ``incierta`` o ``confirmada`` no se puede volver a enviar. Una
   ``revisada`` también bloquea, salvo que el operador haya dejado escrito que el portal NO lo recibió
   (``resultado = no_recibido``). Revisar sin decir el resultado (o resolver la marca en Network Engine) quita la
   alerta, pero no desbloquea el expediente.
7. Un clic observado NO es una recepción: ``envio_observado`` solo es un indicio; ``confirmada`` exige el acuse.
8. Un envío que la PERSONA hace en la ventana (botón, tecla Intro o JavaScript de la página) queda ``incierta``.

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
import sys
import threading
import time
from contextlib import contextmanager
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
ESPERA_CANDADO_S = 30.0                               # A6: espera máxima por el candado entre procesos
MAX_CERRADAS = 500                                    # las abiertas nunca se descartan

CAMPOS = {"id", "creada", "actualizada", "estado", "motivo", "huella", "origen", "token_portal", "folio",
          "sesion_ne", "marca_ne", "revision", "aviso_en_curso", "envio_observado", "api_acuse",
          "posts_vistos", "ref_solicitud", "revision_red", "destino"}
RESULTADOS_REVISION = ("recibido", "no_recibido", "sin_determinar")
_SOL_RE = re.compile(r"^sol_[0-9a-f]{16,32}$")
_ID_RE = re.compile(r"^op_[0-9a-f]{12}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_FOLIO_RE = re.compile(r"^[A-Z0-9-]{4,32}$")
_SID_RE = re.compile(r"^bs_[0-9a-f]{12}$")
_API_RE = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d{1,5})?/[A-Za-z0-9/_-]{0,80}$")


def _bloquea(o: dict) -> bool:
    """¿Esta operación impide volver a enviar su expediente?"""
    if o["estado"] in BLOQUEAN_REPETICION:
        return True
    if o["estado"] == REVISADA:
        # Solo «no_recibido», escrito por un operador, desbloquea. Una revisión antigua sin resultado, la resolución
        # de la marca en Network Engine o «sin_determinar» siguen bloqueando (conservador).
        return ((o.get("revision") or {}).get("resultado") or "sin_determinar") != "no_recibido"
    return False


class OperacionBloqueada(PermissionError):
    def __init__(self, codigo: str, mensaje: str, operacion: Optional[str] = None):
        super().__init__(mensaje)
        self.codigo, self.operacion = codigo, operacion


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resumen(ops: list, general: Optional[dict], archivadas: Optional[dict] = None, clave_id: Optional[str] = None) -> str:
    d: dict[str, Any] = {"o": ops, "g": general}
    if archivadas:                       # solo si existen: los archivos anteriores siguen siendo válidos
        d["a"] = archivadas
    if clave_id:
        d["k"] = clave_id
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


_HUELLA_RE = re.compile(r"^h_[0-9a-f]{24}$")
# Identidad común del expediente (interfaz y preparar): formulario de destino + una huella por campo escrito
_DESTINO_RE = re.compile(r"^d_[0-9a-f]{16}(\.[0-9a-f]{16}){1,300}$")
_CLAVE_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def _sid_usuario() -> Optional[str]:
    import subprocess
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=10)
    sid = out.stdout.strip().split(",")[-1].strip('"')
    return sid if re.match(r"^S-1-[0-9-]+$", sid) else None


def proteger_archivo(archivo: Path) -> str:
    """Como ``proteger_carpeta`` pero para UN archivo (p. ej. un respaldo en una carpeta que elige la persona: no se
    toca la ACL de esa carpeta). Windows: sin herencia, acceso solo del usuario actual y SYSTEM."""
    if sys.platform != "win32":
        return "posix"
    import subprocess
    try:
        sid = _sid_usuario()
        if not sid:
            return "heredada"
        r = subprocess.run(["icacls", str(archivo), "/inheritance:r", "/grant:r", f"*{sid}:F",
                            "/grant:r", "*S-1-5-18:F", "/Q"], capture_output=True, text=True, timeout=30)
        return "acl_usuario" if r.returncode == 0 else "heredada"
    except (OSError, subprocess.SubprocessError):
        return "heredada"


def proteger_carpeta(carpeta: Path) -> str:
    """Permisos EFECTIVOS de la carpeta de estado (clave de huella, registro, identidad del puesto).

    Windows: ``os.open(..., 0o600)`` solo marca «solo lectura»; los permisos reales son la ACL heredada de la carpeta
    (en una carpeta creada en C:\\ suelen incluir a «Usuarios autenticados»). Se quita la herencia y se deja acceso
    total SOLO al usuario actual y a SYSTEM (``icacls``, sin shell). Devuelve ``acl_usuario`` | ``heredada`` (no se
    pudo; se informa) | ``posix`` (los archivos ya se crean 0600)."""
    if sys.platform != "win32":
        return "posix"
    marca = carpeta / ".acl_aplicada"
    if marca.exists():
        # Una sola vez: repetir icacls en cada arranque dejaba un instante sin permisos y otros procesos (la orden
        # de operador, el Forms que envía) recibían «Permission denied» (visto en el CI de Windows).
        return "acl_usuario"
    import subprocess
    try:
        sid = _sid_usuario()
        if not sid:
            return "heredada"
        # 1) SOLO la carpeta: sin herencia de arriba; usuario y SYSTEM con herencia hacia dentro (OI)(CI).
        #    (Con /T esos mismos argumentos se aplicaban a cada archivo: quedaban sin herencia y, en el CI de Windows,
        #    con «Permission denied» incluso para el propio usuario.)
        r = subprocess.run(["icacls", str(carpeta), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F",
                            "/grant:r", "*S-1-5-18:(OI)(CI)F", "/Q"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return "heredada"
        # 2) lo que ya existía dentro vuelve a heredar, ahora de la carpeta protegida
        if any(carpeta.iterdir()):
            subprocess.run(["icacls", str(carpeta / "*"), "/reset", "/T", "/Q"], capture_output=True, text=True,
                           timeout=30)
        marca.write_bytes(b"")
        return "acl_usuario"
    except (OSError, subprocess.SubprocessError):
        return "heredada"


def _limpia(o: Any) -> Optional[dict]:
    """Valida una operación leída del disco. Lo que no encaje se descarta (y el archivo cuenta como dañado)."""
    if not isinstance(o, dict) or not _ID_RE.match(str(o.get("id", ""))) or o.get("estado") not in ESTADOS:
        return None
    if set(o) - CAMPOS:
        return None
    if o.get("destino") is not None and not (isinstance(o["destino"], str) and _DESTINO_RE.match(o["destino"])):
        return None
    return dict(o)


def _partes_destino(d: Optional[str]) -> Optional[tuple[str, frozenset]]:
    if not d or not _DESTINO_RE.match(d):
        return None
    f, *c = d.split(".")
    return f, frozenset(c)


def destinos_casan(a: Optional[str], b: Optional[str]) -> bool:
    """Mismo expediente para el portal: mismo formulario y los campos escritos de uno están todos, con el mismo valor,
    en el otro (uno contiene al otro). Conservador: si una modalidad escribió campos de más, sigue casando."""
    pa, pb = _partes_destino(a), _partes_destino(b)
    if not pa or not pb or pa[0] != pb[0]:
        return False
    return pa[1] <= pb[1] or pb[1] <= pa[1]


VIVO = "operaciones.vivo"


def _candado_exclusivo(ruta: Path):
    """Candado exclusivo SIN espera sobre ``ruta``. Devuelve el archivo abierto (mantenerlo abierto lo sostiene) o None."""
    fh = open(ruta, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def _otro_forms_vivo(ruta: Path) -> bool:
    if not ruta.exists():
        return False
    fh = _candado_exclusivo(ruta)
    if fh is None:
        return True
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        fh.close()
    return False


class RegistroOperaciones:
    def __init__(self, data_dir: str | Path, dueno: bool = False):
        """``dueno=True`` solo para el Forms que envía (la interfaz web): mientras viva, sostiene ``operaciones.vivo``.
        Así otro proceso (la orden de operador) sabe que una operación ``en_curso`` está VIVA y no la convierte en
        incierta por su cuenta; solo lo hace si nadie sostiene ese candado (Forms se cerró o se cayó)."""
        self.dir = Path(data_dir)
        self._vivo_fh = None
        if dueno:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._vivo_fh = _candado_exclusivo(self.dir / VIVO)
        self.otro_forms_en_marcha = dueno and self._vivo_fh is None
        self.path = self.dir / ARCHIVO
        self.bak = self.dir / (ARCHIVO + ".bak")
        self._lock = threading.RLock()
        self.ops: list[dict] = []
        self.general: Optional[dict] = None      # marca general: bloquea todo envío hasta revisión humana
        self.estado_carga = "nuevo"              # nuevo | ok | respaldo | ilegible
        self.eventos: list[dict] = []            # para la auditoría del llamador
        self._sha_conocido: Optional[str] = None # resumen de lo último leído o escrito por ESTE proceso
        self._profundidad = 0
        self.archivadas: dict[str, str] = {}     # huellas de confirmadas que ya salieron de la lista (nunca se borran)
        self._clave_persistida = False
        self.proteccion = "sin_carpeta"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.proteccion = proteger_carpeta(self.dir)
        except OSError:
            pass
        self._clave = self._leer_clave()
        self.clave_id = hashlib.sha256(self._clave).hexdigest()[:16]
        if self.otro_forms_en_marcha:
            self.eventos.append({"event": "operaciones_otro_forms_en_marcha"})
        with self._candado():
            self._cargar()

    def soltar(self) -> None:
        """Forms se cierra de forma ordenada: deja de sostener ``operaciones.vivo``."""
        fh, self._vivo_fh = self._vivo_fh, None
        if fh is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                fh.close()

    # ------------------------------------------------------------------ clave de huella
    def _leer_clave(self) -> bytes:
        p = self.dir / CLAVE
        try:
            k = p.read_bytes()
            if len(k) == 32:
                self._clave_persistida = True
                return k
        except OSError:
            pass
        k = secrets.token_bytes(32)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            if p.exists():
                # existe pero está dañada: se aparta (no se borra) y el registro detectará la clave distinta
                p.replace(p.with_name(f"{CLAVE}.danada-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"))
            # O_EXCL: si otro proceso la crea a la vez, se usa la suya (antes O_TRUNC podía pisarla)
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(k)
                fh.flush()
                os.fsync(fh.fileno())
            self._clave_persistida = True
        except FileExistsError:
            try:
                k2 = p.read_bytes()
                if len(k2) == 32:
                    self._clave_persistida = True
                    return k2
            except OSError:
                pass
        except OSError:
            pass                                     # sin disco: la huella valdría solo en este proceso → no se envía
        return k

    def huella(self, fila: dict[str, Any]) -> str:
        """Huella del expediente: HMAC-SHA256 (clave local aleatoria) de los valores ordenados por columna."""
        canon = json.dumps({str(k): str(v) for k, v in sorted(fila.items())}, ensure_ascii=False, sort_keys=True)
        return "h_" + hmac.new(self._clave, canon.encode("utf-8"), hashlib.sha256).hexdigest()[:24]

    def destino(self, url: str, escrituras: dict[str, Any]) -> Optional[str]:
        """Identidad COMÚN del expediente, igual en la interfaz y en ``preparar``: formulario de destino (esquema,
        servidor, puerto y ruta, sin consulta) y, por cada campo que se escribe (clave del campo en la página → valor),
        un HMAC con la clave local. No guarda ningún valor ni nombre de columna. None si no se escribe nada."""
        from urllib.parse import urlsplit
        u = urlsplit(url or "")
        puerto = u.port or {"http": 80, "https": 443}.get(u.scheme, "")
        f = f"{u.scheme}://{(u.hostname or '').lower()}:{puerto}{u.path or '/'}"
        mac = lambda t: hmac.new(self._clave, t.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
        campos = sorted({mac(json.dumps(["campo", str(k), str(v)], ensure_ascii=False))
                         for k, v in escrituras.items() if k and v is not None and str(v) != ""})
        if not campos:
            return None
        return ".".join(["d_" + mac(json.dumps(["formulario", f], ensure_ascii=False)), *campos[:300]])

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
        a = d.get("confirmadas_archivadas") or {}
        k = d.get("clave_id")
        if not isinstance(a, dict) or not all(isinstance(h, str) and (_HUELLA_RE.match(h) or _DESTINO_RE.match(h))
                                              and isinstance(f, str)
                                              for h, f in a.items()):
            return None
        if k is not None and not (isinstance(k, str) and _CLAVE_ID_RE.match(k)):
            return None
        if d.get("sha256") != _resumen(ops, g, a, k):
            return None                              # editado a mano o escrito a medias: no vale
        return {"operaciones": ops, "general": g, "sha256": d["sha256"], "archivadas": a, "clave_id": k}

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
        self.archivadas = dict(doc["archivadas"])
        self._sha_conocido = doc["sha256"]
        cambio = False
        if doc["clave_id"] and doc["clave_id"] != self.clave_id and self.general is None:
            # huella.key se perdió o cambió: las huellas antiguas ya no casan y la protección contra duplicados
            # de lo enviado antes NO funcionaría. Nada se envía hasta que un operador lo revise.
            self.general = {"motivo": "clave_de_huella_distinta", "desde": _ahora()}
            self.eventos.append({"event": "operaciones_clave_de_huella_distinta"})
            cambio = True
        hay_forms_vivo = self._vivo_fh is None and _otro_forms_vivo(self.dir / VIVO)
        for o in self.ops:
            if o["estado"] == EN_CURSO and not hay_forms_vivo:
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
                for o in self.ops:
                    # una confirmada (o revisada que sigue bloqueando) que sale de la lista conserva su bloqueo
                    if o["id"] in quitar and _bloquea(o) and _HUELLA_RE.match(o.get("huella") or ""):
                        self.archivadas[o["huella"]] = (o.get("creada") or "")[:10]
                    if o["id"] in quitar and _bloquea(o) and _DESTINO_RE.match(o.get("destino") or ""):
                        self.archivadas[o["destino"]] = (o.get("creada") or "")[:10]
                self.ops = [o for o in self.ops if o["id"] not in quitar]
            doc = {"esquema": ESQUEMA, "operaciones": self.ops, "general": self.general,
                   "confirmadas_archivadas": self.archivadas, "clave_id": self.clave_id,
                   "sha256": _resumen(self.ops, self.general, self.archivadas, self.clave_id)}
            self._sha_conocido = doc["sha256"]
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

    @contextmanager
    def _candado(self):
        """Candado ENTRE PROCESOS (Forms y la orden de operador pueden escribir el mismo archivo)."""
        with self._lock:
            if self._profundidad:                   # reentrante en el mismo hilo (iniciar → comprobar)
                self._profundidad += 1
                try:
                    yield
                finally:
                    self._profundidad -= 1
                return
            self.dir.mkdir(parents=True, exist_ok=True)
            fh = open(self.dir / "operaciones.lock", "a+b")
            # A6 de E10: espera con tope (antes, en Windows, un bucle sin fin colgaba la interfaz sin mensaje). Al
            # agotarse se lanza OSError y la capa superior no envía.
            limite = time.monotonic() + ESPERA_CANDADO_S
            while True:
                try:
                    if sys.platform == "win32":
                        import msvcrt
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= limite:
                        fh.close()
                        raise OSError("operaciones.lock: otro proceso retiene el registro de operaciones "
                                      f"más de {ESPERA_CANDADO_S:.0f} s; no se envía") from None
                    time.sleep(0.05)
            try:
                self._profundidad = 1
                yield
            finally:
                self._profundidad = 0
                try:
                    if sys.platform == "win32":
                        import msvcrt
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                finally:
                    fh.close()

    def _recargar(self) -> None:
        """Si OTRO proceso cambió el archivo (p. ej. un operador revisó una operación), se adopta su versión válida.
        Un archivo ilegible durante la ejecución NO se adopta: lo de memoria es más reciente y se reescribirá."""
        doc = self._leer(self.path)
        if doc is not None and doc["sha256"] != self._sha_conocido:
            self.ops, self.general, self._sha_conocido = doc["operaciones"], doc["general"], doc["sha256"]
            self.archivadas = dict(doc["archivadas"])

    # ------------------------------------------------------------------ consultas
    def get(self, oid: str) -> Optional[dict]:
        with self._candado():
            self._recargar()
            return next((dict(o) for o in self.ops if o["id"] == oid), None)

    def abiertas(self) -> list[dict]:
        with self._candado():
            self._recargar()
            return [dict(o) for o in self.ops if o["estado"] in ABIERTOS]

    def hay_abiertas(self) -> bool:
        with self._candado():
            self._recargar()
            return self.general is not None or any(o["estado"] in ABIERTOS for o in self.ops)

    def pide_revision(self) -> bool:
        """¿Hay algo que una PERSONA debe revisar? Inciertas o revisión general.

        No cuenta la operación ``en_curso`` de este mismo proceso: es la que se está ejecutando ahora (un portal lento
        puede tardar varios latidos) y todavía no es incierta. Una ``en_curso`` que venga de un Forms anterior ya se
        convirtió en ``incierta`` al cargar el registro, así que no se pierde."""
        with self._candado():
            self._recargar()
            return self.general is not None or any(o["estado"] == INCIERTA for o in self.ops)

    def lista(self, limite: int = 50) -> list[dict]:
        with self._candado():
            self._recargar()
            return [dict(o) for o in self.ops[-limite:]][::-1]

    def comprobar(self, huella: str, destino: Optional[str] = None) -> None:
        """Lanza ``OperacionBloqueada`` si NO se puede enviar este expediente ahora. Casa por la huella de la modalidad
        (``huella``) o por la identidad común (``destino``), así un expediente enviado desde la interfaz queda
        protegido en ``preparar`` y al revés."""
        with self._candado():
            self._recargar()
            if self.general is not None:
                raise OperacionBloqueada("revision_general_pendiente",
                                         "El registro de operaciones necesita revisión de un operador "
                                         f"({self.general['motivo']}): no se envía nada hasta entonces.")
            arch = next((h for h in self.archivadas if h == huella or (destino and destinos_casan(h, destino))), None)
            if arch is not None:
                raise OperacionBloqueada("expediente_confirmada", "No se envía: este expediente ya consta como recibido "
                                         f"por el portal (archivado, {self.archivadas[arch]}).")
            for o in self.ops:
                if (o.get("huella") == huella or (destino and destinos_casan(o.get("destino"), destino))) \
                        and _bloquea(o):
                    if o["estado"] == REVISADA:
                        raise OperacionBloqueada("expediente_revisada", "No se envía: un operador revisó un envío "
                                                 "anterior de este expediente sin dejar escrito que el portal no lo "
                                                 f"recibió ({o['id']}).", o["id"])
                    texto = {EN_CURSO: "ya hay un envío en curso de este expediente",
                             INCIERTA: "un envío anterior de este expediente tiene resultado desconocido y debe revisarse",
                             CONFIRMADA: "este expediente ya consta como recibido por el portal"}[o["estado"]]
                    raise OperacionBloqueada(f"expediente_{o['estado']}", f"No se envía: {texto} ({o['id']}).", o["id"])

    # ------------------------------------------------------------------ transiciones
    def iniciar(self, huella: str, origen: str, token_portal: Optional[str], sesion_ne: Optional[str],
                api_acuse: Optional[str] = None, destino: Optional[str] = None) -> dict:
        """Crea la operación ``en_curso`` y la guarda en disco. Lanza OSError si no puede (entonces NO se envía)."""
        if not self._clave_persistida:
            raise OSError("huella.key no se pudo guardar: la protección contra duplicados no sobreviviría a un reinicio")
        if self.otro_forms_en_marcha:
            # Otra interfaz de Forms ya trabaja con este registro. Si esta enviara, la orden de operador no sabría
            # cuál de las dos sigue viva. Se reintenta el candado (la otra pudo cerrarse) y, si no, no se envía.
            self._vivo_fh = _candado_exclusivo(self.dir / VIVO)
            self.otro_forms_en_marcha = self._vivo_fh is None
            if self.otro_forms_en_marcha:
                raise OperacionBloqueada("otro_forms_en_marcha", "No se envía: hay otra ventana de Cauce Forms abierta "
                                         "con el mismo registro de operaciones. Ciérrela y vuelva a intentarlo.")
        with self._candado():
            self._recargar()
            self.comprobar(huella, destino)
            o = {"id": "op_" + secrets.token_hex(6), "creada": _ahora(), "actualizada": _ahora(), "estado": EN_CURSO,
                 "motivo": None, "huella": huella, "origen": str(origen)[:200],
                 "token_portal": token_portal if token_portal and _TOKEN_RE.match(token_portal) else None,
                 "folio": None, "sesion_ne": sesion_ne if sesion_ne and _SID_RE.match(sesion_ne) else None,
                 "marca_ne": None, "revision": None, "aviso_en_curso": None, "envio_observado": None,
                 # solo la API del portal PROPIO (127.0.0.1/localhost), para verificar el acuse más tarde
                 "api_acuse": api_acuse if api_acuse and _API_RE.match(api_acuse) else None,
                 "destino": destino if destino and _DESTINO_RE.match(destino) else None}
            self.ops.append(o)
            try:
                self._guardar()
            except OSError:
                self.ops.remove(o)
                raise
            return dict(o)

    def registrar_envio_humano(self, huella: str, origen: str, motivo: str = "envio_humano_directo_sin_acuse",
                               ref_solicitud: Optional[str] = None, destino: Optional[str] = None) -> dict:
        """La PERSONA envió desde la ventana del navegador (sin pasar por ENVIAR). Forms no vio el resultado ni puede
        verificarlo: queda ``incierta`` y bloquea repetir ese expediente hasta que un operador lo revise.
        No comprueba bloqueos (el envío ya ocurrió) y, si no puede escribir, lanza OSError para que se audite."""
        if not isinstance(huella, str) or not huella.startswith("h_"):
            raise ValueError("huella")
        with self._candado():
            self._recargar()
            o = {"id": "op_" + secrets.token_hex(6), "creada": _ahora(), "actualizada": _ahora(), "estado": INCIERTA,
                 "motivo": str(motivo)[:80], "huella": huella, "origen": str(origen)[:200],
                 "token_portal": None, "folio": None, "sesion_ne": None, "marca_ne": None, "revision": None,
                 "aviso_en_curso": None, "envio_observado": True, "api_acuse": None,
                 # id opaco de la solicitud de Monitor (modo preparar): permite cruzar este registro con el de Monitor
                 "ref_solicitud": ref_solicitud if ref_solicitud and _SOL_RE.match(ref_solicitud) else None,
                 "destino": destino if destino and _DESTINO_RE.match(destino) else None}
            self.ops.append(o)
            try:
                self._guardar()
            except OSError:
                self.ops.remove(o)
                raise
            return dict(o)

    def actualizar(self, oid: str, **campos: Any) -> dict:
        with self._candado():
            self._recargar()
            o = next(x for x in self.ops if x["id"] == oid)
            for k, v in campos.items():
                if k in ("aviso_en_curso", "envio_observado"):
                    o[k] = None if v is None else bool(v)
                elif k == "posts_vistos" and isinstance(v, int) and not isinstance(v, bool):
                    o[k] = max(0, min(v, 99))      # peticiones de escritura que el navegador hizo tras el clic
            o["actualizada"] = _ahora()
            self._guardar_sin_lanzar()
            return dict(o)

    def cerrar(self, oid: str, estado: str, motivo: Optional[str] = None, folio: Optional[str] = None) -> dict:
        """Resultado del envío. Solo admite pasar de ``en_curso`` a un estado final o a ``incierta``."""
        if estado not in (CONFIRMADA, INCIERTA, NO_ENVIADA, RECHAZADA):
            raise ValueError(estado)
        with self._candado():
            self._recargar()
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
        with self._candado():
            self._recargar()
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
        with self._candado():
            self._recargar()
            cambio = False
            for o in self.ops:
                if o["estado"] == INCIERTA and o.get("marca_ne") is None:
                    o["marca_ne"], cambio = sid, True
            if cambio:
                self._guardar_sin_lanzar()

    def resuelta_por_operador_ne(self, sid: str, revision: Optional[dict] = None) -> list[str]:
        """El operador resolvió la marca de la sesión ``sid`` en Network Engine.

        SOLO se anota (``revision_red``). La operación sigue ``incierta`` y bloqueando: una revisión de red no es un
        acuse del portal (contrato E10: ``confirms_receipt: false``). Antes (e55a45d) pasaba a ``revisada`` y el
        expediente se podía reenviar: duplicado reproducido por E10 (A1). Solo la revisión en el puesto la cierra."""
        info = {"en": _ahora(), "por": "operador_network_engine", "confirma_recepcion": False}
        if isinstance(revision, dict):
            if re.fullmatch(r"rv_[0-9a-f]{12}", str(revision.get("id", ""))):
                info["id"] = revision["id"]
            if re.fullmatch(r"[0-9a-f]{16}", str(revision.get("note_sha256") or "")):
                info["nota_sha256"] = revision["note_sha256"]
        out = []
        with self._candado():
            self._recargar()
            for o in self.ops:
                if o["estado"] == INCIERTA and o.get("marca_ne") == sid and o.get("revision_red") is None:
                    o.update(revision_red=dict(info), actualizada=_ahora())
                    out.append(o["id"])
            if out:
                self._guardar_sin_lanzar()
        return out

    def revisar(self, oid: str, nota: str, confirmar: str, por: str = "operador",
                resultado: str = "sin_determinar") -> dict:
        """Revisión de un OPERADOR (línea de órdenes, no la interfaz de la persona). Exige nota y repetir el id.

        ``resultado``: ``recibido`` (el portal lo tiene: sigue bloqueado), ``no_recibido`` (comprobado que no llegó: se
        puede volver a enviar) o ``sin_determinar`` (quita la alerta, sigue bloqueado). Una ``revisada`` con
        ``sin_determinar`` se puede volver a revisar para dejar el resultado."""
        if not isinstance(nota, str) or len(nota.strip()) < 5:
            raise ValueError("Escriba qué revisó (nota de al menos 5 caracteres).")
        if resultado not in RESULTADOS_REVISION:
            raise ValueError("resultado: recibido | no_recibido | sin_determinar")
        with self._candado():
            self._recargar()
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
            sin_resultado = o["estado"] == REVISADA and _bloquea(o) and \
                ((o.get("revision") or {}).get("resultado") or "sin_determinar") == "sin_determinar"
            if o["estado"] not in ABIERTOS and not sin_resultado:
                raise ValueError(f"La operación no está pendiente de revisión ({o['estado']}).")
            if confirmar != oid:
                raise ValueError(f"Para confirmar escriba exactamente: {oid}")
            o.update(estado=REVISADA, actualizada=_ahora(),
                     revision={"por": str(por)[:40], "nota": redact(nota.strip())[:300], "en": _ahora(),
                               "resultado": resultado})
            self._guardar()
            return dict(o)

    def marcar_general(self, motivo: str) -> None:
        """Bloquea TODO envío hasta que un operador lo revise (``operaciones revisar GENERAL``). Lanza OSError."""
        with self._candado():
            self._recargar()
            self.general = {"motivo": str(motivo)[:80], "desde": _ahora()}
            self._guardar()

    def incorporar(self, ops: list, archivadas: dict) -> int:
        """Restauración: añade operaciones (por id) y huellas archivadas de OTRO registro con la MISMA clave.
        Nunca quita ni rebaja nada de lo que ya hay. Devuelve cuántas operaciones añadió. Lanza OSError."""
        with self._candado():
            self._recargar()
            ids = {o["id"] for o in self.ops}
            nuevas = [dict(o) for o in ops if o["id"] not in ids]
            for o in nuevas:
                if o["estado"] == EN_CURSO:      # lo que estaba a medias en el otro registro es desconocido
                    o.update(estado=INCIERTA, motivo="en_curso_al_restaurar", actualizada=_ahora())
            self.ops.extend(nuevas)
            for h, f in archivadas.items():
                self.archivadas.setdefault(h, f)
            self._guardar()
            return len(nuevas)

    def _guardar_sin_lanzar(self) -> None:
        try:
            self._guardar()
        except OSError:
            # El cambio sigue en memoria; al no poder escribir, una reapertura verá «en_curso» → «incierta» (seguro).
            self.eventos.append({"event": "operaciones_escritura_fallida"})
