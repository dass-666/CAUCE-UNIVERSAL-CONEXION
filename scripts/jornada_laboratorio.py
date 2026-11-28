"""Jornadas SIMULADAS de laboratorio: Forms (proceso real) + Network Engine RC3 (proceso real, red simulada) + portal
FICTICIO propio. Solo 127.0.0.1 y datos ficticios.

Qué es nativo de Forms y qué es del laboratorio (para no confundirlos en el informe):
- NATIVO DE FORMS: interfaz y API local, relleno carácter por carácter, envío protegido, registro durable de
  operaciones, diagnóstico de red (latidos), verificación de acuse, bloqueo de repeticiones.
- DEL LABORATORIO (simulado): el portal y sus fallos, la red de Network Engine (modo ``sim``), y la PERSONA: este guion
  escribe «ENVIAR» en su lugar. Esa confirmación es SIMULADA; en uso real la da una persona.

Uso:
  python scripts/jornada_laboratorio.py --ne-repo RUTA_RC3 --expedientes 6 10 20 50 --salida evidence/out/jornadas
  python scripts/jornada_laboratorio.py --ne-repo RUTA_RC3 --duracion-min 60 --salida evidence/out/prolongada
  (sin --ne-repo: jornada sin Network Engine, Forms debe decir «sin diagnóstico»)
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cauce_form_connector.red.cliente_ne import read_secret  # noqa: E402

try:
    import psutil  # solo para medir; no es dependencia de Forms
except ImportError:  # pragma: no cover
    psutil = None

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(metodo: str, url: str, cuerpo: Any = None, token: Optional[str] = None, timeout: float = 120,
         archivo: Optional[tuple[str, bytes]] = None) -> tuple[int, Any, float]:
    t0 = time.monotonic()
    if archivo:
        limite = "----cauce" + os.urandom(6).hex()
        data = (f"--{limite}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{archivo[0]}\"\r\n"
                f"Content-Type: application/json\r\n\r\n").encode() + archivo[1] + f"\r\n--{limite}--\r\n".encode()
        req = urllib.request.Request(url, data=data, method=metodo)
        req.add_header("Content-Type", f"multipart/form-data; boundary={limite}")
    else:
        req = urllib.request.Request(url, method=metodo, data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null"), time.monotonic() - t0
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null"), time.monotonic() - t0
        except ValueError:
            return e.code, None, time.monotonic() - t0
    except OSError as e:
        return 0, {"error": type(e).__name__}, time.monotonic() - t0


def esperar_puerto(port: int, limite: float = 60) -> bool:
    t0 = time.time()
    while time.time() - t0 < limite:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


class Proc:
    def __init__(self, nombre: str, args: list[str], cwd: Path, env: dict, log: Path, port: int):
        self.nombre, self.args, self.cwd, self.env, self.log, self.port = nombre, args, cwd, env, log, port
        self.p: Optional[subprocess.Popen] = None

    def iniciar(self) -> None:
        self.p = subprocess.Popen(self.args, cwd=str(self.cwd), env=self.env, stdout=open(self.log, "a"),
                                  stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        if not esperar_puerto(self.port):
            raise RuntimeError(f"{self.nombre} no arrancó: {self.log.read_text()[-600:]}")

    def matar(self) -> None:
        if self.p and self.p.poll() is None:
            try:
                os.killpg(self.p.pid, signal.SIGKILL)
            except OSError:
                self.p.kill()
            self.p.wait(10)

    def parar(self) -> None:
        if self.p and self.p.poll() is None:
            try:
                os.killpg(self.p.pid, signal.SIGTERM)
            except OSError:
                self.p.terminate()
            try:
                self.p.wait(15)
            except subprocess.TimeoutExpired:
                self.matar()

    def arbol(self) -> list:
        if not (psutil and self.p and self.p.poll() is None):
            return []
        try:
            pr = psutil.Process(self.p.pid)
            return [pr] + pr.children(recursive=True)
        except psutil.Error:
            return []


class Medidor(threading.Thread):
    """Muestrea RAM (RSS), CPU y nº de procesos de cada componente cada ``paso`` segundos."""

    def __init__(self, procs: dict[str, Proc], paso: float = 5.0):
        super().__init__(daemon=True)
        self.procs, self.paso = procs, paso
        self.muestras: list[dict] = []
        self.parar = threading.Event()
        self._cpu_prev: dict[int, Any] = {}

    def run(self) -> None:
        while not self.parar.is_set():
            m: dict[str, Any] = {"t": time.time()}
            for nombre, pr in self.procs.items():
                arbol = pr.arbol()
                rss = cpu = pss = 0.0
                chromium_rss = 0.0
                n_chromium = 0
                for x in arbol:
                    try:
                        r = x.memory_info().rss
                        c = x.cpu_percent(None)
                        try:
                            pss += getattr(x.memory_full_info(), "pss", r)   # PSS: sin contar dos veces lo compartido
                        except psutil.Error:
                            pss += r
                        rss += r
                        cpu += c
                        if "chrom" in x.name().lower() or "headless_shell" in x.name().lower():
                            chromium_rss += r
                            n_chromium += 1
                    except psutil.Error:
                        pass
                m[nombre] = {"rss_mb": round(rss / 2**20, 1), "pss_mb": round(pss / 2**20, 1), "cpu": round(cpu, 1),
                             "procesos": len(arbol),
                             "chromium_rss_mb": round(chromium_rss / 2**20, 1), "chromium_procesos": n_chromium}
            self.muestras.append(m)
            self.parar.wait(self.paso)


class Laboratorio:
    def __init__(self, salida: Path, ne_repo: Optional[str], python: str = sys.executable):
        self.salida = salida
        self.salida.mkdir(parents=True, exist_ok=True)
        self.ne_repo = ne_repo
        self.py = python
        self.p_forms, self.p_portal, self.p_ne = libre(), libre(), libre()
        self.dir_ne = salida / "ne"
        self.dir_forms = salida / "forms"
        self.procs: dict[str, Proc] = {}
        self.eventos: list[dict] = []
        self.tiempos: dict[str, list[float]] = {"analizar": [], "rellenar": [], "enviar": [], "diagnostico": []}
        env_base = {k: v for k, v in os.environ.items() if not k.startswith("CAUCE_")}
        self.portal = Proc("portal", [python, str(ROOT / "lab_portal" / "servidor.py"), "--port", str(self.p_portal),
                                      "--capacidad", "100000", "--por-minuto", "1000"], ROOT, env_base,
                           salida / "portal.log", self.p_portal)
        self.procs["portal"] = self.portal
        self.ne = None
        self.token_file = None
        if ne_repo:
            self.dir_ne.mkdir(parents=True, exist_ok=True)
            cfg = self.dir_ne / "config.json"
            cfg.write_text(json.dumps({"data_dir": str(self.dir_ne / "datos"), "api": {"port": self.p_ne},
                                       "lab": {"rotation_enabled": False}, "sessions": {"max": 8}}), "utf-8")
            self.cfg_ne = cfg
            self.ne = Proc("network_engine", [python, "-m", "cauce", "--config", str(cfg), "sim", "--port",
                                              str(self.p_ne)], Path(ne_repo), env_base, salida / "ne.log", self.p_ne)
            self.procs["network_engine"] = self.ne
        env_f = dict(env_base, CAUCE_HEADLESS="1", CAUCE_TYPING_DELAY_MS=os.environ.get("JORNADA_DELAY_MS", "5"),
                     CAUCE_FORMS_DATA_DIR=str(self.dir_forms / "state"), CAUCE_AUDIT_PATH=str(self.dir_forms / "audit.jsonl"),
                     CAUCE_PROFILES_DIR=str(self.dir_forms / "profiles"), CAUCE_NE_PORT=str(self.p_ne),
                     CAUCE_NE_CONNECTION_ID="sim-a", CAUCE_NE_LATIDO_S="5", CAUCE_FORMS_PUESTO="forms-lab-jornada")
        if os.environ.get("CAUCE_BROWSER_PATH"):
            env_f["CAUCE_BROWSER_PATH"] = os.environ["CAUCE_BROWSER_PATH"]
        if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            env_f["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
        self.env_forms = env_f
        self.forms = Proc("forms", [python, "-m", "cauce_form_connector.web.app", "--port", str(self.p_forms),
                                    "--no-open"], ROOT, env_f, salida / "forms.log", self.p_forms)
        self.procs["forms"] = self.forms

    # ------------------------------------------------------------------ utilidades
    def ev(self, tipo: str, **kw: Any) -> None:
        e = {"t": ahora(), "evento": tipo, **kw}
        self.eventos.append(e)
        print(json.dumps(e, ensure_ascii=False), flush=True)

    @property
    def fu(self) -> str:
        return f"http://127.0.0.1:{self.p_forms}"

    @property
    def pu(self) -> str:
        return f"http://127.0.0.1:{self.p_portal}"

    def admin(self) -> str:
        return read_secret(str(self.dir_ne / "datos" / "api.token"))

    def ne_api(self, metodo: str, ruta: str, cuerpo: Any = None):
        return http(metodo, f"http://127.0.0.1:{self.p_ne}/api/v1{ruta}", cuerpo, token=self.admin(), timeout=10)

    def portal_api(self, metodo: str, ruta: str, cuerpo: Any = None):
        return http(metodo, self.pu + ruta, cuerpo, timeout=10)[1]

    # ------------------------------------------------------------------ arranque
    def iniciar(self) -> None:
        self.portal.iniciar()
        if self.ne:
            self.ne.iniciar()
            t0 = time.time()
            while not (self.dir_ne / "datos" / "api.token").exists() and time.time() - t0 < 30:
                time.sleep(0.2)
            self.token_file = self.dir_ne / "forms-lab-jornada.token"
            if not self.token_file.exists():
                r = subprocess.run([self.py, "-m", "cauce", "--config", str(self.cfg_ne), "token", "create", "--name",
                                    "forms-lab-jornada", "--scope", "session", "--out", str(self.token_file)],
                                   cwd=self.ne_repo, capture_output=True, text=True)
                assert r.returncode == 0, r.stderr
            self.env_forms["CAUCE_NE_SESSION_TOKEN_FILE"] = str(self.token_file)
        self.forms.iniciar()
        self.ev("arranque", forms=self.p_forms, portal=self.p_portal, network_engine=self.p_ne if self.ne else None)

    def detener(self) -> None:
        for p in (self.forms, self.ne, self.portal):
            if p:
                p.parar()

    def reiniciar_forms(self, matar: bool = True) -> None:
        (self.forms.matar if matar else self.forms.parar)()
        self.forms.iniciar()

    # ------------------------------------------------------------------ pasos de la persona (simulada)
    def cargar_datos(self, filas: list[dict]) -> None:
        st, r, _ = http("POST", self.fu + "/api/data", archivo=("expedientes.json", json.dumps(filas).encode()))
        assert st == 200, (st, r)

    def analizar(self) -> list[dict]:
        st, r, dt = http("POST", self.fu + "/api/analyze", {"mode": "url", "url": self.pu + "/tramite"})
        assert st == 200, (st, r)
        self.tiempos["analizar"].append(dt)
        return [f for fm in r["analysis"]["forms"] for f in fm["fields"]]

    def rellenar(self, campos: list[dict], fila: int) -> dict:
        mapping = {f["key"]: f["name"] for f in campos if f["type"] != "hidden"}
        st, r, dt = http("POST", self.fu + "/api/fill", {"mapping": mapping, "row_index": fila, "reload_first": True})
        assert st == 200, (st, r)
        self.tiempos["rellenar"].append(dt)
        return r["report"]

    def enviar(self) -> tuple[int, Any]:
        st, r, dt = http("POST", self.fu + "/api/submit", {"phrase": "ENVIAR"})      # confirmación SIMULADA
        self.tiempos["enviar"].append(dt)
        return st, r

    def diag(self) -> dict:
        st, r, dt = http("GET", self.fu + "/api/diagnostico", timeout=10)
        self.tiempos["diagnostico"].append(dt)
        return r if st == 200 else {"error": st}

    def operaciones(self) -> list[dict]:
        return http("GET", self.fu + "/api/operaciones?limit=200")[1]["operaciones"]

    def verificar(self, oid: str) -> dict:
        return http("POST", self.fu + f"/api/operaciones/{oid}/verificar")[1]


def filas_ficticias(desde: int, n: int) -> list[dict]:
    return [{"expediente": f"EXP-{i:05d}", "nombre": f"Persona Ficticia {i}", "correo": f"ficticia{i}@example.test",
             "telefono": f"55 2000 {i % 10000:04d}", "tipo": ("alta", "cambio", "baja")[i % 3]}
            for i in range(desde, desde + n)]


def plan_fallos(n: int) -> dict[int, str]:
    """Fallos deliberados repartidos por la jornada (deterministas). Con 6 expedientes, uno de cada tipo principal."""
    tipos = ["cortar_despues", "cortar_antes", "ne_apagado", "red_caida", "doble_clic", "portal_lento",
             "forms_reiniciado", "capacidad", "navegador_cerrado"]
    if n <= 6:
        return {1: "cortar_despues", 2: "cortar_antes", 3: "ne_apagado", 4: "doble_clic", 5: "forms_reiniciado"}
    paso = max(2, n // len(tipos))
    return {1 + k * paso: t for k, t in enumerate(tipos) if 1 + k * paso < n}


def jornada(lab: Laboratorio, n: int, desde: int, etiqueta: str) -> dict:
    filas = filas_ficticias(desde, n)
    lab.cargar_datos(filas)
    fallos = plan_fallos(n)
    stats0 = lab.portal_api("GET", "/_lab/estadisticas")
    resultados = []
    bloqueos_correctos = 0
    reintento_tras_capacidad = 0
    for i in range(n):
        fallo = fallos.get(i) if lab.ne or fallos.get(i) not in ("ne_apagado", "red_caida") else None
        campos = lab.analizar()
        rep = lab.rellenar(campos, i)
        if not rep["ok"]:
            resultados.append({"i": i, "estado": "relleno_con_incidencias", "fallo": fallo})
            continue
        r: Any = None
        st = 0
        pp0 = lab.portal_api("GET", "/_lab/estadisticas")["peticiones_post"]
        if fallo in ("cortar_despues", "cortar_antes"):
            lab.portal_api("POST", "/_lab/fallo", {"tipo": fallo, "veces": 6})   # también los reintentos de Chromium
        if fallo == "portal_lento":
            lab.portal_api("POST", "/_lab/fallo", {"tipo": "lento", "segundos": 25})
        if fallo == "ne_apagado":
            lab.ne.parar()
        if fallo == "red_caida":
            lab.ne_api("POST", "/sim/connections/sim-a/state", {"state": "down"})
            time.sleep(8)
        if fallo == "capacidad":
            lab.portal_api("POST", "/_lab/limites", {"capacidad": lab.portal_api("GET", "/_lab/estadisticas")["recibidos"]})
        if fallo == "doble_clic":
            lab.portal_api("POST", "/_lab/fallo", {"tipo": "lento", "segundos": 2})
            res: list = []
            hs = [threading.Thread(target=lambda: res.append(lab.enviar())) for _ in range(2)]
            [h.start() for h in hs]
            [h.join() for h in hs]
            ok = [x for x in res if x[0] == 200]
            assert len(ok) == 1, res
            st, r = ok[0]
            bloqueos_correctos += 1
        elif fallo == "forms_reiniciado":
            lab.portal_api("POST", "/_lab/fallo", {"tipo": "lento", "segundos": 6})
            pp = lab.portal_api("GET", "/_lab/estadisticas")["peticiones_post"]
            hilo = threading.Thread(target=lambda: lab.enviar(), daemon=True)
            hilo.start()
            t0 = time.time()
            while lab.portal_api("GET", "/_lab/estadisticas")["peticiones_post"] == pp and time.time() - t0 < 15:
                time.sleep(0.1)
            time.sleep(0.5)
            lab.reiniciar_forms(matar=True)                    # Forms muere con el envío en el aire
            lab.cargar_datos(filas)
            ops = lab.operaciones()
            st, r = 200, {"operacion": ops[0] if ops else None}
        elif fallo == "navegador_cerrado":
            lab.portal_api("POST", "/_lab/fallo", {"tipo": "lento", "segundos": 6})
            hilo = threading.Thread(target=lambda: lab.enviar(), daemon=True)
            hilo.start()
            time.sleep(1.5)
            # Chromium del proceso Forms muere (no el proceso Forms)
            for x in lab.forms.arbol()[1:]:
                try:
                    if "chrom" in x.name().lower() or "headless_shell" in x.name().lower():
                        x.kill()
                except Exception:  # noqa: BLE001
                    pass
            hilo.join(60)
            ops = lab.operaciones()
            st, r = 200, {"operacion": ops[0] if ops else None}
            http("POST", lab.fu + "/api/close", {})            # la persona termina y vuelve a abrir
            lab.cargar_datos(filas)
        else:
            st, r = lab.enviar()
        op = (r or {}).get("operacion") if isinstance(r, dict) else None
        estado = op["estado"] if op else f"http_{st}"
        lab.portal_api("POST", "/_lab/limpiar_fallos", {})       # ningún fallo pasa al expediente siguiente
        fila = {"i": i, "expediente": filas[i]["expediente"], "fallo": fallo, "estado": estado,
                "motivo": op.get("motivo") if op else (r or {}).get("detail"), "op": op["id"] if op else None,
                "post_vistos_por_el_portal": lab.portal_api("GET", "/_lab/estadisticas")["peticiones_post"] - pp0}
        # repetir lo incierto debe estar bloqueado
        if estado == "incierta":
            campos = lab.analizar()
            lab.rellenar(campos, i)
            st2, r2 = lab.enviar()
            fila["repetir"] = st2
            if st2 == 409:
                bloqueos_correctos += 1
        if estado == "rechazada" and fallo == "capacidad":
            lab.portal_api("POST", "/_lab/limites", {"capacidad": 100000})
            campos = lab.analizar()
            lab.rellenar(campos, i)
            st3, r3 = lab.enviar()                             # NUEVA confirmación (simulada) tras el rechazo
            reintento_tras_capacidad += 1
            fila["tras_capacidad"] = (r3 or {}).get("operacion", {}).get("estado") if st3 == 200 else st3
        if fallo == "ne_apagado":
            fila["diag_durante"] = lab.diag().get("motor")
            lab.ne.iniciar()
        if fallo == "red_caida":
            fila["diag_durante"] = lab.diag().get("conectividad")
            lab.ne_api("POST", "/sim/connections/sim-a/state", {"state": "up"})
        resultados.append(fila)
    # La persona verifica en el portal propio las inciertas (acción explícita; nunca reenvía)
    time.sleep(27)
    verificadas = []
    for op in lab.operaciones():
        if op["estado"] == "incierta" and op.get("verificable"):
            v = lab.verificar(op["id"])
            verificadas.append({"op": op["id"], "tras": v["operacion"]["estado"], "motivo": v.get("motivo")})
    stats1 = lab.portal_api("GET", "/_lab/estadisticas")
    ops = lab.operaciones()
    conf = [o for o in ops if o["estado"] == "confirmada"]
    d = lab.diag()
    out = {"jornada": etiqueta, "expedientes": n, "fallos": fallos, "resultados": resultados, "verificadas": verificadas,
           "portal": {"recibidos": stats1["recibidos"] - stats0["recibidos"], "duplicados": stats1["duplicados"],
                      "peticiones_post": stats1["peticiones_post"] - stats0["peticiones_post"],
                      "reintentos_mismo_token": stats1["reintentos_mismo_token"] - stats0["reintentos_mismo_token"],
                      "rechazos": stats1["rechazos"]},
           "forms": {"confirmadas": len(conf), "abiertas": d.get("operaciones_abiertas"),
                     "estados": {s: sum(1 for o in ops if o["estado"] == s) for s in {o["estado"] for o in ops}}},
           "bloqueos_correctos": bloqueos_correctos, "reintento_tras_capacidad": reintento_tras_capacidad,
           "diagnostico_final": {k: d.get(k) for k in ("motor", "conectividad", "fase", "requiere_persona", "motivo",
                                                        "avisos_no_entregados", "operaciones_abiertas")}}
    out["sin_duplicados"] = stats1["duplicados"] == 0
    return out


def resumen_tiempos(t: dict[str, list[float]]) -> dict:
    out = {}
    for k, v in t.items():
        if v:
            s = sorted(v)
            out[k] = {"n": len(v), "media_s": round(statistics.mean(v), 3), "p95_s": round(s[int(0.95 * (len(s) - 1))], 3),
                      "max_s": round(max(v), 3)}
    return out


def resumen_muestras(m: list[dict]) -> dict:
    out = {}
    if not m:
        return out
    for comp in [k for k in m[0] if k != "t"]:
        rss = [x[comp]["rss_mb"] for x in m if comp in x]
        pss = [x[comp].get("pss_mb", 0) for x in m if comp in x]
        cpu = [x[comp]["cpu"] for x in m if comp in x]
        pr = [x[comp]["procesos"] for x in m if comp in x]
        cr = [x[comp]["chromium_rss_mb"] for x in m if comp in x]
        tercio = max(1, len(rss) // 3)
        out[comp] = {"pss_mb_media": round(statistics.mean(pss), 1), "pss_mb_max": max(pss),
                     "pss_mb_primer_tercio": round(statistics.mean(pss[:tercio]), 1),
                     "pss_mb_ultimo_tercio": round(statistics.mean(pss[-tercio:]), 1),
                     "rss_mb_media": round(statistics.mean(rss), 1), "rss_mb_max": max(rss),
                     "rss_mb_primer_tercio": round(statistics.mean(rss[:tercio]), 1),
                     "rss_mb_ultimo_tercio": round(statistics.mean(rss[-tercio:]), 1),
                     "cpu_media_pct": round(statistics.mean(cpu), 1), "cpu_max_pct": max(cpu),
                     "procesos_max": max(pr), "chromium_rss_mb_max": max(cr) if cr else 0}
    return out


def tamanos(lab: Laboratorio) -> dict:
    archivos = {"forms_audit": lab.dir_forms / "audit.jsonl", "forms_operaciones": lab.dir_forms / "state" / "operaciones.json",
                "ne_audit": lab.dir_ne / "datos" / "audit.jsonl", "ne_sesiones": lab.dir_ne / "datos" / "sessions_state.json"}
    return {k: (p.stat().st_size if p.exists() else None) for k, p in archivos.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ne-repo", default=os.environ.get("CAUCE_NE_REPO"))
    ap.add_argument("--expedientes", type=int, nargs="*", default=[6, 10, 20, 50])
    ap.add_argument("--duracion-min", type=float, default=0, help="prueba prolongada: repetir jornadas este tiempo")
    ap.add_argument("--salida", default=str(ROOT / "evidence" / "out" / "jornadas"))
    a = ap.parse_args(argv)
    salida = Path(a.salida)
    lab = Laboratorio(salida, a.ne_repo)
    med = Medidor(lab.procs, paso=5.0 if not a.duracion_min else 15.0)
    informe: dict[str, Any] = {"inicio": ahora(), "network_engine": bool(a.ne_repo), "jornadas": [],
                               "nota": "Simulación de laboratorio: portal ficticio, red simulada de Network Engine y "
                                       "confirmación humana SIMULADA por el guion. No es validación física."}
    if a.ne_repo:
        informe["network_engine_commit"] = subprocess.run(["git", "-C", a.ne_repo, "rev-parse", "HEAD"],
                                                          capture_output=True, text=True).stdout.strip()
    informe["forms_commit"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                                             text=True).stdout.strip()
    try:
        lab.iniciar()
        med.start()
        t_ini = time.time()
        informe["tamanos_inicio"] = tamanos(lab)
        desde = 1
        if a.duracion_min:
            k = 0
            while time.time() - t_ini < a.duracion_min * 60:
                k += 1
                r = jornada(lab, 10, desde, f"prolongada-{k}")
                desde += 10
                informe["jornadas"].append(r)
                (salida / "jornadas.json").write_text(json.dumps(informe, ensure_ascii=False, indent=1), "utf-8")
        else:
            for n in a.expedientes:
                r = jornada(lab, n, desde, f"{n}-expedientes")
                desde += n
                informe["jornadas"].append(r)
                (salida / "jornadas.json").write_text(json.dumps(informe, ensure_ascii=False, indent=1), "utf-8")
        informe["duracion_s"] = round(time.time() - t_ini, 1)
        informe["tamanos_fin"] = tamanos(lab)
    finally:
        med.parar.set()
        informe["tiempos"] = resumen_tiempos(lab.tiempos)
        informe["recursos"] = resumen_muestras(med.muestras)
        informe["fin"] = ahora()
        lab.detener()
        (salida / "jornadas.json").write_text(json.dumps(informe, ensure_ascii=False, indent=1), "utf-8")
        (salida / "muestras.json").write_text(json.dumps(med.muestras), "utf-8")
    ok = all(j["sin_duplicados"] for j in informe["jornadas"])
    print(json.dumps({"ok": ok, "jornadas": len(informe["jornadas"]), "salida": str(salida)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
