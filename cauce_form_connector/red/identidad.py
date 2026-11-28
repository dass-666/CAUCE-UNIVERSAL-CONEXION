"""Identidad estable del puesto de trabajo, sin datos personales.

La etiqueta que Forms registra en Network Engine (``label``) debe ser:
- estable entre reinicios de Forms y del equipo (para que Monitor vea siempre el mismo puesto);
- sin nombre de persona, usuario de Windows, nombre del equipo, dirección IP ni MAC;
- válida para el motor: ``^[a-z0-9][a-z0-9_-]{1,31}$``.

Se genera UNA vez al azar (``forms-`` + 8 hexadecimales) y se guarda en ``puesto.json`` dentro de la carpeta de datos
de Forms. Quien instala puede fijarla con ``CAUCE_FORMS_PUESTO`` (p. ej. ``forms-mostrador-2``) si prefiere un nombre
legible; se valida igual y se rechaza si parece un correo, un número largo o lleva espacios.

``puesto.json`` también recuerda el id de la sesión de Network Engine (no es un secreto: sin la credencial no sirve
de nada). Así, tras reiniciar Forms se sigue latiendo con el mismo id en vez de chocar con 409 ``credential_has_session``.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Optional

LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
_PARECE_PERSONAL = re.compile(r"@|\d{7,}")
_SID_RE = re.compile(r"^bs_[0-9a-f]{12}$")
ARCHIVO = "puesto.json"


class IdentidadError(ValueError):
    pass


def validar_etiqueta(v: str) -> str:
    if not LABEL_RE.match(v or ""):
        raise IdentidadError("CAUCE_FORMS_PUESTO: 2-32 caracteres, minúsculas, dígitos, '-' o '_'")
    if _PARECE_PERSONAL.search(v):
        raise IdentidadError("CAUCE_FORMS_PUESTO no debe contener correos ni números largos")
    return v


def _escribir_atomico(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class Puesto:
    """Lee/crea ``puesto.json``. Solo guarda: ``etiqueta``, ``creado`` y ``sesion_ne`` (id ``bs_…`` o null)."""

    def __init__(self, data_dir: str | Path, etiqueta_fija: Optional[str] = None):
        self.path = Path(data_dir) / ARCHIVO
        self._lock = threading.Lock()
        doc = self._leer()
        if etiqueta_fija:
            etiqueta = validar_etiqueta(etiqueta_fija)
        else:
            etiqueta = doc.get("etiqueta") if isinstance(doc.get("etiqueta"), str) and LABEL_RE.match(doc["etiqueta"]) \
                else "forms-" + secrets.token_hex(4)
        sid = doc.get("sesion_ne") if isinstance(doc.get("sesion_ne"), str) and _SID_RE.match(doc["sesion_ne"]) else None
        if doc.get("etiqueta") != etiqueta:
            sid = None                     # otra etiqueta = otro puesto: el id anterior no le pertenece
        self.etiqueta = etiqueta
        self._sid = sid
        if doc != self._doc():
            self._guardar()

    def _leer(self) -> dict:
        try:
            d = json.loads(self.path.read_text("utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _doc(self) -> dict:
        return {"etiqueta": self.etiqueta, "sesion_ne": self._sid}

    def _guardar(self) -> None:
        try:
            _escribir_atomico(self.path, self._doc())
        except OSError:
            pass                            # sin disco se sigue en memoria; solo se pierde la comodidad del id

    @property
    def sesion_ne(self) -> Optional[str]:
        return self._sid

    def recordar_sesion(self, sid: Optional[str]) -> None:
        if sid is not None and not _SID_RE.match(sid):
            return
        with self._lock:
            if sid != self._sid:
                self._sid = sid
                self._guardar()
