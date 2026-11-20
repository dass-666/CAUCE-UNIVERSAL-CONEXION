"""Ejemplo reproducible: cómo Cauce Monitor PODRÍA usar el conector (no es una integración real).

Simula el papel de Cauce Monitor con datos ficticios y usa solo la API HTTP local que ya
existe (sin endpoints nuevos):

1. Arranca el conector como proceso independiente en 127.0.0.1.
2. Entrega UNA fila ficticia (en memoria, sin escribirla en disco).
3. Pide el análisis de un formulario de laboratorio y las correspondencias propuestas.
4. Si alguna correspondencia necesita revisión, se detiene: eso es trabajo humano.
5. Ordena el relleno y recibe el `FillReport`.
6. Guarda su propio registro SIN valores personales (solo estados) y marca el caso como
   «pendiente de confirmación humana».
7. Solo una persona, escribiendo ENVIAR en esta consola, puede confirmar el envío.
   Con --no-interactivo (pruebas/CI) la confirmación nunca se da y se comprueba que no hubo envío.
8. Cierra la sesión del conector, que descarta los datos de la memoria.

Uso:
    python scripts/ejemplo_cauce_monitor.py                  # interactivo
    python scripts/ejemplo_cauce_monitor.py --no-interactivo # demostración automática sin envío
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

# Fila ficticia tal como la tendría Cauce Monitor (dominio reservado example.com)
FILA_FICTICIA = {
    "Nombre completo": "Mariana Ejemplo Prueba",
    "Correo electrónico": "mariana.prueba@example.com",
    "Teléfono": "5587654321",
    "Código postal": "03100",
    "Ciudad": "Ciudad de México",
    "Acepta privacidad": "sí",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-interactivo", action="store_true")
    ap.add_argument("--formulario", default="03_validaciones.html")
    ap.add_argument("--salida", default=str(ROOT / "evidence" / "out" / "ejemplo_monitor_registro.json"))
    args = ap.parse_args()

    port = _free_port()
    env = dict(os.environ)
    if args.no_interactivo:
        env.setdefault("CAUCE_HEADLESS", "1")
    env.setdefault("CAUCE_AUDIT_PATH", str(ROOT / "evidence" / "out" / "ejemplo_monitor_audit.jsonl"))
    conector = subprocess.Popen(
        [sys.executable, "-m", "cauce_form_connector.web.app", "--no-open", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f"http://127.0.0.1:{port}"
    c = httpx.Client(base_url=base, timeout=120)
    try:
        for _ in range(200):
            try:
                c.get("/api/status")
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("El conector no arrancó: " + conector.stderr.read().decode(errors="ignore")[-500:])

        # 2. Entregar la fila en memoria (multipart JSON; no se escribe ningún archivo)
        datos = json.dumps([FILA_FICTICIA], ensure_ascii=False).encode()
        c.post("/api/data", files={"file": ("fila.json", datos, "application/json")}).raise_for_status()

        # 3. Analizar y obtener propuestas
        r = c.post("/api/analyze", json={"mode": "lab", "lab": args.formulario})
        r.raise_for_status()
        res = r.json()
        if res["analysis"]["safety"]["blocked"]:
            print("La página exige intervención humana:", res["analysis"]["safety"]["reasons"])
            return 4
        props = res["proposals"]

        # 4. Política de Cauce Monitor: solo se preaprueba lo que el conector marca "auto"
        revisar = [p["field_key"] for p in props if p["status"] == "revisar"]
        if revisar:
            print("Correspondencias que necesitan revisión humana:", revisar)
            return 5
        mapping = {p["field_key"]: p["column"] for p in props}

        # 5. Rellenar (el conector se detiene antes de enviar)
        rep = c.post("/api/fill", json={"mapping": mapping, "row_index": 0}).json()["report"]

        # 6. Registro de Cauce Monitor sin valores personales
        registro = {
            "caso": "demo-0001",
            "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "formulario": res["analysis"]["source_label"],
            "resultado_conector": "ok" if rep["ok"] else "incidencias",
            "detenido_antes_de_enviar": rep["stopped_before_submit"],
            "campos": [{"campo": x["field_key"], "metodo": x["method"], "estado": x["status"],
                        "caracteres": x["keystrokes"]} for x in rep["results"]],
            "confirmaciones": [{"campo": x["confirmacion"], "coinciden": x["match"]} for x in rep["confirmations"]],
            "estado": "pendiente_confirmacion_humana" if rep["ok"] else "requiere_revision",
            "confirmado_por_humano": False,
        }

        # 7. Confirmación humana
        if rep["ok"] and not args.no_interactivo:
            print("\nRevise la ventana del navegador. Para enviar escriba ENVIAR; cualquier otra cosa cancela.")
            if input("> ").strip() == "ENVIAR":
                r = c.post("/api/submit", json={"phrase": "ENVIAR"})
                registro["confirmado_por_humano"] = r.status_code == 200
                registro["estado"] = "enviado_por_confirmacion_humana" if r.status_code == 200 else "envio_fallido"
            else:
                registro["estado"] = "cancelado_por_humano"

        eventos = [e["event"] for e in c.get("/api/audit").json()]
        registro["envio_en_auditoria"] = "human_submit" in eventos

        Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
        Path(args.salida).write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(registro, ensure_ascii=False, indent=2))

        if args.no_interactivo:
            ok = rep["ok"] and all(x["coinciden"] for x in registro["confirmaciones"]) \
                and not registro["envio_en_auditoria"] and registro["estado"] == "pendiente_confirmacion_humana"
            print("EJEMPLO SUPERADO: relleno verificado y sin envío" if ok else "EJEMPLO CON FALLOS")
            return 0 if ok else 1
        return 0
    finally:
        # 8. Cerrar y descartar datos de la memoria del conector
        try:
            c.post("/api/close", json={})
        except httpx.HTTPError:
            pass
        conector.terminate()
        try:
            conector.wait(10)
        except subprocess.TimeoutExpired:
            conector.kill()


if __name__ == "__main__":
    raise SystemExit(main())
