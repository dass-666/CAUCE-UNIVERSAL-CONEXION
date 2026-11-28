"""Inspector de páginas dinámicas (Playwright).

`BrowserSession` mantiene un navegador y una página abiertos. Toda llamada a
Playwright ocurre en UN hilo dedicado (`BrowserWorker`), porque la API síncrona
de Playwright no es segura entre hilos. Así la interfaz web (FastAPI) puede
llamar desde cualquier hilo sin romper nada, también en Windows.
"""
from __future__ import annotations

import concurrent.futures as cf
import queue
import re
import threading
import time
from urllib.parse import urlsplit
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
    """Ejecuta funciones en un único hilo propietario de Playwright.

    ``en_reposo``: función que el hilo ejecuta cada ``REPOSO_S`` mientras no tiene trabajo. La API síncrona de Playwright
    solo atiende los eventos del navegador (peticiones interceptadas, avisos de ``submit``) DURANTE una llamada. Sin
    esto, cuando la persona envía desde la ventana con la interfaz en reposo, su petición se queda parada en el
    interceptor hasta la siguiente llamada (medido: 0 recibidas en 4 s). Si esa llamada es el clic de ENVIAR, salían
    las dos peticiones (posible duplicado en un portal sin protección)."""

    REPOSO_S = 0.1

    def __init__(self, en_reposo: Optional[Callable[[], None]] = None) -> None:
        self.en_reposo = en_reposo
        self._cola: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._hilo = threading.Thread(target=self._bucle, name="playwright", daemon=True)
        self._hilo.start()

    def _bucle(self) -> None:
        while True:
            try:
                item = self._cola.get(timeout=self.REPOSO_S)
            except queue.Empty:
                if self.en_reposo is not None:
                    try:
                        self.en_reposo()
                    except Exception:  # noqa: BLE001
                        pass
                continue
            if item is None:
                return
            fut, fn, args, kwargs = item
            if fut.set_running_or_notify_cancel():
                try:
                    fut.set_result(fn(*args, **kwargs))
                except BaseException as exc:  # noqa: BLE001
                    fut.set_exception(exc)

    def run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if threading.current_thread() is self._hilo:
            return fn(*args, **kwargs)
        fut: cf.Future = cf.Future()
        self._cola.put((fut, fn, args, kwargs))
        return fut.result()

    def shutdown(self) -> None:
        self.en_reposo = None
        self._cola.put(None)
        self._hilo.join(timeout=30)


def _mismo_sitio(a: str, b: Optional[str]) -> bool:
    try:
        x, y = urlsplit(a), urlsplit(b or "")
        return bool(x.hostname) and (x.scheme, x.hostname, x.port) == (y.scheme, y.hostname, y.port)
    except ValueError:
        return False


ESCRITURAS = ("POST", "PUT", "PATCH", "DELETE")


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
        # Integración con Network Engine: avisar si el navegador o la página se cierran SIN que Forms lo pida
        # (la persona cerró la ventana, Chromium se cayó). Se llama desde el hilo de Playwright; debe ser rápido.
        self.on_cierre_inesperado: Optional[Callable[[], None]] = None
        self.on_abierta: Optional[Callable[[], None]] = None
        self._cierre_propio = False
        # Revisión final: un `submit` con gesto de la persona fuera de `enviar_protegido` se avisa aquí (ver web/app.py).
        # Devuelve False si el envío NO pudo quedar registrado: entonces una petición de escritura se corta.
        self.on_submit_humano: Optional[Callable[[str], Optional[bool]]] = None
        self.envio_programado = False      # True solo durante el clic de `enviar_protegido`
        self.aprobacion_en_uso = False     # `enviar_protegido` consumió la aprobación y aún no ha pulsado
        self.envios_humanos = 0            # avisos de envío humano atendidos (submit con gesto o escritura de la página)
        self.escrituras_vistas: list[tuple[str, float]] = []   # (método, instante) de escrituras al sitio del formulario

    def _avisar_cierre(self) -> None:
        if self._cierre_propio or self.on_cierre_inesperado is None:
            return
        try:
            self.on_cierre_inesperado()
        except Exception:
            pass

    def _avisar_apertura(self) -> None:
        if self.on_abierta is not None:
            try:
                self.on_abierta()
            except Exception:
                pass

    def _envio_humano_visto(self, via: str) -> bool:
        """Envío hecho por la PERSONA en la ventana (no por `enviar_protegido`): debe quedar en el registro durable.
        Devuelve False solo si había algo que registrar y no se pudo."""
        if self.envio_programado or self.on_submit_humano is None:
            return True
        try:
            r = self.on_submit_humano(via)       # True: registrado · None: nada que registrar · False: no se pudo
        except Exception:  # noqa: BLE001
            r = False
        if r is True:
            self.envios_humanos += 1
        return r is not False

    def _submit_visto(self, gesto: Any) -> None:
        self.submit_events.append((bool(gesto), time.monotonic()))
        if gesto:
            self._envio_humano_visto("submit")

    def bombear(self) -> None:
        """Atiende eventos pendientes del navegador (ver BrowserWorker.en_reposo). Barato: 1 ms."""
        if self.page is not None and not self.page.is_closed():
            self.page.wait_for_timeout(1)

    def observe_submits(self) -> None:
        """Empieza a registrar eventos `submit` de la página actual (idempotente por página)."""
        if self.page is None or self._observed_page is self.page:
            return
        self.page.expose_function("__cauceSubmitSeen", self._submit_visto)
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
        self._browser.on("disconnected", lambda _b: self._avisar_cierre())
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
            self._cierre_propio = True
            try:
                self._context.close()
            finally:
                self._cierre_propio = False
        self.blocked_requests = 0
        self.submit_armed, self.submit_events, self._observed_page = False, [], None
        self.escrituras_vistas = []
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
            if req.method.upper() in ESCRITURAS and not self.envio_programado and _mismo_sitio(req.url, self._url):
                # Petición que puede modificar datos en el sitio del formulario y que Forms no originó: la persona
                # envió (botón, Intro o JavaScript de la página, aunque no haya evento `submit`). Se registra ANTES de
                # dejarla salir; si no se puede registrar, se corta (conservador).
                self.escrituras_vistas.append((req.method.upper(), time.monotonic()))
                if not self._envio_humano_visto("escritura"):
                    self.blocked_requests += 1
                    route.abort()
                    return
            route.continue_()

        self._context.route("**/*", on_route)
        if offline:
            self._context.route_web_socket(re.compile(".*"), lambda ws: ws.close())
        self.page = self._context.new_page()
        main = self.page
        main.on("close", lambda _p: self._avisar_cierre())
        self._context.on("page", lambda p: p.close() if p is not main else None)

    def add_init_script(self, js: str) -> None:
        """Script que se inyecta antes de cargar cada página (se usa en pruebas para
        instrumentar los eventos de teclado y demostrar la escritura secuencial)."""
        self._init_scripts.append(js)
        if self._context is not None:
            self._context.add_init_script(js)

    def close(self) -> None:
        self._cierre_propio = True
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        if self._pw is not None:
            self._pw.stop()
        self._pw = self._browser = self._context = self.page = None
        self._cierre_propio = False

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
        self._avisar_apertura()

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
        self._avisar_apertura()

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
                elif f.key in conocidas and f.tag == "input" and f.type == "custom-select" and not f.options:
                    # Combo que la página ya marca como lista pero cuyas opciones solo se leyeron al aprobar el perfil
                    # (p. ej. del almacén de datos del componente): se usan las aprobadas, no lo que flote en pantalla.
                    f.options = list(conocidas[f.key])
        if len(self.page.frames) > 1:
            result.warnings.append(
                f"La página contiene {len(self.page.frames) - 1} iframe(s); los campos dentro de iframes "
                "no se analizan en esta versión.")
        return result

    def screenshot(self, path: str | Path) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)
        return str(path)
