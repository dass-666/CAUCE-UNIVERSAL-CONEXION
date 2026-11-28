"""Propuesta de sustituto del consumidor de referencia del paso 09b (Network Engine RC3) por Cauce Forms NATIVO.

Mismo contrato que el «hijo» de ``scripts/laboratorio_integrado/sesion_laboratorio.py`` de Network Engine f93dd58:
lee las mismas variables (``CAUCE_LAB_NE_PORT``, ``CAUCE_LAB_NE_ADMIN``, ``CAUCE_LAB_NE_SESSION``, ``CAUCE_LAB_FORMS_URL``,
``CAUCE_LAB_ETIQUETA``) y escribe en la última línea de la salida el mismo JSON (``fases``, ``tiempos``,
``formulario_enviado``, ``sigue_pidiendo_persona_tras_recuperar``, ``error``). Así el padre del paso 09b solo tendría que
lanzar este archivo en lugar de su hijo (ver «Solicitudes para el otro agente» en el informe). Este repositorio NO
modifica Network Engine.

Diferencia con el consumidor de referencia: aquí el dueño de la sesión es el código de Forms
(``DiagnosticoRed`` + ``Puesto`` + ``BrowserSession`` con sus avisos de cierre), no un bucle de latidos escrito a mano.
La credencial de administración del laboratorio se usa SOLO para lo que hace el operador del laboratorio: cambiar la
red simulada, observar la fase y resolver la marca. Forms no la recibe ni la usa.

  python scripts/paso_09b_nativo.py            (con las variables anteriores)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cauce_form_connector.page_inspector import BrowserSession  # noqa: E402
from cauce_form_connector.red.cliente_ne import CauceSessionClient  # noqa: E402
from cauce_form_connector.red.diagnostico import DiagnosticoRed  # noqa: E402
from cauce_form_connector.red.identidad import Puesto  # noqa: E402

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
FASES = ("iniciada", "activa", "perdida_conectividad", "recuperada", "requiere_intervencion", "incierta",
         "interrumpida", "cerrada")


def _http(port: int, ruta: str, token: str, metodo: str = "GET", cuerpo=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1{ruta}", method=metodo,
                                 data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with _OPENER.open(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None


def recorrido() -> dict:
    port = int(os.environ["CAUCE_LAB_NE_PORT"])
    etiqueta = os.environ.get("CAUCE_LAB_ETIQUETA", "lab-forms")
    adm, ses, url = os.environ["CAUCE_LAB_NE_ADMIN"], os.environ["CAUCE_LAB_NE_SESSION"], os.environ["CAUCE_LAB_FORMS_URL"]
    fases = {f: False for f in FASES}
    tiempos: dict[str, float] = {}
    out: dict = {"fases": fases, "tiempos": tiempos, "formulario_enviado": None, "consumidor": "cauce-forms-nativo"}
    datos = Path(tempfile.mkdtemp(prefix="forms-09b-"))
    puesto = Puesto(datos, etiqueta_fija=etiqueta)
    # latido_max_s=6 como el consumidor de referencia (heartbeat_timeout_s=6): «incierta» en segundos, no en minutos
    diag = DiagnosticoRed(CauceSessionClient(ses, port=port), puesto, conexion="sim-a", intervalo_s=0.8, latido_max_s=6)
    nav = BrowserSession(headless=True)
    envios: list[str] = []

    def fase_ne() -> str:
        sid = diag.c.session_id
        return ((_http(port, f"/sessions/{sid}", adm)[1] or {}).get("phase") if sid else None) or ""

    def esperar_fase(fase: str, limite: float) -> bool:
        t0 = time.time()
        while time.time() - t0 < limite:
            if fase_ne() == fase:
                fases[fase] = True
                tiempos[fase] = round(time.time() - t0, 1)
                return True
            time.sleep(0.5)
        return False

    def red(estado: str) -> None:
        _http(port, "/sim/connections/sim-a/state", adm, "POST", {"state": estado})

    try:
        assert diag.latir(obligatorio=True), diag.estado()
        fases["iniciada"] = diag.estado()["fase"] in ("iniciada", "activa")
        diag.iniciar()                                                     # hilo de latidos PROPIO de Forms
        nav.on_cierre_inesperado = lambda: diag.navegador(False, "browser_closed")
        nav.on_abierta = lambda: diag.navegador(True)
        nav.start()
        nav.page.on("request", lambda r: envios.append(r.method) if r.method == "POST" else None)
        nav.open_url(url)
        nav.page.on("request", lambda r: envios.append(r.method) if r.method == "POST" else None)
        esperar_fase("activa", 15)
        red("down")
        esperar_fase("perdida_conectividad", 40)
        red("up")
        esperar_fase("recuperada", 60)
        # operación a medias: Forms la anuncia (in_progress), se escribe en un campo y NO se envía; cae la red
        assert diag.operacion_inicio()
        campo = nav.page.query_selector("input[type=text], input:not([type])")
        if campo:
            campo.fill("Prueba ficticia")
        red("down")
        esperar_fase("requiere_intervencion", 40)
        red("up")
        diag.operacion_fin(resultado_seguro=True)                         # no hubo clic: nada que dudar en Forms
        time.sleep(6)
        out["sigue_pidiendo_persona_tras_recuperar"] = diag.estado()["requiere_persona"] is True and \
            (_http(port, f"/sessions/{diag.c.session_id}", adm)[1] or {}).get("requires_human") is True
        _http(port, f"/sessions/{diag.c.session_id}/resolve", adm, "POST", {})   # el OPERADOR del laboratorio
        diag.detener()                                                     # Forms deja de latir (colgado, suspendido…)
        esperar_fase("incierta", 20)
        diag.iniciar()                                                     # vuelve: misma sesión (puesto.json)
        esperar_fase("recuperada", 15)
        nav.page.close()                                                   # la persona cierra la ventana
        esperar_fase("interrumpida", 15)
        out["formulario_enviado"] = bool(envios)
        sid = diag.c.session_id
        diag.detener(cerrar=True)
        _, resumen = _http(port, "/status/sessions", adm)
        fases["cerrada"] = any(c.get("label") == etiqueta and c.get("phase") == "cerrada"
                               for c in (resumen or {}).get("closed") or [])
        out["sesion_reutilizada"] = sid is not None and puesto.sesion_ne is None
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:300]
        red("up")
        diag.detener(cerrar=True)
    finally:
        nav.close()
    return out


if __name__ == "__main__":
    print(json.dumps(recorrido(), ensure_ascii=False))
