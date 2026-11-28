"""Capturas de la interfaz de Forms con Chromium REAL en cada estado de diagnóstico (bloque E).

Usa los mismos procesos que las jornadas (Forms real, Network Engine RC3 en modo simulado, portal ficticio propio).
Las acciones de la persona se hacen por la API local de Forms; la página de Forms se abre en Chromium y se captura
tal como la vería la persona (se refresca sola cada 3 s).

  python scripts/capturas_rc3.py --ne-repo RUTA_RC3 --salida evidence/rc3/capturas
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from jornada_laboratorio import Laboratorio, filas_ficticias, http  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ne-repo", default=os.environ.get("CAUCE_NE_REPO"))
    ap.add_argument("--salida", default=str(ROOT / "evidence" / "out" / "capturas"))
    a = ap.parse_args(argv)
    out = Path(a.salida).resolve()
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    kw = {"headless": True}
    if os.environ.get("CAUCE_BROWSER_PATH"):
        kw["executable_path"] = os.environ["CAUCE_BROWSER_PATH"]
    hechas: list[dict] = []

    def capturar(page, nombre: str, espera: float = 4.0, desc: str = "") -> None:
        page.reload()
        page.wait_for_timeout(int(espera * 1000))
        ruta = out / f"{len(hechas) + 1:02d}_{nombre}.png"
        page.locator("#estado").screenshot(path=str(ruta))
        if nombre in ("resultado_desconocido", "confirmada"):
            page.locator("#paso-envio").screenshot(path=str(out / f"{len(hechas) + 1:02d}_{nombre}_operaciones.png"))
        hechas.append({"archivo": ruta.name, "estado": nombre, "descripcion": desc,
                       "indicadores": page.locator(".indicadores").inner_text().replace("\n", " · "),
                       "revision": page.locator("#revision").inner_text() if page.locator("#revision").is_visible() else ""})
        print(json.dumps(hechas[-1], ensure_ascii=False), flush=True)

    # 1) Sin Network Engine configurado
    lab0 = Laboratorio(out / "lab-sin-ne", None)
    lab0.iniciar()
    with sync_playwright() as p:
        nav = p.chromium.launch(**kw)
        page = nav.new_page(viewport={"width": 1100, "height": 900}, device_scale_factor=2)
        page.goto(lab0.fu + "/")
        capturar(page, "ne_no_configurado", desc="Forms sin credencial de Network Engine: funciona, sin diagnóstico")
        nav.close()
    lab0.detener()

    lab = Laboratorio(out / "lab", a.ne_repo)
    lab.iniciar()
    try:
        filas = filas_ficticias(9001, 6)
        lab.cargar_datos(filas)
        with sync_playwright() as p:
            nav = p.chromium.launch(**kw)
            page = nav.new_page(viewport={"width": 1100, "height": 900}, device_scale_factor=2)
            page.goto(lab.fu + "/")
            campos = lab.analizar()
            capturar(page, "ne_conectado_verificada", 9, "Network Engine conectado, conectividad verificada, sesión activa")
            # operación en curso (portal lento)
            lab.rellenar(campos, 0)
            lab.portal_api("POST", "/_lab/fallo", {"tipo": "lento", "segundos": 9})
            h = threading.Thread(target=lab.enviar, daemon=True)
            h.start()
            time.sleep(1.0)
            page.wait_for_timeout(3500)
            ruta = out / f"{len(hechas) + 1:02d}_operacion_en_curso.png"
            page.locator("#estado").screenshot(path=str(ruta))
            hechas.append({"archivo": ruta.name, "estado": "operacion_en_curso",
                           "indicadores": page.locator(".indicadores").inner_text().replace("\n", " · ")})
            h.join(60)
            lab.portal_api("POST", "/_lab/limpiar_fallos", {})
            capturar(page, "confirmada", 4, "Envío confirmado por el acuse verificado del portal propio")
            # resultado desconocido (corte antes de registrar, también reintentos del navegador)
            campos = lab.analizar()
            lab.rellenar(campos, 1)
            lab.portal_api("POST", "/_lab/fallo", {"tipo": "cortar_antes", "veces": 6})
            lab.enviar()
            lab.portal_api("POST", "/_lab/limpiar_fallos", {})
            capturar(page, "resultado_desconocido", 8, "Resultado desconocido: Forms no reenvía y pide revisión")
            # pérdida de conectividad (red simulada del motor)
            lab.ne_api("POST", "/sim/connections/sim-a/state", {"state": "down"})
            capturar(page, "conectividad_perdida", 14, "Network Engine informa conectividad perdida")
            lab.ne_api("POST", "/sim/connections/sim-a/state", {"state": "up"})
            capturar(page, "recuperada_con_revision", 14, "La red vuelve, pero lo incierto sigue pidiendo revisión")
            # información desactualizada: el motor deja de responder (congelado) unos segundos
            import signal
            os.kill(lab.ne.p.pid, signal.SIGSTOP)
            capturar(page, "conectividad_desactualizada", 26, "Motor congelado: la información de conectividad envejece")
            os.kill(lab.ne.p.pid, signal.SIGCONT)
            # motor apagado
            lab.ne.parar()
            capturar(page, "ne_no_disponible", 9, "Network Engine apagado: «sin diagnóstico», NO «sin internet»")
            lab.ne.iniciar()
            capturar(page, "ne_vuelve", 12, "El motor vuelve: la revisión pendiente sigue")
            # el operador resuelve en Network Engine y en Forms (orden de operador) → sin alertas
            d = http("GET", lab.fu + "/api/diagnostico")[1]
            if d.get("sesion_id"):
                lab.ne_api("POST", f"/sessions/{d['sesion_id']}/resolve", {})
            for o in lab.operaciones():
                if o["estado"] == "incierta":
                    import subprocess
                    subprocess.run([sys.executable, "-m", "cauce_form_connector", "operaciones", "revisar", o["id"],
                                    "--nota", "Comprobado en el portal de laboratorio: no consta", "--confirmar", o["id"]],
                                   cwd=str(ROOT), env=lab.env_forms, capture_output=True)
            capturar(page, "revisado_por_operador", 9, "Tras la revisión del operador (Network Engine y Forms)")
            nav.close()
    finally:
        lab.detener()
    (out / "capturas.json").write_text(json.dumps(hechas, ensure_ascii=False, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
