"""Ayudas para las pruebas del modo `cauce.conector.v1` (no son pruebas)."""
from __future__ import annotations

import functools
import http.server
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cauce_form_connector import contrato_v1 as C
from cauce_form_connector.config_store import ProfileStore
from cauce_form_connector.instrumentation import KEYLOG_JS
from cauce_form_connector.page_inspector import BrowserSession
from cauce_form_connector.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab_forms"

# Datos FICTICIOS; el prefijo «zz» permite buscarlos en cualquier salida
VALORES = {"nombre": "Zzmariana", "apellidos": "Zzejemplo Prueba", "email": "zz.mariana@example.com",
           "telefono": "5587654321", "pais": "MX", "acepta": "si"}
CAMPOS = {"nombre": "registro::nombre", "apellidos": "registro::apellidos", "email": "registro::email",
          "telefono": "registro::telefono", "pais": "registro::pais", "acepta": "registro::acepta_terminos"}


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def settings_para(tmp: Path, **kw) -> Settings:
    s = Settings()
    s.headless = True
    s.profiles_dir = str(tmp / "profiles")
    s.audit_path = str(tmp / "audit.jsonl")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def aprobar(settings: Settings, url: str, nombre: str = "perfil_lab", *, campos=None, formulario_id="frm_lab",
            html_session=None) -> dict:
    """Hace lo que haría una persona con `aprobar-perfil`: abre el formulario real y guarda el perfil aprobado."""
    with BrowserSession(settings, headless=True) as s:
        s.open_url(url)
        analysis = s.inspect()
    path, digest = ProfileStore(settings.profiles_dir).save_integration(
        nombre, analysis, campos or CAMPOS, formulario_id=formulario_id, url=url, aprobado_por_ref="rev_lab")
    from cauce_form_connector.signature import structure_signature_v2
    return {"perfil": nombre, "sha": digest, "firma": structure_signature_v2(analysis.all_fields),
            "formulario_id": formulario_id, "url": url, "ruta": path}


def solicitud(info: dict, *, valores=None, espera=0, ahora=None, **over) -> dict:
    ahora = ahora or datetime.now(timezone.utc)
    sol = {
        "contrato": C.VERSION, "tipo": "preparar_formulario", "solicitud_id": "sol_" + secrets.token_hex(8),
        "emitida_en": iso(ahora - timedelta(seconds=5)), "caduca_en": iso(ahora + timedelta(seconds=600)),
        "autorizacion": {"ref": "aut_lab", "formulario_id": info["formulario_id"], "otorgada_por_ref": "rev_lab",
                         "vigente_hasta": (ahora + timedelta(days=2)).date().isoformat(), "consentimiento_vigente": True},
        "formulario": {"formulario_id": info["formulario_id"], "url": info["url"], "perfil": info["perfil"],
                       "perfil_sha256": info["sha"], "firma_estructura_esperada": info["firma"]},
        "campos": dict(VALORES if valores is None else valores),
        "opciones": {"espera_revision_max_s": espera, "pausa_tecla_ms": 0},
    }
    for k, v in over.items():
        sol[k] = v
    return sol


class Espia(BrowserSession):
    """Sesión que, antes de cerrarse, guarda qué se tecleó y qué valores quedaron en la página."""
    ultimo: "Espia | None" = None

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.add_init_script(KEYLOG_JS)
        self.teclas = {}
        self.valores = {}
        Espia.ultimo = self

    def close(self):
        try:
            self.teclas = self.page.evaluate("window.__keylog || {}")
            self.valores = self.page.evaluate(
                "Object.fromEntries([...document.querySelectorAll('input,textarea,select')].map(e => [e.name||e.id, e.value]))")
        except Exception:
            pass
        super().close()


def fabrica_espia(s: Settings) -> Espia:
    return Espia(s, headless=True)


def fabrica_prohibida(_s):
    raise AssertionError("no debía abrirse el navegador")


class Sitio:
    """Servidor HTTP local de laboratorio con contenido y redirecciones editables."""

    def __init__(self):
        self.paginas: dict[str, str] = {}
        self.redirecciones: dict[str, str] = {}
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                ruta = self.path.split("?")[0]
                if ruta in outer.redirecciones:
                    self.send_response(302)
                    self.send_header("Location", outer.redirecciones[ruta])
                    self.end_headers()
                    return
                cuerpo = outer.paginas.get(ruta)
                if cuerpo is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = cuerpo.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def cerrar(self):
        self.srv.shutdown()
        self.srv.server_close()
