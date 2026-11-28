"""Portal FICTICIO de laboratorio con acuse verificable (solo 127.0.0.1, solo datos ficticios).

Sirve para probar la protección de operaciones de Forms sin tocar ningún portal real. No es un portal institucional
ni imita a ninguno: es un trámite inventado («Trámite de laboratorio Cauce»).

Qué ofrece:
- ``GET /tramite``: formulario con un ``cauce_token_formulario`` opaco por carga de página y metadatos
  ``cauce-portal-laboratorio`` / ``cauce-acuse-api`` para que Forms sepa que puede verificar el acuse.
- ``POST /tramite``: registra la recepción UNA vez por token (idempotente: el mismo token devuelve el mismo folio y
  cuenta un «reintento»). Devuelve una página de acuse con ``<meta name="cauce-acuse" content="FOLIO">``.
- ``GET /api/acuse/{token}``: ``{"recibido": bool, "folio": str|null}``. Es lo ÚNICO que confirma una recepción.
- Límites de capacidad: recepciones totales (``capacidad``) y por minuto (``por_minuto``) → 429 sin registrar nada.
- Fallos deliberados (``POST /_lab/fallo``), uno o varios seguidos, para las pruebas:
  ``lento`` (tarda N s y registra), ``cortar_antes`` (cierra la conexión SIN registrar), ``cortar_despues`` (registra y
  cierra la conexión sin responder: el clásico «¿llegó o no?»), ``error_500`` (sin registrar), ``acuse_lento`` (la
  API de acuse tarda N s).
- ``GET /_lab/estadisticas``: recepciones, reintentos con el mismo token y DUPLICADOS (mismo expediente ficticio con
  tokens distintos), para que las pruebas lo comprueben desde fuera de Forms.

Guarda solo: token, folio, instante y una huella SHA-256 del número de expediente ficticio (para contar duplicados).
Solo biblioteca estándar (``http.server``), para poder cortar conexiones de verdad.
Arranque: ``python lab_portal/servidor.py --port 8790``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs

FORM = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Trámite de laboratorio Cauce (ficticio)</title>
<meta name="cauce-portal-laboratorio" content="1">
<meta name="cauce-acuse-api" content="/api/acuse/">
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:24px auto;padding:0 16px}}
label{{display:block;margin-top:10px;font-weight:600}}input,select{{width:100%;padding:6px}}
.aviso{{background:#fff4ce;padding:8px;border-radius:6px}}button{{margin-top:16px;padding:8px 16px}}</style></head>
<body><h1>Trámite de laboratorio Cauce</h1>
<p class="aviso">Portal FICTICIO de pruebas. No envíe datos reales.</p>
<form id="tramite" method="post" action="/tramite">
<input type="hidden" name="cauce_token_formulario" value="{token}">
<label for="expediente">Número de expediente (ficticio)</label>
<input id="expediente" name="expediente" required pattern="EXP-[0-9]{{4,8}}" placeholder="EXP-0001">
<label for="nombre">Nombre completo</label><input id="nombre" name="nombre" required autocomplete="name">
<label for="correo">Correo electrónico</label><input id="correo" name="correo" type="email" required>
<label for="telefono">Teléfono</label><input id="telefono" name="telefono" type="tel" required pattern="[0-9 +()-]{{8,20}}">
<label for="tipo">Tipo de trámite</label>
<select id="tipo" name="tipo" required><option value="">— elija —</option><option value="alta">Alta</option>
<option value="cambio">Cambio de datos</option><option value="baja">Baja</option></select>
<button type="submit">Enviar trámite</button>
</form></body></html>"""

ACUSE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Acuse de recepción (laboratorio)</title>
<meta name="cauce-acuse" content="{folio}"><meta name="cauce-acuse-token" content="{token}"></head>
<body style="font-family:system-ui,sans-serif;max-width:640px;margin:24px auto">
<h1>Acuse de recepción</h1><p>Folio: <strong id="folio">{folio}</strong></p>
<p>{nota}</p><p><em>Portal ficticio de laboratorio.</em></p></body></html>"""

RECHAZO = """<!doctype html><html lang="es"><head><meta charset="utf-8"><title>No recibido</title>
<meta name="cauce-rechazo" content="{codigo}"></head><body><h1>Trámite no recibido</h1><p>{texto}</p></body></html>"""

FALLOS = ("lento", "cortar_antes", "cortar_despues", "error_500", "acuse_lento")


class Portal:
    def __init__(self, capacidad: int = 1000, por_minuto: int = 120):
        self.capacidad, self.por_minuto = capacidad, por_minuto
        self.lock = threading.Lock()
        self.tokens: set[str] = set()                 # tokens emitidos (un formulario cargado = un token)
        self.recibidos: dict[str, dict] = {}          # token -> {folio, t, exp}
        self.reintentos_mismo_token = 0
        self.rechazos: dict[str, int] = {}
        self.fallos: list[dict] = []                  # cola de fallos a aplicar a los siguientes POST
        self.acuse_retardo_s = 0.0
        self.peticiones_post = 0

    def emitir(self) -> str:
        t = secrets.token_urlsafe(16)
        with self.lock:
            self.tokens.add(t)
        return t

    def estadisticas(self) -> dict[str, Any]:
        with self.lock:
            por_exp: dict[str, int] = {}
            for r in self.recibidos.values():
                por_exp[r["exp"]] = por_exp.get(r["exp"], 0) + 1
            return {"recibidos": len(self.recibidos), "expedientes_distintos": len(por_exp),
                    "duplicados": sum(n - 1 for n in por_exp.values() if n > 1),
                    "reintentos_mismo_token": self.reintentos_mismo_token, "rechazos": dict(self.rechazos),
                    "peticiones_post": self.peticiones_post, "fallos_pendientes": [f["tipo"] for f in self.fallos],
                    "capacidad": self.capacidad, "por_minuto": self.por_minuto}



class _Handler(BaseHTTPRequestHandler):
    server_version = "PortalLabCauce/1"
    portal: Portal

    def log_message(self, *a):            # sin registro por petición (no hay datos que guardar)
        pass

    def _local(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            self._enviar(403, b'{"detail": "solo laboratorio local"}', "application/json")
            return False
        return True

    def _enviar(self, code: int, data: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj: Any) -> None:
        self._enviar(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def _cortar(self) -> None:
        """Cierra el socket sin escribir respuesta (RST): el navegador ve un fallo de red."""
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
        except OSError:
            pass
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _cuerpo(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(min(n, 65536)) if n else b""

    def do_GET(self) -> None:
        if not self._local():
            return
        p = self.portal
        ruta = self.path.split("?")[0]
        if ruta == "/tramite":
            self._enviar(200, FORM.format(token=p.emitir()).encode())
        elif ruta.startswith("/api/acuse/"):
            if p.acuse_retardo_s:
                time.sleep(p.acuse_retardo_s)
            token = ruta[len("/api/acuse/"):]
            with p.lock:
                r = p.recibidos.get(token)
            self._json(200, {"recibido": r is not None, "folio": r["folio"] if r else None})
        elif ruta == "/_lab/estadisticas":
            self._json(200, p.estadisticas())
        else:
            self._enviar(404, b"no encontrado")

    def do_POST(self) -> None:
        if not self._local():
            return
        p = self.portal
        ruta = self.path.split("?")[0]
        raw = self._cuerpo()
        if ruta == "/tramite":
            return self._recibir(raw)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._json(400, {"detail": "JSON no válido"})
        if ruta == "/_lab/fallo":
            tipo = body.get("tipo")
            if tipo not in FALLOS:
                return self._json(400, {"detail": f"tipo: {FALLOS}"})
            with p.lock:
                if tipo == "acuse_lento":
                    p.acuse_retardo_s = float(body.get("segundos", 0))
                else:
                    for _ in range(int(body.get("veces", 1))):
                        p.fallos.append({"tipo": tipo, "segundos": float(body.get("segundos", 8))})
            return self._json(200, p.estadisticas())
        if ruta == "/_lab/limites":
            with p.lock:
                p.capacidad = int(body.get("capacidad", p.capacidad))
                p.por_minuto = int(body.get("por_minuto", p.por_minuto))
            return self._json(200, p.estadisticas())
        if ruta == "/_lab/reiniciar":
            with p.lock:
                p.recibidos.clear(); p.tokens.clear(); p.fallos.clear(); p.rechazos.clear()
                p.reintentos_mismo_token = p.peticiones_post = 0
                p.acuse_retardo_s = 0.0
            return self._json(200, p.estadisticas())
        self._enviar(404, b"no encontrado")

    def _recibir(self, raw: bytes) -> None:
        p = self.portal
        form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        token = form.get("cauce_token_formulario", "")
        exp = form.get("expediente", "")
        with p.lock:
            p.peticiones_post += 1
            fallo = p.fallos.pop(0) if p.fallos else None
        tipo = fallo["tipo"] if fallo else None
        if tipo == "lento":
            time.sleep(fallo.get("segundos", 8))
        if tipo == "cortar_antes":
            return self._cortar()
        if tipo == "error_500":
            return self._enviar(500, RECHAZO.format(codigo="error_servidor", texto="Error del portal (simulado).").encode())
        with p.lock:
            if token not in p.tokens:
                p.rechazos["token_desconocido"] = p.rechazos.get("token_desconocido", 0) + 1
                return self._enviar(400, RECHAZO.format(codigo="token_desconocido", texto="Formulario caducado.").encode())
            if token in p.recibidos:                  # idempotente: el mismo envío no crea otro trámite
                p.reintentos_mismo_token += 1
                r = p.recibidos[token]
                if tipo == "cortar_despues":
                    return self._cortar()
                return self._enviar(200, ACUSE.format(folio=r["folio"], token=token,
                                                      nota="Este envío ya se había recibido.").encode())
            ahora = time.time()
            recientes = sum(1 for r in p.recibidos.values() if ahora - r["t"] < 60)
            if len(p.recibidos) >= p.capacidad or recientes >= p.por_minuto:
                p.rechazos["capacidad"] = p.rechazos.get("capacidad", 0) + 1
                return self._enviar(429, RECHAZO.format(codigo="capacidad", texto="Capacidad del portal agotada.").encode())
            folio = f"LAB-{len(p.recibidos) + 1:05d}-{secrets.token_hex(2).upper()}"
            p.recibidos[token] = {"folio": folio, "t": ahora, "exp": hashlib.sha256(exp.encode()).hexdigest()[:16]}
        if tipo == "cortar_despues":
            return self._cortar()
        self._enviar(200, ACUSE.format(folio=folio, token=token, nota="Recibido.").encode())


class ServidorPortal:
    """Portal en un hilo (para pruebas) o en primer plano (``main``)."""

    def __init__(self, port: int = 0, portal: Optional[Portal] = None):
        self.portal = portal or Portal()
        handler = type("H", (_Handler,), {"portal": self.portal})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._t: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def iniciar(self) -> "ServidorPortal":
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="portal-lab")
        self._t.start()
        return self

    def detener(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Portal ficticio de laboratorio (127.0.0.1)")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--capacidad", type=int, default=1000)
    ap.add_argument("--por-minuto", type=int, default=120)
    a = ap.parse_args()
    s = ServidorPortal(a.port, Portal(a.capacidad, a.por_minuto))
    print(f"Portal ficticio de laboratorio en {s.url}/tramite (Ctrl+C para salir)", flush=True)
    try:
        s.httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
