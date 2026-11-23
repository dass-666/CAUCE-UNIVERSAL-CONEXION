"""Ventana de Cauce Forms para Windows (doble clic en `abrir_cauce_forms.bat`).

No añade lógica nueva: cada botón ejecuta el comando ya existente del conector (`analizar-formulario`,
`autorrellenar`) en un proceso aparte, así que se mantienen sus comprobaciones técnicas (lista local de dominios
permitidos para sitios externos, parada ante CAPTCHA o inicio de sesión, perfil revisado y aprobado con huella,
tablas de equivalencias, verificación de cada campo) y el conector NUNCA envía el formulario.
Los formularios locales (localhost, 127.0.0.1, archivos .html) se usan sin ningún paso previo.

Resultados, perfiles, auditoría y lista local de dominios se guardan por usuario en
%LOCALAPPDATA%\\CauceForms (fuera del repositorio), con permisos solo para el usuario actual.
Este archivo no contiene URL de destino ni datos personales.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

RAIZ = Path(__file__).resolve().parents[1]
EXPEDIENTE_SINTETICO = RAIZ / "sample_data" / "expediente_monitor_sintetico.json"
LOCALES = {"localhost", "127.0.0.1"}
ES_WINDOWS = os.name == "nt"
REVISOR = "rev_usuario_local"   # quien usa la ventana revisa el perfil en la vista previa antes de autorrellenar


def paquete_python_store() -> str:
    """Nombre de familia del paquete si este Python es el de Microsoft Store (p. ej.
    «PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0»); "" en otro caso."""
    for ruta in (sys.base_prefix, getattr(sys, "_base_executable", ""), sys.executable):
        for parte in PureWindowsPath(ruta or "").parts:   # rutas de Windows, también al probar en Linux
            if parte.startswith("PythonSoftwareFoundation.") and "_" in parte:
                trozos = parte.split("_")          # Nombre_Versión_Arq__Editor o Nombre_Editor
                return f"{trozos[0]}_{trozos[-1]}"
    return ""


def localappdata_real() -> str:
    """%LOCALAPPDATA% tal como lo ven otros programas. El Python de Microsoft Store redirige las escrituras en
    %LOCALAPPDATA% a %LOCALAPPDATA%\\Packages\\<paquete>\\LocalCache\\Local, así que Chromium o el Explorador no
    encontrarían la ruta «normal». Si existe la carpeta redirigida, se usa esa (los datos siguen donde están)."""
    base = os.environ.get("LOCALAPPDATA", "")
    paquete = paquete_python_store() if (base and ES_WINDOWS) else ""
    if paquete:
        real = Path(base) / "Packages" / paquete / "LocalCache" / "Local"
        if (real / "CauceForms").exists() or not (Path(base) / "CauceForms").exists():
            if real.parent.exists():
                return str(real)
    return base


def carpeta_datos() -> Path:
    base = os.environ.get("CAUCE_DATOS") or localappdata_real() or str(Path.home() / ".local" / "share")
    d = Path(base) / "CauceForms"
    nueva = not d.exists()
    for sub in ("analisis", "perfiles", "logs", "config", "estado", "fuentes", "formularios"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    if nueva and os.name == "nt":   # acceso solo para el usuario actual
        usuario = os.environ.get("USERNAME", "")
        if usuario:
            subprocess.run(["icacls", str(d), "/inheritance:r", "/grant:r", f"{usuario}:(OI)(CI)F"],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    hosts = d / "config" / "allowed_hosts.json"
    if not hosts.exists():
        hosts.write_text(json.dumps({"_comentario": "Dominios con autorización por escrito (lista local de este usuario).",
                                     "allowed_hosts": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def entorno(datos: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "CAUCE_ALLOWED_HOSTS_FILE": str(datos / "config" / "allowed_hosts.json"),
        "CAUCE_PROFILES_DIR": str(datos / "perfiles"),
        "CAUCE_AUDIT_PATH": str(datos / "logs" / "audit.jsonl"),
        "CAUCE_STATE_DIR": str(datos / "estado"),
        "CAUCE_FUENTES_DIR": str(datos / "fuentes"),      # fuentes de identificadores configuradas por la persona
        "PYTHONIOENCODING": "utf-8",
    })
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(RAIZ / ".pw-browsers"))
    return env


def host_de(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def limpiar_url(url: str) -> str:
    return (url or "").split("#")[0].split("?")[0].rstrip("/")


def hosts_permitidos(datos: Path) -> list[str]:
    try:
        return json.loads((datos / "config" / "allowed_hosts.json").read_text(encoding="utf-8")).get("allowed_hosts", [])
    except (OSError, ValueError):
        return []


def agregar_host(datos: Path, host: str) -> None:
    p = datos / "config" / "allowed_hosts.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    if host not in data.setdefault("allowed_hosts", []):
        data["allowed_hosts"].append(host)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def args_analizar(url: str, autorizado: bool, expediente: str, salida: Path, previo: str = "") -> list[str]:
    a = [sys.executable, "-m", "cauce_form_connector", "analizar-formulario", "--url", url,
         "--expediente", expediente, "--salida", str(salida)]
    if previo:
        a += ["--perfil-previo", previo]
    return a + (["--authorized"] if autorizado else [])


def perfil_previo_de(url: str, carpetas: list[tuple[Path, str]]) -> str:
    """El perfil revisado más reciente de esta URL (guardado al aprobar, o descargado), para no repetir la configuración."""
    u, mejor = limpiar_url(url), None
    for carpeta, patron in carpetas:
        try:
            archivos = list(carpeta.glob(patron))
        except OSError:
            continue
        for p in archivos:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict) and limpiar_url(d.get("url", "")) == u and d.get("campos_expediente"):
                    if mejor is None or p.stat().st_mtime > mejor.stat().st_mtime:
                        mejor = p
            except (OSError, ValueError, AttributeError):
                continue
    return str(mejor) if mejor else ""


def args_autorrellenar(perfil: str, expediente: str, url: str, revisor: str, autorizado: bool,
                       autorizacion: str) -> list[str]:
    a = [sys.executable, "-m", "cauce_form_connector", "autorrellenar", "--formulario", perfil,
         "--expediente", expediente, "--espera-s", "300"]
    if url:
        a += ["--url", url]
    if revisor:
        a += ["--aprobar-perfil", revisor]
    if autorizado:
        a.append("--authorized")
    if autorizacion:
        a += ["--autorizacion", autorizacion]
    return a


SALIDAS = {0: "Terminado.", 2: "Hubo campos por revisar (vea [REVISAR] arriba). Nada se envió.", 3: "URL no autorizada: no se abrió la página.", 4: "La página pide CAPTCHA: no se continúa.",
           6: "Configuración o expediente no válido (vea el detalle arriba).",
           7: "Ventana cerrada sin revisión dentro del plazo. Nada se envió."}


class App:
    def __init__(self, tk, ttk, filedialog, messagebox, scrolledtext, simpledialog=None):
        self.tk, self.filedialog, self.messagebox, self.simpledialog = tk, filedialog, messagebox, simpledialog
        self.datos = carpeta_datos()
        self.ultimo_analisis: Path | None = None
        self.cola: queue.Queue = queue.Queue()
        self.ocupado = False

        r = self.root = tk.Tk()
        r.title("Cauce Forms")
        r.geometry("780x600")
        f = ttk.Frame(r, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        self.url = tk.StringVar()
        self.exp = tk.StringVar(value=str(EXPEDIENTE_SINTETICO))

        ttk.Label(f, text="URL del formulario").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.url).grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Label(f, text="Expediente (JSON)").grid(row=4, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.exp).grid(row=4, column=1, sticky="ew", pady=3)
        ttk.Button(f, text="Elegir…", command=self.elegir_expediente).grid(row=4, column=2, padx=(6, 0))

        b = ttk.Frame(f)
        b.grid(row=5, column=0, columnspan=3, pady=10, sticky="w")
        self.botones = [ttk.Button(b, text="Analizar formulario", command=self.analizar),
                        ttk.Button(b, text="Revisar perfil", command=self.revisar),
                        ttk.Button(b, text="Preparar autorrellenado", command=self.preparar),
                        ttk.Button(b, text="Abrir carpeta de resultados", command=self.abrir_carpeta)]
        for i, x in enumerate(self.botones):
            x.grid(row=0, column=i, padx=(0, 8))

        self.log = scrolledtext.ScrolledText(f, height=20, wrap="word")
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(6, weight=1)
        self.escribir("Cauce Forms. El conector rellena y verifica, pero NUNCA envía el formulario: "
                      "el envío lo hace usted.\nResultados en: " + str(self.datos) + "\n")
        r.after(150, self.vaciar_cola)

    # ------------------------------------------------------------------ utilidades
    def escribir(self, texto: str) -> None:
        self.log.insert("end", texto if texto.endswith("\n") else texto + "\n")
        self.log.see("end")

    def vaciar_cola(self) -> None:
        try:
            while True:
                tipo, valor = self.cola.get_nowait()
                if tipo == "linea":
                    self.escribir(valor)
                else:
                    self.terminar(*valor)
        except queue.Empty:
            pass
        self.root.after(150, self.vaciar_cola)

    def ejecutar(self, args: list[str], al_terminar=None, entrada: str | None = None) -> None:
        if self.ocupado:
            self.messagebox.showinfo("Cauce Forms", "Espere a que termine la operación en curso.")
            return
        self.ocupado = True
        for x in self.botones[:3]:
            x.state(["disabled"])

        self.salida: list[str] = []

        def hilo():
            try:
                p = subprocess.Popen(args, cwd=str(RAIZ), env=entorno(self.datos), stdout=subprocess.PIPE,
                                     stdin=subprocess.PIPE if entrada is not None else subprocess.DEVNULL,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if entrada is not None:        # captura manual: solo por stdin, nunca en argumentos ni archivos
                    p.stdin.write(entrada)
                    p.stdin.close()
                for linea in p.stdout:
                    self.cola.put(("linea", linea.rstrip("\n")))
                    self.salida.append(linea)
                rc = p.wait()
            except OSError as e:
                self.cola.put(("linea", f"No se pudo ejecutar el conector: {e}"))
                rc = -1
            self.cola.put(("fin", (rc, al_terminar)))

        threading.Thread(target=hilo, daemon=True).start()

    def terminar(self, rc: int, al_terminar) -> None:
        self.ocupado = False
        for x in self.botones[:3]:
            x.state(["!disabled"])
        self.escribir(SALIDAS.get(rc, f"Terminó con código {rc}.") + "\n")
        if al_terminar:
            al_terminar(rc)

    def comprobar_url(self, obligatoria: bool) -> str | None:
        url = self.url.get().strip()
        if not url:
            if obligatoria:
                self.messagebox.showwarning("Cauce Forms", "Escriba la URL del formulario.")
            return None if obligatoria else ""
        if urlsplit(url).scheme.lower() == "file":     # formularios .html locales de laboratorio
            return url
        host = host_de(url)
        if not host:
            self.messagebox.showwarning("Cauce Forms", "La URL no es válida.")
            return None
        if host not in LOCALES:
            permitidos = hosts_permitidos(self.datos)
            if not any(host == h or host.endswith("." + h) for h in permitidos):
                if not self.messagebox.askyesno("Cauce Forms", f"«{host}» es un sitio externo que aún no está en su "
                                                "lista de dominios permitidos.\n\n¿Añadirlo? (solo se pregunta la "
                                                "primera vez)"):
                    return None
                agregar_host(self.datos, host)
                self.escribir(f"Dominio añadido a la lista local: {host}")
        return url

    # ------------------------------------------------------------------ botones
    def elegir_expediente(self) -> None:
        p = self.filedialog.askopenfilename(title="Expediente JSON", filetypes=[("JSON", "*.json")])
        if p:
            self.exp.set(p)

    def analizar(self) -> None:
        url = self.comprobar_url(obligatoria=True)
        if not url:
            return
        salida = self.datos / "analisis" / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.escribir(f"\nAnalizando (solo lectura)… resultados en {salida}")

        def fin(rc):
            if (salida / "vista_previa.html").exists():
                self.ultimo_analisis = salida
                self.escribir("Pulse «Revisar perfil» para revisar y descargar el perfil.")
                if (salida / "diagnostico_controles.json").exists():
                    self.escribir("Si algún desplegable no se reconoció, envíe «diagnostico_controles.json» de esa carpeta: "
                                  "describe la estructura de los controles, sin datos personales.")

        previo = perfil_previo_de(url, [(self.datos / "formularios", "*.json"), (Path.home() / "Downloads", "perfil_*.json")])
        if previo:
            self.escribir(f"Se conservará la configuración ya revisada de esta URL ({Path(previo).name}).")
        self.ejecutar(args_analizar(url, host_de(url) not in LOCALES, self.exp.get(), salida, previo), fin)

    def revisar(self) -> None:
        d = self.ultimo_analisis
        if d is None:
            previos = sorted((self.datos / "analisis").glob("*/vista_previa.html"))
            d = previos[-1].parent if previos else None
        if d is None:
            self.messagebox.showinfo("Cauce Forms", "Primero pulse «Analizar formulario».")
            return
        self.escribir(f"Abriendo la vista previa: {d / 'vista_previa.html'}\nMarque los campos, complete las tablas y "
                      "descargue el perfil revisado (perfil_….json).")
        webbrowser.open((d / "vista_previa.html").as_uri())

    def preparar(self) -> None:
        url = self.comprobar_url(obligatoria=False)
        if url is None:
            return
        externo = bool(url) and host_de(url) not in LOCALES
        perfil, aprobar = self.elegir_perfil(url)
        if not perfil:
            return
        manuales = self.pedir_manuales(perfil)
        if manuales is None:
            return
        self.escribir(f"\nPreparando autorrellenado con {perfil}…\nSe abrirá el navegador con los campos aprobados "
                      "rellenados y verificados. El conector NO envía: revise y envíe usted, o cierre la ventana.")
        args = args_autorrellenar(perfil, self.exp.get(), url, REVISOR if aprobar else "", externo, "")
        if manuales:
            args.append("--manuales-stdin")

        def fin(rc):
            texto = "".join(self.salida)
            if aprobar and "Perfil aprobado:" in texto:          # se guarda para las siguientes ejecuciones
                destino = self.datos / "formularios" / Path(perfil).name
                if Path(perfil).resolve() != destino.resolve():
                    destino.write_bytes(Path(perfil).read_bytes())
                self.escribir(f"Perfil aprobado guardado: las próximas veces se usará sin volver a configurarlo.")
            if "ESTRUCTURA_CAMBIADA" in texto:
                self.escribir("El formulario cambió desde que se aprobó el perfil: pulse «Analizar formulario», "
                              "revise el perfil y descárguelo de nuevo.")

        self.ejecutar(args, fin, entrada=json.dumps(manuales) if manuales else None)

    def elegir_perfil(self, url: str) -> tuple[str, bool]:
        """(ruta, aprobar). Reutiliza el perfil aprobado guardado para esta URL; si en Descargas hay uno revisado más
        reciente para la misma URL, usa ese y lo aprueba. Si no hay ninguno, lo pregunta."""
        def url_de(p: Path) -> str:
            try:
                return limpiar_url(json.loads(p.read_text(encoding="utf-8")).get("url", ""))
            except (OSError, ValueError, AttributeError):
                return ""
        descargas = Path.home() / "Downloads"
        if url:
            u = limpiar_url(url)
            guardado = next((p for p in sorted((self.datos / "formularios").glob("*.json")) if url_de(p) == u), None)
            t0 = guardado.stat().st_mtime if guardado else 0
            nuevos = sorted((p for p in descargas.glob("perfil_*.json") if p.stat().st_mtime > t0 and url_de(p) == u),
                            key=lambda p: p.stat().st_mtime) if descargas.exists() else []
            if nuevos:
                self.escribir(f"Perfil revisado nuevo en Descargas: {nuevos[-1].name} (se aprobará y guardará).")
                return str(nuevos[-1]), True
            if guardado:
                self.escribir(f"Usando el perfil aprobado guardado para esta URL: {guardado.name}.")
                return str(guardado), False
        p = self.filedialog.askopenfilename(title="Perfil revisado (perfil_….json)",
                                            initialdir=str(descargas if descargas.exists() else Path.home()),
                                            filetypes=[("Perfil JSON", "perfil_*.json"), ("JSON", "*.json")])
        return (p, True) if p else ("", False)

    def pedir_manuales(self, perfil: str) -> dict[str, str] | None:
        """Datos que el expediente no tiene (p. ej. un identificador): la persona los escribe para ESTA ejecución.
        No se guardan ni se muestran en el registro. Devuelve None si la persona cancela."""
        try:
            cfg = json.loads(Path(perfil).read_text(encoding="utf-8"))
            campos = [l for l, r in cfg.get("campos_expediente", {}).items() if r == "@manual"]
        except (OSError, ValueError, AttributeError):
            return {}
        etiquetas = cfg.get("etiquetas") or {}
        out: dict[str, str] = {}
        for logico in campos:
            v = self.simpledialog.askstring("Cauce Forms", f"Escriba «{etiquetas.get(logico, logico)}».\n"
                                            "Se usa solo en este autorrellenado y no se guarda.\n"
                                            "Déjelo vacío para completarlo usted en el formulario.", parent=self.root)
            if v is None:
                return None
            if v.strip():
                out[logico] = v.strip()
        if campos:
            self.escribir(f"Captura manual: {len(out)} de {len(campos)} dato(s) escritos (no se muestran ni se guardan).")
        return out

    def abrir_carpeta(self) -> None:
        d = self.ultimo_analisis or self.datos
        if os.name == "nt":
            os.startfile(str(d))  # type: ignore[attr-defined]
        else:
            webbrowser.open(d.as_uri())


def main() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
    App(tk, ttk, filedialog, messagebox, scrolledtext, simpledialog).root.mainloop()


if __name__ == "__main__":
    main()
