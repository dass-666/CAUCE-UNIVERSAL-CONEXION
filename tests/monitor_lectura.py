"""Ejecuta, en un proceso aparte, el código REAL de lectura de red de Cauce Monitor (``monitoreo/red_lectura``).

Solo para pruebas de compatibilidad (``tests/test_compat_monitor.py``). No modifica Monitor: añade su carpeta a
``sys.path`` (``CAUCE_MONITOR_REPO``) y usa sus variables de entorno oficiales (``MONITOREO_NETWORK_*``).
Cada ejecución es un «Monitor recién arrancado».

  python tests/monitor_lectura.py leer               -> JSON con lo que mostraría Monitor (motor y sesiones)
  python tests/monitor_lectura.py resolver PID NOTA CONFIRMAR   -> resuelve con la credencial «review» de Monitor
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.environ["CAUCE_MONITOR_REPO"])
from monitoreo.red_lectura import revisiones, servicio  # noqa: E402


def leer() -> dict:
    srv, razon = servicio.desde_entorno()
    if srv is None:
        return {"network_engine": razon}
    fin = time.monotonic() + 15
    while True:
        srv.paso()
        e = srv.estado()
        if e["network_engine"] != "disponible" or e.get("sesiones") is not None or time.monotonic() > fin:
            return {"network_engine": e["network_engine"], "sesiones": e.get("sesiones"),
                    "revisiones_resolubles": e.get("revisiones_resolubles")}
        time.sleep(0.5)


def main() -> int:
    if sys.argv[1] == "leer":
        print(json.dumps(leer(), ensure_ascii=False))
        return 0
    pid, nota, confirmar = sys.argv[2:5]
    err = revisiones.validar(pid, nota, confirmar)
    if err:
        print(json.dumps({"error": err}))
        return 1
    try:
        print(json.dumps({"ok": revisiones.resolver(pid, nota, confirmar)}, ensure_ascii=False, default=str))
        return 0
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": type(e).__name__, "status": getattr(e, "status", None),
                          "code": getattr(e, "code", None)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
