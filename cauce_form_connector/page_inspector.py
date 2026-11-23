"""Inspector de páginas dinámicas (Playwright).

`BrowserSession` mantiene un navegador y una página abiertos. Toda llamada a
Playwright ocurre en UN hilo dedicado (`BrowserWorker`), porque la API síncrona
de Playwright no es segura entre hilos. Así la interfaz web (FastAPI) puede
llamar desde cualquier hilo sin romper nada, también en Windows.
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from .form_detector import build_analysis
from .html_analyzer import html_fingerprint
from .models import AnalysisResult
from .settings import Settings, get_settings
from .url_policy import safe_url_label

DETECTOR_JS = (Path(__file__).with_name("detector.js")).read_text(encoding="utf-8")

# Espera a que el DOM deje de mutar `quiet` ms seguidos (máximo `max` ms).
SETTLE_JS = """
([quiet, max]) => new Promise((resolve) => {
  let last = performance.now(), changes = 0;
  const obs = new MutationObserver(() => { last = performance.now(); changes++; });
  obs.observe(document.documentElement, {subtree: true, childList: true, attributes: true});
  const start = performance.now();
  const tick = () => {
    const now = performance.now();
    if (now - last >= quiet || now - start >= max) { obs.disconnect(); resolve(changes); }
    else setTimeout(tick, 50);
  };
  setTimeout(tick, 50);
})
"""

# Observa eventos `submit` (fase de captura). Se registra con `expose_function`, que sobrevive a navegaciones.
SUBMIT_OBSERVER_JS = """
(() => { if (window.__cauceObs) return; window.__cauceObs = true;
  document.addEventListener('submit', (e) => { try { window.__cauceSubmitSeen(!!e.isTrusted && !!(navigator.userActivation && navigator.userActivation.isActive)); } catch (_) {} }, true); })()
"""

T = TypeVar("T")


class BrowserWorker:
    """Ejecuta funciones en un único hilo propietario de Playwright."""

    def __init__(self) -> None:
        self._pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")

    def run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return self._pool.submit(fn, *args, **kwargs).result()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


class BrowserSession:
    """Navegador + página. Usar SIEMPRE desde el mismo hilo (ver BrowserWorker)."""

    def __init__(self, settings: Optional[Settings] = None, headless: Optional[bool] = None):
        self.settings = settings or get_settings()
        self.headless = self.settings.headless if headless is None else headless
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self.source: str = ""
        self.source_label: str = ""
        self._html: Optional[str] = None
        self._url: Optional[str] = None
        self._init_scripts: list[str] = []
        self._nav_guard: Optional[Callable[[str], bool]] = None
        self.blocked_requests = 0
        # clave -> opciones de los cuadros de texto que se comprobó (o se aprobó) que abren una lista
        self.listas_texto: dict[str, list] = {}
        self.solo_lectura_red = False       # True durante la fase activa del análisis
        self.escrituras_bloqueadas = 0
        self.analisis_alterado: list[str] = []
        # Solo informativo (analizador): HTML tal como lo sirvió el sitio, antes de ejecutar JavaScript
        self.initial_html: Optional[str] = None
        self.last_settle_changes = 0
        # --- H1: el envío no es una capacidad permanente de la sesión ---
        self.integration_mode = False      # True: no existe envío programático (modo `preparar`)
        self.submit_armed = False          # solo tras un relleno completado en ESTA página; un solo uso
        self.submit_events: list[tuple[bool, float]] = []  # (¿con gesto de usuario activo?, instante monotónico)
        self._observed_page = None

    def observe_submits(self) -> None:
        """Empieza a registrar eventos `submit` de la página actual (idempotente por página)."""
        if self.page is None or self._observed_page is self.page:
            return
        self.page.expose_function("__cauceSubmitSeen", lambda gesto: self.submit_events.append((bool(gesto), time.monotonic())))
        self.page.add_init_script(SUBMIT_OBSERVER_JS)
        try:
            self.page.evaluate(SUBMIT_OBSERVER_JS)
        except Exception:
            pass
        self._observed_page = self.page

    # ---------------------------------------------------------------- ciclo de vida
    def start(self) -> "BrowserSession":
        if self._browser is not None:
            return self
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        kwargs: dict[str, Any] = {"headless": self.headless}
        if self.settings.browser_path:
            kwargs["executable_path"] = self.settings.browser_path
        self._browser = self._pw.chromium.launch(**kwargs)
        self._new_context()
        return self

    def _new_context(self, offline: bool = False,
                     nav_guard: Optional[Callable[[str], bool]] = None) -> None:
        """Contexto nuevo y aislado (sin cookies ni almacenamiento previos).

        - `offline=True` (HTML pegado): se aborta TODA petición HTTP y WebSocket a nivel
          de contexto, de modo que tampoco escapan ventanas emergentes.
        - `nav_guard`: comprueba cada navegación del marco principal (incluidas
          redirecciones) contra la política de URLs.
        - Service workers bloqueados y ventanas emergentes cerradas en ambos casos.
        """
        if self._context is not None:
            self._context.close()
        self.blocked_requests = 0
        self.submit_armed, self.submit_events, self._observed_page = False, [], None
        # offline=True emula "sin conexión" en el propio Chromium: cubre lo que el enrutado de
        # Playwright no intercepta (p. ej. navigator.sendBeacon), además de abortar con route.
        self._context = self._browser.new_context(locale="es-ES", service_workers="block",
                                                  accept_downloads=False, offline=offline)
        for js in self._init_scripts:
            self._context.add_init_script(js)
        self._context.set_default_timeout(self.settings.nav_timeout_ms)

        def on_route(route: Any) -> None:
            req = route.request
            if offline:
                self.blocked_requests += 1
                route.abort()
                return
            if self.solo_lectura_red and req.method.upper() not in ("GET", "HEAD", "OPTIONS"):
                # análisis: mientras se interactúa con la página, nada que pueda modificar datos sale hacia el servidor
                self.escrituras_bloqueadas += 1
                route.abort()
                return
            if nav_guard is not None and req.is_navigation_request() and req.frame.parent_frame is None:
                if not nav_guard(req.url):
                    self.blocked_requests += 1
                    route.abort()
                    return
            route.continue_()

        self._context.route("**/*", on_route)
        if offline:
            self._context.route_web_socket(re.compile(".*"), lambda ws: ws.close())
        self.page = self._context.new_page()
        main = self.page
        self._context.on("page", lambda p: p.close() if p is not main else None)

    def add_init_script(self, js: str) -> None:
        """Script que se inyecta antes de cargar cada página (se usa en pruebas para
        instrumentar los eventos de teclado y demostrar la escritura secuencial)."""
        self._init_scripts.append(js)
        if self._context is not None:
            self._context.add_init_script(js)

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        if self._pw is not None:
            self._pw.stop()
        self._pw = self._browser = self._context = self.page = None

    def __enter__(self) -> "BrowserSession":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------------------------------------------------------------- carga
    def open_html(self, html: str) -> None:
        """Carga HTML pegado por el usuario. Se BLOQUEA toda petición de red: el
        HTML puede ejecutar sus scripts en línea, pero no descargar recursos ni
        comunicarse con servidores externos."""
        self.start()
        self._new_context(offline=True)
        self.page.set_content(html, wait_until="load")
        self.source, self.source_label = "html", html_fingerprint(html)
        self._html, self._url = html, None
        self.settle()

    def open_url(self, url: str, nav_guard: Optional[Callable[[str], bool]] = None) -> None:
        """Abre una URL. La autorización debe comprobarse ANTES con `UrlPolicy`;
        `nav_guard` impide además que la página redirija a destinos no autorizados."""
        self.start()
        self._nav_guard = nav_guard
        self._new_context(nav_guard=nav_guard)
        resp = self.page.goto(url, wait_until="domcontentloaded")
        self.initial_html = None
        try:
            self.initial_html = resp.text() if resp is not None else None
        except Exception:
            pass  # algunos esquemas (file://) o respuestas no exponen el cuerpo
        try:
            self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass  # páginas con conexiones persistentes nunca llegan a "networkidle"
        self.source, self.source_label = "url", safe_url_label(url)
        self._url, self._html = url, None
        self.last_settle_changes = self.settle()

    def reload_source(self) -> None:
        """Vuelve a cargar la fuente original (para empezar un relleno limpio)."""
        if self._html is not None:
            self.open_html(self._html)
        elif self._url is not None:
            self.open_url(self._url, self._nav_guard)
        else:
            raise RuntimeError("No hay ninguna página cargada.")

    @property
    def loaded(self) -> bool:
        return self.page is not None and (self._html is not None or self._url is not None)

    # ---------------------------------------------------------------- inspección
    def settle(self, quiet_ms: Optional[int] = None, max_ms: Optional[int] = None) -> int:
        """Espera a que el DOM se estabilice. Devuelve el nº de mutaciones observadas."""
        q = self.settings.settle_quiet_ms if quiet_ms is None else quiet_ms
        m = self.settings.settle_max_ms if max_ms is None else max_ms
        try:
            return int(self.page.evaluate(SETTLE_JS, [q, m]))
        except Exception:
            return 0

    def raw_scan(self) -> dict[str, Any]:
        return self.page.evaluate(DETECTOR_JS)

    def inspect(self) -> AnalysisResult:
        result = build_analysis(self.raw_scan(), self.source or "url", self.source_label, engine="browser")
        conocidas = getattr(self, "listas_texto", None)
        if conocidas:                 # cuadros de texto ya comprobados (o aprobados en el perfil) como listas
            from .listas import TIPOS_TEXTO, promover
            for f in result.all_fields:
                if f.key in conocidas and f.tag == "input" and f.type in TIPOS_TEXTO:
                    promover(f, conocidas[f.key])
        if len(self.page.frames) > 1:
            result.warnings.append(
                f"La página contiene {len(self.page.frames) - 1} iframe(s); los campos dentro de iframes "
                "no se analizan en esta versión.")
        return result

    def screenshot(self, path: str | Path) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)
        return str(path)
