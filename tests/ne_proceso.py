"""Network Engine RC3 REAL como proceso aparte (red SIMULADA del propio motor), para las pruebas de integración.

Se usa la copia de SOLO LECTURA indicada en ``CAUCE_NE_REPO`` (en CI: checkout de dass-666/cauce-network-engine en el
commit fijado de RC3). Sin esa variable, las pruebas que lo necesitan se omiten (y lo dicen).
Las credenciales se crean con la orden oficial ``cauce token create --scope session --out ARCHIVO``.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import pytest

from cauce_form_connector.red.cliente_ne import read_secret

NE_REPO = os.environ.get("CAUCE_NE_REPO", "")
# Network Engine E10 (PR #16, laboratorio): opcional, solo para las pruebas del contrato E10 (local_review, reviews)
NE_E10_REPO = os.environ.get("CAUCE_NE_E10_REPO", "")
E10 = "4f223a7d366a362fbdd64db4ae9a94bf2b63e414"
RC3 = "f93dd58eeddc1cc3032711bb0b6e378708063d41"
necesita_ne = pytest.mark.skipif(not (NE_REPO and Path(NE_REPO, "cauce", "sessions.py").exists()),
                                 reason="CAUCE_NE_REPO no apunta a una copia de Network Engine RC3")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def commit_ne() -> str:
    try:
        return subprocess.run(["git", "-C", NE_REPO, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


class MotorNE:
    def __init__(self, carpeta: Path, port: Optional[int] = None, persist: bool = True, repo: Optional[str] = None):
        self.repo = repo or NE_REPO
        self.dir = Path(carpeta)
        self.data = self.dir / "datos-ne"
        self.port = port or puerto_libre()
        self.persist = persist
        self.proc: Optional[subprocess.Popen] = None
        self.cfg = self.dir / "config-ne.json"
        self.log = self.dir / "ne.log"

    def _escribir_cfg(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg.write_text(json.dumps({"data_dir": str(self.data), "api": {"port": self.port},
                                        "lab": {"rotation_enabled": False},
                                        "sessions": {"max": 8, "persist": self.persist}}), "utf-8")

    def iniciar(self, limite: float = 40) -> "MotorNE":
        self._escribir_cfg()
        tok = self.data / "api.token"
        if tok.exists():
            tok.unlink()                       # el motor lo reescribe al estar listo
        self.proc = subprocess.Popen([sys.executable, "-m", "cauce", "--config", str(self.cfg), "sim", "--port",
                                      str(self.port)], cwd=self.repo, stdout=open(self.log, "a"),
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     env={k: v for k, v in os.environ.items() if not k.startswith("CAUCE_")}
                                     | {"PYTHONUNBUFFERED": "1"})
        t0 = time.time()
        while time.time() - t0 < limite:
            if tok.exists():
                try:
                    self.llamar("GET", "/ping", token="")
                    return self
                except OSError:
                    pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"Network Engine terminó: {self.log.read_text()[-800:]}")
            time.sleep(0.2)
        raise RuntimeError("Network Engine no arrancó a tiempo")

    def detener(self, matar: bool = False) -> None:
        if self.proc and self.proc.poll() is None:
            if matar:
                self.proc.kill()
            else:
                self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        self.proc = None

    def congelar(self) -> None:
        os.kill(self.proc.pid, signal.SIGSTOP)

    def descongelar(self) -> None:
        os.kill(self.proc.pid, signal.SIGCONT)

    @property
    def admin(self) -> str:
        return read_secret(str(self.data / "api.token"))

    def llamar(self, metodo: str, ruta: str, cuerpo: Any = None, token: Optional[str] = None, timeout: float = 5):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/v1{ruta}", method=metodo,
                                     data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
        req.add_header("Content-Type", "application/json")
        tk = self.admin if token is None else token
        if tk:
            req.add_header("Authorization", "Bearer " + tk)
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None

    def credencial(self, nombre: str, ambito: str = "session") -> tuple[str, Path]:
        """Crea una credencial con la orden OFICIAL y la guarda en un archivo protegido. Devuelve (id, ruta)."""
        ruta = self.dir / f"{nombre}.token"
        r = subprocess.run([sys.executable, "-m", "cauce", "--config", str(self.cfg), "token", "create", "--name", nombre,
                            "--scope", ambito, "--out", str(ruta)], cwd=self.repo, capture_output=True, text=True,
                           env={k: v for k, v in os.environ.items() if not k.startswith("CAUCE_")})
        assert r.returncode == 0, r.stderr + r.stdout
        tid = r.stdout.split("Credencial ")[1].split(" ")[0]
        return tid, ruta

    def revocar(self, tid: str) -> None:
        st, _ = self.llamar("DELETE", f"/tokens/{tid}")
        assert st == 200, st

    def red(self, estado: str, conexion: str = "sim-a") -> None:
        st, _ = self.llamar("POST", f"/sim/connections/{conexion}/state", {"state": estado})
        assert st == 200, st

    def sesion(self, sid: str) -> dict:
        return self.llamar("GET", f"/sessions/{sid}")[1] or {}

    def resumen(self) -> dict:
        return self.llamar("GET", "/status/sessions")[1] or {}

    def resolver(self, sid: str, esperado: int = 200, nota: Optional[str] = None) -> dict:
        st, v = self.llamar("POST", f"/sessions/{sid}/resolve", {"note": nota} if nota else {})
        assert st == esperado, (st, v)
        return v


def esperar(cond, limite: float = 30, paso: float = 0.3):
    t0 = time.time()
    ultimo = None
    while time.time() - t0 < limite:
        ultimo = cond()
        if ultimo:
            return ultimo
        time.sleep(paso)
    return ultimo
