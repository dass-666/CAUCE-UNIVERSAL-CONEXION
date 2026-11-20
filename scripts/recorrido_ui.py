"""Recorrido completo de la INTERFAZ, como lo haría una persona, con capturas de cada paso.

1. Abre la interfaz local.  2. Analiza un formulario de laboratorio.  3. Importa el Excel ficticio.
4. Muestra las correspondencias.  5. Rellena carácter por carácter.  6. Comprueba confirmaciones
de correo y teléfono (laboratorio 3).  7. Comprueba campos dinámicos (laboratorio 2).
8. Comprueba que nada se envía sin intervención humana.

Uso:  python scripts/recorrido_ui.py            (con ventana visible)
      python scripts/recorrido_ui.py --headless
Salida: evidence/out/recorrido/*.png y recorrido.json. Código 0 si todos los pasos pasan.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evidence" / "out" / "recorrido"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    env = dict(os.environ, CAUCE_HEADLESS="1" if args.headless else "0",
               CAUCE_AUDIT_PATH=str(OUT / "audit.jsonl"))
    app = subprocess.Popen([sys.executable, "-m", "cauce_form_connector.web.app", "--no-open", "--port", str(port)],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    pasos: list[dict] = []

    def paso(n: int, nombre: str, ok: bool, detalle: str = "") -> None:
        pasos.append({"paso": n, "nombre": nombre, "ok": bool(ok), "detalle": detalle})
        print(f"[{'OK' if ok else 'FALLO'}] {n}. {nombre} {detalle}")

    try:
        for _ in range(200):
            try:
                httpx.get(base + "/api/status")
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        kw = {"executable_path": os.environ["CAUCE_BROWSER_PATH"]} if os.environ.get("CAUCE_BROWSER_PATH") else {}
        with sync_playwright() as p:
            b = p.chromium.launch(headless=args.headless, **kw)
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            pg.goto(base + "/")
            paso(1, "Abrir la interfaz local", "Conector universal" in pg.title() + pg.text_content("h1"))
            pg.screenshot(path=str(OUT / "1_interfaz.png"))

            for lab, fila, chequeo in (("03_validaciones.html", "0", "confirmaciones"), ("02_dinamico.html", "1", "dinamicos")):
                pg.select_option("#lab-sel", lab)
                pg.click("#btn-analizar")
                pg.wait_for_selector("#analisis-msg.ok", timeout=60000)
                paso(2, f"Analizar {lab}", True, pg.text_content("#analisis-msg"))
                pg.set_input_files("#data-file", str(ROOT / "sample_data" / "personas_ficticias.xlsx"))
                pg.wait_for_selector("#datos-msg.ok")
                paso(3, "Importar Excel ficticio", "2 fila" in pg.text_content("#datos-msg"), pg.text_content("#datos-msg"))
                pg.wait_for_selector("#map-tabla select")
                n_map = pg.locator("#map-tabla select").count()
                paso(4, "Revisar correspondencias", n_map > 0, f"{n_map} correspondencias")
                pg.screenshot(path=str(OUT / f"4_correspondencias_{Path(lab).stem}.png"), full_page=True)
                pg.select_option("#row-sel", fila)
                pg.click("#btn-fill")
                pg.wait_for_selector("#fill-msg.ok, #fill-msg.aviso, #fill-msg.error", timeout=120000)
                texto = pg.text_content("#resultados") or ""
                paso(5, f"Rellenar carácter por carácter ({lab})", "teclado_secuencial" in texto
                     and pg.locator("#fill-msg.ok").count() == 1, pg.text_content("#fill-msg"))
                pg.wait_for_selector("#captura img")
                pg.wait_for_function("document.querySelector('#captura img').complete")
                pg.screenshot(path=str(OUT / f"5_resultado_{Path(lab).stem}.png"), full_page=True)
                if chequeo == "confirmaciones":
                    items = pg.locator("#resultados li").all_text_contents()
                    paso(6, "Correo y teléfono coinciden con sus confirmaciones",
                         len(items) == 2 and all("coinciden" in t and "NO" not in t for t in items), " | ".join(items))
                else:
                    paso(7, "Campos dinámicos aparecen y se rellenan",
                         "apareció al rellenar" in texto and "Tostadores Ficticios" in texto)
            eventos = [e["event"] for e in httpx.get(base + "/api/audit").json()]
            sin_envio = "human_submit" not in eventos and "Detenido antes del envío" in pg.text_content("#fill-msg")
            paso(8, "No se envía sin intervención humana", sin_envio,
                 "sin evento human_submit en auditoría; botón de envío detectado y no pulsado")
            r = httpx.post(base + "/api/submit", json={"phrase": "no"})
            paso(8, "El envío exige escribir ENVIAR", r.status_code == 403, f"HTTP {r.status_code}")
            b.close()
    finally:
        try:
            httpx.post(base + "/api/close", json={})
        except httpx.HTTPError:
            pass
        app.terminate()
        try:
            app.wait(10)
        except subprocess.TimeoutExpired:
            app.kill()

    ok = all(p["ok"] for p in pasos)
    (OUT / "recorrido.json").write_text(json.dumps({"ok": ok, "pasos": pasos}, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print("RECORRIDO SUPERADO" if ok else "RECORRIDO CON FALLOS", "->", OUT)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
