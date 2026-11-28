"""Interfaz de revisión: aplicación web LOCAL (FastAPI) para una sola persona.

Escucha solo en 127.0.0.1. No tiene autenticación porque no debe exponerse a
la red; ver docs/LIMITACIONES.md.
"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..audit import AuditLog
from ..config_store import ProfileStore
from ..data_adapter import DataError, columns_of, load_data
from ..engine import fill_form
from ..envio import _publica, enviar_protegido, verificar_operacion
from ..html_analyzer import analyze_html
from ..matcher import apply_manual, match_fields
from ..models import AnalysisResult, Mapping
from ..operaciones import OperacionBloqueada, RegistroOperaciones
from ..red.montaje import crear_diagnostico
from ..page_inspector import BrowserSession, BrowserWorker
from ..settings import Settings, get_settings
from ..url_policy import UrlNotAllowed, UrlPolicy

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).with_name("static")
LAB_DIR = ROOT / "lab_forms"
MAX_HTML_BYTES = 2_000_000
MAX_DATA_BYTES = 10_000_000


class AnalyzeIn(BaseModel):
    mode: str                       # html | url | lab
    html: str = ""
    url: str = ""
    lab: str = ""
    authorized: bool = False        # la persona declara tener autorización sobre la URL
    engine: str = "browser"         # browser | static (solo para HTML)


class FillIn(BaseModel):
    mapping: dict[str, Optional[str]]
    row_index: int = 0
    delay_ms: Optional[int] = None
    reload_first: bool = True
    form_key: Optional[str] = None


class SubmitIn(BaseModel):
    phrase: str


class ProfileIn(BaseModel):
    name: str
    mapping: dict[str, Optional[str]]


class Workspace:
    """Estado de la sesión de trabajo (una persona, un navegador)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.audit = AuditLog(settings.audit_path)
        self.policy = UrlPolicy.load(settings.allowed_hosts_file)
        self.profiles = ProfileStore(settings.profiles_dir)
        self.worker = BrowserWorker()
        self.session: Optional[BrowserSession] = None
        self.analysis: Optional[AnalysisResult] = None
        self.rows: list[dict[str, str]] = []
        self.proposals: list[Mapping] = []
        self.fila_rellenada: Optional[int] = None   # fila del último relleno (para la huella del expediente)
        self.lock = threading.Lock()
        # Bloque C: registro durable de operaciones confirmadas (antes del clic, en disco)
        self.registro = RegistroOperaciones(settings.data_dir, dueno=True)
        for ev in self.registro.eventos:
            self.audit.log(ev["event"], estado=ev.get("estado"), codigo=ev.get("codigo"))
        # Bloque B: diagnóstico de red con Network Engine (opcional)
        self.diag, self.diag_info = crear_diagnostico(
            settings, hay_inciertas=self.registro.pide_revision, al_ver_marca=self.registro.marca_vista_en,
            al_resolver=self._resuelta_en_ne, al_cambiar=self._diag_cambio)
        if self.registro.pide_revision():
            self.diag.exigir_marca()                 # tras un reinicio, el motor debe volver a saberlo
        self.diag.iniciar()

    def _resuelta_en_ne(self, sid: str) -> None:
        ids = self.registro.resuelta_por_operador_ne(sid)
        self.audit.log("ne_marca_resuelta_por_operador", count=len(ids))

    def _diag_cambio(self, e: dict) -> None:
        self.audit.log("ne_estado", estado=e["motor"], status=e.get("fase"), ok=not e["requiere_persona"],
                       codigo=e.get("motivo") or e.get("codigo"))

    def browser(self) -> BrowserSession:
        if self.session is None:
            self.session = BrowserSession(self.settings)
            self.session.on_cierre_inesperado = lambda: self.diag.navegador(False, "browser_closed")
            self.session.on_abierta = lambda: self.diag.navegador(True)
            self.session.on_submit_humano = self._envio_humano
            self.diag.iniciar()
        return self.session

    def _envio_humano(self) -> None:
        """La persona pulsó el botón del formulario en la ventana: queda en el registro durable como «incierta».

        Sin esto, un envío directo no dejaba rastro y la misma fila podía enviarse otra vez con ENVIAR."""
        i = self.fila_rellenada
        if i is None or not 0 <= i < len(self.rows):
            self.audit.log("envio_humano_sin_fila")          # no hay fila rellenada que atribuir
            return
        try:
            op = self.registro.registrar_envio_humano(self.registro.huella(self.rows[i]),
                                                      self.session.source_label if self.session else "")
            self.audit.log("envio_humano_directo", detail=op["id"])
        except (OSError, ValueError):
            self.audit.log("envio_humano_no_registrado", codigo="registro_no_escribible")
        self.fila_rellenada = None                            # la aprobación de esa fila ya no vale
        if self.session is not None:
            self.session.submit_armed = False
        self.diag.exigir_marca()                              # el motor y Monitor también deben verlo

    def close(self, cerrar_sesion_ne: bool = True) -> None:
        if self.session is not None:
            self.worker.run(self.session.close)
            self.session = None
        if cerrar_sesion_ne:
            # Cerrar la sesión NO borra revisiones: el motor guarda la marca y el registro local también.
            self.diag.detener(cerrar=True)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    ws = Workspace(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # Al apagar el servidor: se cierra el navegador; la sesión de Network Engine se deja (el motor la verá sin
        # latidos y, si Forms vuelve, seguirá con el mismo id). El registro de operaciones ya está en disco.
        ws.close(cerrar_sesion_ne=False)
        ws.diag.detener(cerrar=False)
        ws.worker.shutdown()
        ws.registro.soltar()

    app = FastAPI(title="Cauce Universal Form Connector", version="0.2.0rc1", lifespan=lifespan)
    app.state.ws = ws

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        """Defensa contra CSRF y DNS rebinding: la API solo acepta peticiones cuyo
        Host sea local y, si traen Origin, que sea esta misma aplicación. Así una
        web cualquiera abierta en el navegador no puede ordenar rellenar o enviar."""
        host = (request.headers.get("host") or "").split(":")[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1"):
            return JSONResponse({"detail": "Host no permitido"}, status_code=403)
        # Los navegadores actuales envían Sec-Fetch-Site: una petición desde otra web es "cross-site"
        if request.headers.get("sec-fetch-site") in ("cross-site", "same-site") and request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Petición desde otro sitio rechazada"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and request.url.path.startswith("/api/"):
            o = urlsplit(origin)
            if o.hostname not in ("127.0.0.1", "localhost", "::1") or o.port != request.url.port:
                return JSONResponse({"detail": "Origen no permitido"}, status_code=403)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    if LAB_DIR.is_dir():  # los laboratorios solo existen al ejecutar desde el repositorio
        app.mount("/lab", StaticFiles(directory=LAB_DIR, html=True), name="lab")

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/diagnostico")
    def diagnostico() -> dict[str, Any]:
        e = ws.diag.estado()
        abiertas = ws.registro.abiertas()
        e.update({"configurado": ws.diag_info["configurado"], "proteccion_credencial": ws.diag_info["proteccion_credencial"],
                  "operaciones_abiertas": len(abiertas),
                  # la operación viva NO es un resultado desconocido (antes la alerta la contaba como tal)
                  "operaciones_inciertas": sum(1 for o in abiertas if o["estado"] == "incierta"),
                  "operacion_viva": any(o["estado"] == "en_curso" for o in abiertas),
                  "revision_general": ws.registro.general,
                  "registro": ws.registro.estado_carga,
                  "envio_bloqueado": ws.registro.general is not None})
        return e

    @app.get("/api/operaciones")
    def operaciones(limit: int = 20) -> dict[str, Any]:
        return {"operaciones": [_publica(o) for o in ws.registro.lista(max(1, min(limit, 200)))],
                "revision_general": ws.registro.general}

    @app.post("/api/operaciones/{oid}/verificar")
    def verificar(oid: str) -> dict[str, Any]:
        """La persona pide comprobar el acuse en el portal propio. Nunca reenvía; «no consta» no desbloquea."""
        try:
            r = verificar_operacion(ws.registro, oid)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        ws.audit.log("operation_verified", detail=oid, estado=r["operacion"]["estado"], codigo=r.get("motivo"))
        return r

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {
            "loaded": ws.analysis is not None, "rows": len(ws.rows),
            "headless": settings.headless, "typing_delay_ms": settings.typing_delay_ms,
            "allowed_hosts": sorted(ws.policy.allowed_hosts),
        }

    @app.get("/api/lab")
    def lab_list() -> list[str]:
        return sorted(p.name for p in LAB_DIR.glob("*.html")) if LAB_DIR.is_dir() else []

    # ------------------------------------------------------------- análisis
    @app.post("/api/analyze")
    def analyze(body: AnalyzeIn, request: Request) -> dict[str, Any]:
        with ws.lock:
            ws.audit.new_run()
            if body.mode == "html":
                if not body.html.strip():
                    raise HTTPException(400, "Pegue o cargue un HTML.")
                if len(body.html.encode()) > MAX_HTML_BYTES:
                    raise HTTPException(413, "HTML demasiado grande (máx. 2 MB).")
                if body.engine == "static":
                    ws.analysis = analyze_html(body.html)
                else:
                    s = ws.browser()
                    ws.analysis = ws.worker.run(lambda: (s.open_html(body.html), s.inspect())[1])
            elif body.mode in ("url", "lab"):
                if body.mode == "lab":
                    if body.lab not in lab_list():
                        raise HTTPException(404, "Formulario de laboratorio inexistente.")
                    url = str(request.base_url).rstrip("/") + "/lab/" + body.lab
                    guard = UrlPolicy(allow_file=False).guard(False)   # el laboratorio es local
                else:
                    try:
                        url = ws.policy.check(body.url, body.authorized)
                        guard = ws.policy.guard(body.authorized)
                    except UrlNotAllowed as exc:
                        ws.audit.log("url_rejected", detail=str(exc))
                        raise HTTPException(403, str(exc))
                s = ws.browser()
                try:
                    ws.analysis = ws.worker.run(lambda: (s.open_url(url, guard), s.inspect())[1])
                except Exception as exc:
                    raise HTTPException(502, f"No se pudo abrir la página: {str(exc).splitlines()[0][:200]}")
            else:
                raise HTTPException(400, "Modo no válido.")
            a = ws.analysis
            ws.audit.log("analyze", source=a.source, source_label=a.source_label, engine=a.engine,
                         forms=len(a.forms), fields=len(a.all_fields), blocked=a.safety.blocked,
                         warnings=a.warnings)
            ws.proposals = match_fields(a.all_fields, columns_of(ws.rows)) if ws.rows else []
            return {"analysis": a.to_dict(), "proposals": [m.to_dict() for m in ws.proposals]}

    # ------------------------------------------------------------- datos
    @app.post("/api/data")
    async def upload_data(file: UploadFile = File(...)) -> dict[str, Any]:
        content = await file.read()
        if len(content) > MAX_DATA_BYTES:
            raise HTTPException(413, "Archivo demasiado grande (máx. 10 MB).")
        try:
            rows = load_data(file.filename or "datos.json", content)
        except (DataError, ValueError, KeyError) as exc:
            raise HTTPException(400, f"No se pudo leer el archivo: {exc}")
        ws.rows = rows
        cols = columns_of(rows)
        ws.audit.log("data_loaded", format=Path(file.filename or "").suffix, rows=len(rows), columns=len(cols))
        if ws.analysis:
            ws.proposals = match_fields(ws.analysis.all_fields, cols)
        return {"rows": len(rows), "columns": cols, "preview": rows[:5],
                "proposals": [m.to_dict() for m in ws.proposals]}

    @app.post("/api/mapping/propose")
    def propose() -> dict[str, Any]:
        if not ws.analysis or not ws.rows:
            raise HTTPException(400, "Analice un formulario y cargue datos primero.")
        ws.proposals = match_fields(ws.analysis.all_fields, columns_of(ws.rows))
        for m in ws.proposals:
            ws.audit.log("mapping_proposed", field_key=m.field_key, column=m.column,
                         confidence=m.confidence, mapping_status=m.status)
        return {"proposals": [m.to_dict() for m in ws.proposals]}

    # ------------------------------------------------------------- relleno
    @app.post("/api/fill")
    def fill(body: FillIn) -> dict[str, Any]:
        if ws.session is None or not ws.session.loaded:
            raise HTTPException(400, "El relleno necesita el análisis con navegador (HTML con navegador, URL o laboratorio).")
        if not ws.rows:
            raise HTTPException(400, "Cargue datos primero.")
        if not 0 <= body.row_index < len(ws.rows):
            raise HTTPException(400, "Fila fuera de rango.")
        row = ws.rows[body.row_index]
        unknown = {c for c in body.mapping.values() if c and c not in row}
        if unknown:
            raise HTTPException(400, f"Columnas inexistentes: {sorted(unknown)}")
        final = apply_manual(ws.proposals, {k: v for k, v in body.mapping.items()})
        for m in final:
            if m.status in ("manual", "ignorado"):
                ws.audit.log("mapping_override", field_key=m.field_key, column=m.column, mapping_status=m.status)
        s = ws.session

        def job():
            if body.reload_first:
                s.reload_source()
            return fill_form(s, dict(body.mapping), row, delay_ms=body.delay_ms, audit=ws.audit,
                             form_key=body.form_key)

        with ws.lock:
            report = ws.worker.run(job)
            ws.fila_rellenada = body.row_index
        return {"report": report.to_dict()}

    @app.post("/api/submit")
    def submit(body: SubmitIn) -> dict[str, Any]:
        if ws.session is None or not ws.session.loaded:
            raise HTTPException(400, "No hay página cargada.")
        # El candado hace que un doble clic espere y luego encuentre la aprobación ya consumida (403).
        if not ws.lock.acquire(timeout=0.5):
            raise HTTPException(409, "Ya hay un envío en marcha: espere su resultado. No se envía otra vez.")
        try:
            if ws.fila_rellenada is None or not 0 <= ws.fila_rellenada < len(ws.rows):
                raise HTTPException(403, "No hay un relleno revisado pendiente de envío (aprobación de un solo uso).")
            huella = ws.registro.huella(ws.rows[ws.fila_rellenada])
            return ws.worker.run(enviar_protegido, ws.session, body.phrase, ws.registro, huella, ws.audit, ws.diag)
        except OperacionBloqueada as exc:
            raise HTTPException(409, str(exc))
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        finally:
            ws.lock.release()

    @app.get("/api/screenshot")
    def screenshot() -> FileResponse:
        if ws.session is None or not ws.session.loaded:
            raise HTTPException(400, "No hay página cargada.")
        # Se devuelve en memoria: la captura contiene datos personales y no se guarda en disco
        png = ws.worker.run(lambda: ws.session.page.screenshot(full_page=True))
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------- perfiles
    @app.get("/api/profiles")
    def profiles() -> list[dict[str, Any]]:
        return ws.profiles.list()

    @app.post("/api/profiles")
    def save_profile(body: ProfileIn) -> dict[str, Any]:
        if not ws.analysis:
            raise HTTPException(400, "Analice un formulario primero.")
        try:
            path = ws.profiles.save(body.name, ws.analysis, body.mapping)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        ws.audit.log("profile_saved", profile=body.name, fields=len(ws.analysis.all_fields))
        return {"saved": path.name}

    @app.get("/api/profiles/{name}")
    def load_profile(name: str) -> dict[str, Any]:
        try:
            prof = ws.profiles.load(name)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, f"Perfil no encontrado o no válido: {exc}")
        out: dict[str, Any] = {"profile": {k: prof[k] for k in ("name", "source_label", "saved_at")}}
        if ws.analysis:
            out.update(ProfileStore.reconcile(prof, ws.analysis))
        else:
            out["mapping"] = prof["mapping"]
        ws.audit.log("profile_loaded", profile=name)
        return out

    @app.get("/api/audit")
    def audit(limit: int = 100) -> list[dict[str, Any]]:
        return ws.audit.events[-limit:]

    @app.post("/api/close")
    def close() -> dict[str, bool]:
        with ws.lock:
            ws.close()
            ws.analysis = None
            ws.rows = []          # los datos personales solo viven en memoria: se descartan
            ws.proposals = []
            ws.fila_rellenada = None
        return {"closed": True}

    return app


def main() -> None:
    import argparse
    import webbrowser

    import uvicorn

    ap = argparse.ArgumentParser(description="Interfaz local del conector de formularios")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="no abrir el navegador automáticamente")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Conector de formularios en {url}  (Ctrl+C para salir)")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
