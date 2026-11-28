"""Cliente mínimo de Cauce Network Engine para Forms (credencial ``session``; solo biblioteca estándar).

COPIA REDUCIDA Y FIJADA de Network Engine RC3 (ver ``red/PIN.txt``): ``cauce/client.py`` (``CauceAPIError``,
``CauceClient._call`` y ``CauceSessionClient``) y ``cauce/secrets_store.py`` (solo LECTURA de secretos).

Por qué copia y no dependencia: Network Engine no publica un paquete solo-cliente; instalarlo entero dentro de Forms
traería el motor, su CLI y sus permisos a un proceso que solo necesita cuatro llamadas HTTP. La copia es pequeña, no
tiene dependencias y una prueba (``tests/test_red_cliente_pin.py``) compara su origen con el commit fijado.

Qué se quitó a propósito respecto del original:
- Todo lo de administración, rutas, tareas, rotación, tokens y lectura de red: Forms no debe ni poder pedirlo.
- ``write_secret`` y el cifrado DPAPI: Forms nunca crea ni guarda credenciales; solo lee la que preparó el
  administrador con ``cauce token create --scope session --out ARCHIVO`` (protegida con DPAPI en Windows).
Qué se añadió:
- ``scope_check``: el cliente se niega a seguir si el motor indica que la credencial NO es de ámbito ``session``.
- El texto de los errores del motor no se conserva (solo estado HTTP y código), para que nada ajeno llegue a registros.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Optional

# Copia literal de cauce.DEFAULT_API_PORT en Network Engine RC3 (f93dd58).
DEFAULT_API_PORT = 8780
API = "/api/v1"

_MAGIC_DPAPI = b"CAUCE-DPAPI1\n"
_MAGIC_PLAIN = b"CAUCE-PLAIN1\n"


class CauceAPIError(Exception):
    def __init__(self, status: int, code: str, message: str = "", retry_after_s: Optional[float] = None):
        super().__init__(f"{status} {code}")
        self.status, self.code = status, code
        # E10 S1: en 409 credential_has_session el motor dice cuánto esperar (solo el número; nada más se guarda)
        self.retry_after_s = retry_after_s
        # El mensaje del motor no se guarda: podría acabar en un registro de Forms. Basta con estado y código.
        self.message = ""


def _dpapi_unprotect(data: bytes) -> bytes:
    """Descifra con DPAPI (ámbito del usuario actual de Windows). Copia de la rama de lectura del original."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def read_secret(path: str) -> str:
    """Lee una credencial creada por ``cauce token create --out``. Nunca la escribe ni la copia."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(_MAGIC_DPAPI):
        if sys.platform != "win32":
            raise OSError("Secreto cifrado con DPAPI: solo legible en Windows por el mismo usuario")
        return _dpapi_unprotect(raw[len(_MAGIC_DPAPI):]).decode("utf-8").strip()
    if raw.startswith(_MAGIC_PLAIN):
        return raw[len(_MAGIC_PLAIN):].decode("utf-8").strip()
    raise OSError("Formato de secreto desconocido")


def storage_method(path: str) -> str:
    """``dpapi`` | ``file-0600`` | ``desconocido`` (sin leer el secreto)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(len(_MAGIC_DPAPI))
    except OSError:
        return "desconocido"
    if head.startswith(_MAGIC_DPAPI):
        return "dpapi"
    if head.startswith(_MAGIC_PLAIN):
        return "file-0600"
    return "desconocido"


class _Local:
    """Transporte HTTP a 127.0.0.1 sin proxy (copia de ``CauceClient.__init__`` y ``CauceClient._call``)."""

    def __init__(self, token: str, port: int, timeout: float):
        self.base = f"http://127.0.0.1:{int(port)}"
        self.token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method: str, path: str, body: Optional[dict] = None, timeout: Optional[float] = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + API + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=self.timeout if timeout is None else timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8")).get("error", {})
            except Exception:  # noqa: BLE001
                err = {}
            det = err.get("details") if isinstance(err.get("details"), dict) else {}
            ra = det.get("retry_after_s")
            ra = float(ra) if isinstance(ra, (int, float)) and not isinstance(ra, bool) and 0 <= ra <= 3600 else None
            raise CauceAPIError(e.code, str(err.get("code", "http_error"))[:64], retry_after_s=ra) from None


class CauceSessionClient:
    """Dueño de UNA sesión de navegador con una credencial ``session`` (contrato de RC3).

    Solo informa estado: registrar, latir (``ok``/``error`` + ``operation``), consultar su propia sesión y cerrarla.
    """

    # 5 s como en RC3: un latido que cambia la operación se guarda en disco ANTES de responder (Windows: hasta 1,4 s).
    def __init__(self, token: str, port: int = DEFAULT_API_PORT, timeout: float = 5.0):
        if not token or any(c.isspace() for c in token):
            raise ValueError("credencial vacía o con espacios")
        self._c = _Local(token, port, timeout)
        self.session_id: Optional[str] = None

    @classmethod
    def from_token_file(cls, token_path: str, port: int = DEFAULT_API_PORT, **kw) -> "CauceSessionClient":
        return cls(read_secret(token_path), port=port, **kw)

    def register(self, label: str, connection_id: Optional[str] = None, heartbeat_timeout_s: float = 30.0) -> dict:
        body = {"label": label, "authorized_by_user": True, "heartbeat_timeout_s": heartbeat_timeout_s}
        if connection_id is not None:
            body["connection_id"] = connection_id
        out = self._c.call("POST", "/sessions", body)
        self.session_id = out["id"]
        return out

    def _sid(self) -> str:
        if not self.session_id:
            raise RuntimeError("Primero register()")
        return self.session_id

    def heartbeat(self, ok: bool = True, error_code: Optional[str] = None, operation: Optional[str] = None,
                  timeout: Optional[float] = None, local_review: Optional[str] = None) -> dict:
        body: dict[str, Any] = {"status": "ok" if ok else "error"}
        if not ok:
            body["error_code"] = error_code or "other"
        if operation is not None:
            body["operation"] = operation          # "idle" | "in_progress"
        if local_review is not None:
            body["local_review"] = local_review    # E10: "none" | "pending" (operaciones locales sin revisar)
        return self._c.call("POST", f"/sessions/{self._sid()}/heartbeat", body, timeout=timeout)

    def reviews(self, timeout: Optional[float] = None) -> dict:
        """E10 S3: revisiones administrativas de las marcas de esta credencial/etiqueta. Solo informativo:
        ``confirms_receipt`` es siempre False; nunca desbloquea ni confirma una operación local."""
        return self._c.call("GET", f"/sessions/{self._sid()}/reviews", timeout=timeout)

    def get(self) -> dict:
        return self._c.call("GET", f"/sessions/{self._sid()}")

    def close(self) -> dict:
        out = self._c.call("DELETE", f"/sessions/{self._sid()}")
        self.session_id = None
        return out

    def ping(self) -> dict:
        return self._c.call("GET", "/ping")


def env_path(name: str) -> Optional[str]:
    v = (os.environ.get(name) or "").strip()
    return v or None
