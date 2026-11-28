"""Descarga UNA vez la biblioteca Ext JS 6.2.0 (edición GPL) para el laboratorio 13 y deja solo los archivos necesarios
en lab_forms/vendor/extjs/ (carpeta ignorada por git: la biblioteca NO forma parte del repositorio ni del conector).

Origen: paquete público `extjs-gpl` del registro npm (~120 MB); se comprueba su huella SHA-256 antes de extraer.
No toca ninguna otra carpeta ni instala nada.   python scripts/preparar_lab_extjs.py
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINO = ROOT / "lab_forms" / "vendor" / "extjs"
URL = "https://registry.npmjs.org/extjs-gpl/-/extjs-gpl-6.2.0.tgz"
SHA256 = "c6b73ac23d810208a8da9d7c02cfdc910d90a49219d503264cdf01893bf1f962"
ARCHIVOS = {      # ruta dentro del paquete -> (nombre local, SHA-256 esperado)
    "package/build/ext-all.js": ("ext-all.js", "372eaf7a95982ae84915181e1fe36ac5c7252b7a374e56276eb523e382d240f2"),
    "package/build/classic/theme-classic/resources/theme-classic-all.css":
        ("theme-classic-all.css", "5cd0d853e5e28177a1c0b35aff298ac6e1a025787cd0f1f725f7d0670cb87770"),
    "package/build/classic/theme-classic/resources/theme-classic-all_1.css":
        ("theme-classic-all_1.css", "f0f066ebabd36f257801db2e3cad2f1b6092635d7e360ffda6f485006b44c81c"),
    "package/build/classic/theme-classic/resources/theme-classic-all_2.css":
        ("theme-classic-all_2.css", "37b7f200c770e2724e9b5ca1766158e1d0b4f71f38e7a9b180b8ba42c011cd03"),
    "package/LICENSE": ("LICENSE", ""),
}


def _huella(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def preparado() -> bool:
    return all((DESTINO / n).is_file() and (not sha or _huella(DESTINO / n) == sha) for n, sha in ARCHIVOS.values())


def main() -> int:
    if preparado():
        print(f"Ext JS ya está preparado en {DESTINO}")
        return 0
    print(f"Descargando {URL} (~120 MB, una sola vez)…")
    with tempfile.TemporaryDirectory() as tmp:
        tgz = Path(tmp) / "extjs.tgz"
        try:
            with urllib.request.urlopen(URL, timeout=120) as r, tgz.open("wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
        except OSError as e:
            print(f"No se pudo descargar: {e}", file=sys.stderr)
            return 1
        if _huella(tgz) != SHA256:
            print("La huella del paquete no coincide: no se extrae nada.", file=sys.stderr)
            return 1
        DESTINO.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tgz) as t:
            for miembro in t:
                if miembro.name in ARCHIVOS and miembro.isfile():
                    (DESTINO / ARCHIVOS[miembro.name][0]).write_bytes(t.extractfile(miembro).read())
    if not preparado():
        print("Faltan archivos o su huella no coincide tras extraer.", file=sys.stderr)
        return 1
    print(f"Listo: {DESTINO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
