"""Respaldo y restauración de la carpeta de estado de Forms (orden de OPERADOR: ``cauce-forms estado ...``).

Qué entra en el respaldo (un solo ``.zip``):

- ``estado/operaciones.json`` (y ``.bak`` si es válido): registro de operaciones, con las confirmadas archivadas;
- ``estado/huella.key``: clave de las huellas. Sin ella las huellas antiguas no casan con nada;
- ``estado/puesto.json``: identidad del puesto (etiqueta e id de sesión de Network Engine; no es un secreto);
- ``solicitudes_usadas/``: ids de solicitud de Monitor ya consumidos (evita aceptar dos veces la misma solicitud);
- ``config/allowed_hosts.json`` y ``perfiles/``: configuración necesaria para trabajar igual que antes.

Qué NO entra: la credencial de Network Engine (es un secreto y, en Windows, está cifrada con DPAPI para ese usuario:
el administrador emite otra), los registros de auditoría, cookies o datos de expedientes (Forms no los guarda).

Integridad: ``MANIFIESTO.json`` con el SHA-256 de cada archivo y un resumen de todos. Detecta daños y cambios
accidentales; NO protege frente a alguien que pueda reescribir a la vez el respaldo y su manifiesto (la clave va
dentro). Protección: el archivo se crea solo para el usuario (0600 / ACL de usuario y SYSTEM en la carpeta destino).
Guárdelo en un medio cifrado (BitLocker o similar): contiene ``huella.key``.

Restauración, reglas conservadoras (todas con pruebas en ``tests/test_respaldo.py``):

1. Se verifica el respaldo entero antes de tocar nada; si algo no casa, no se restaura.
2. No se restaura con una interfaz de Forms abierta sobre esa carpeta.
3. Lo que había se copia aparte (``<carpeta>.antes-restaurar-<fecha>``); nunca se borra.
4. Las operaciones del registro ACTUAL se conservan y se suman a las del respaldo (misma clave): una restauración
   antigua nunca rebaja ni quita una operación incierta, confirmada o revisada posterior al respaldo.
5. Lo que estaba ``en_curso`` pasa a ``incierta``.
6. SIEMPRE queda una marca GENERAL: no se envía nada hasta que un operador revise lo ocurrido desde la fecha del
   respaldo (``operaciones revisar GENERAL``). Si el registro actual usaba otra clave y no se pudo sumar, la marca lo dice.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .operaciones import ARCHIVO, CLAVE, VIVO, RegistroOperaciones, _otro_forms_vivo, proteger_carpeta

ESQUEMA = "cauce.forms.respaldo/1"
MANIFIESTO = "MANIFIESTO.json"
PUESTO = "puesto.json"
MAX_ARCHIVO = 20 * 1024 * 1024


class RespaldoInvalido(ValueError):
    pass


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _resumen(man: dict) -> str:
    """Resumen del manifiesto entero (archivos y metadatos), sin el propio resumen."""
    return _sha(json.dumps({k: v for k, v in man.items() if k != "resumen"}, sort_keys=True, ensure_ascii=False).encode())


def _state_dir() -> Path:
    return Path(os.environ.get("CAUCE_STATE_DIR", "state"))


def _leer_registro(p: Path) -> Optional[dict]:
    """Lee un operaciones.json SIN abrir un registro (abrirlo podría crear una clave nueva)."""
    return RegistroOperaciones._leer(None, p) if p.exists() else None   # _leer no usa el estado de la instancia


def _entradas(settings: Any) -> dict[str, bytes]:
    data = Path(settings.data_dir)
    out: dict[str, bytes] = {}
    for nombre in (ARCHIVO, ARCHIVO + ".bak", CLAVE, PUESTO):
        p = data / nombre
        if p.is_file():
            b = p.read_bytes()
            if nombre.startswith(ARCHIVO) and _leer_registro(p) is None:
                if nombre == ARCHIVO:
                    raise RespaldoInvalido("operaciones.json está dañado: revise el registro antes de respaldarlo")
                continue                                   # un .bak dañado no se respalda
            out[f"estado/{nombre}"] = b
    if f"estado/{CLAVE}" not in out and f"estado/{ARCHIVO}" in out:
        raise RespaldoInvalido("falta huella.key: un respaldo sin la clave no protegería contra duplicados")
    usadas = _state_dir() / "solicitudes_usadas"
    if usadas.is_dir():
        for p in sorted(usadas.iterdir()):
            if p.is_file() and p.name.startswith("sol_"):
                out[f"solicitudes_usadas/{p.name}"] = p.read_bytes()[:200]
    hosts = Path(settings.allowed_hosts_file)
    if hosts.is_file():
        out["config/allowed_hosts.json"] = hosts.read_bytes()
    perfiles = Path(settings.profiles_dir)
    if perfiles.is_dir():
        for p in sorted(perfiles.rglob("*")):
            if p.is_file() and p.stat().st_size <= MAX_ARCHIVO:
                out["perfiles/" + p.relative_to(perfiles).as_posix()] = p.read_bytes()
    return out


def respaldar(settings: Any, destino: str | Path) -> dict:
    data = Path(settings.data_dir)
    if not (data / ARCHIVO).exists():                 # registro recién creado y aún vacío: se escribe para respaldarlo
        reg0 = RegistroOperaciones(data)
        with reg0._candado():
            reg0._guardar()
    entradas = _entradas(settings)
    if f"estado/{ARCHIVO}" not in entradas:
        raise RespaldoInvalido("no hay registro de operaciones que respaldar")
    reg = _leer_registro(Path(settings.data_dir) / ARCHIVO)
    archivos = {k: _sha(v) for k, v in sorted(entradas.items())}
    ahora = datetime.now(timezone.utc)
    man = {"esquema": ESQUEMA, "creado": ahora.isoformat(timespec="seconds"), "version_forms": __version__,
           "clave_id": (reg or {}).get("clave_id"), "operaciones": len((reg or {}).get("operaciones", [])),
           "archivadas": len((reg or {}).get("archivadas", {})), "archivos": archivos}
    man["resumen"] = _resumen(man)
    d = Path(destino)
    d.mkdir(parents=True, exist_ok=True)
    proteccion = proteger_carpeta(d)
    ruta = d / f"respaldo-forms-{ahora.strftime('%Y%m%dT%H%M%SZ')}.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in sorted(entradas.items()):
            z.writestr(k, v)
        z.writestr(MANIFIESTO, json.dumps(man, ensure_ascii=False, indent=1))
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(buf.getvalue())
        fh.flush()
        os.fsync(fh.fileno())
    verificar(ruta)                                       # se relee lo escrito: un respaldo que no se verifica no vale
    return {"respaldo": str(ruta), "archivos": len(archivos), "operaciones": man["operaciones"],
            "archivadas": man["archivadas"], "proteccion": proteccion, "sha256": _sha(ruta.read_bytes())}


def verificar(ruta: str | Path) -> tuple[dict, dict[str, bytes]]:
    """Comprueba el respaldo entero. Devuelve (manifiesto, contenido). Lanza RespaldoInvalido si algo no casa."""
    try:
        with zipfile.ZipFile(ruta) as z:
            nombres = z.namelist()
            if MANIFIESTO not in nombres:
                raise RespaldoInvalido("falta el manifiesto")
            man = json.loads(z.read(MANIFIESTO))
            contenido = {}
            for n in nombres:
                if n == MANIFIESTO:
                    continue
                if n.startswith("/") or ".." in Path(n).parts or z.getinfo(n).file_size > MAX_ARCHIVO:
                    raise RespaldoInvalido(f"entrada no admitida: {n}")
                contenido[n] = z.read(n)
    except (OSError, zipfile.BadZipFile, ValueError, KeyError) as exc:
        if isinstance(exc, RespaldoInvalido):
            raise
        raise RespaldoInvalido(f"no se puede leer el respaldo ({type(exc).__name__})")
    if not isinstance(man, dict) or man.get("esquema") != ESQUEMA or not isinstance(man.get("archivos"), dict):
        raise RespaldoInvalido("manifiesto no reconocido")
    if man.get("resumen") != _resumen(man):
        raise RespaldoInvalido("el manifiesto fue modificado")
    if set(contenido) != set(man["archivos"]):
        raise RespaldoInvalido("faltan o sobran archivos respecto al manifiesto")
    for n, b in contenido.items():
        if _sha(b) != man["archivos"][n]:
            raise RespaldoInvalido(f"archivo dañado o modificado: {n}")
    if f"estado/{ARCHIVO}" not in contenido or f"estado/{CLAVE}" not in contenido:
        raise RespaldoInvalido("el respaldo no tiene registro y clave")
    if len(contenido[f"estado/{CLAVE}"]) != 32:
        raise RespaldoInvalido("huella.key del respaldo no es válida")
    return man, contenido


def _escribir(p: Path, b: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".restaurando")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(b)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def restaurar(settings: Any, ruta: str | Path) -> dict:
    man, contenido = verificar(ruta)
    data = Path(settings.data_dir)
    if _otro_forms_vivo(data / VIVO):
        raise RespaldoInvalido("hay una ventana de Cauce Forms abierta con esta carpeta: ciérrela antes de restaurar")
    tmpdir = data.parent / f".{data.name}.verif-{os.getpid()}"   # el registro del respaldo debe ser legible por Forms
    tmpdir.mkdir(parents=True, exist_ok=True)
    try:
        (tmpdir / ARCHIVO).write_bytes(contenido[f"estado/{ARCHIVO}"])
        reg_respaldo = _leer_registro(tmpdir / ARCHIVO)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if reg_respaldo is None:
        raise RespaldoInvalido("el registro del respaldo no es válido")

    # 1) lo actual se aparta (copia), nunca se borra
    ahora = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    aparte = data.with_name(f"{data.name}.antes-restaurar-{ahora}")
    actual_reg, actual_clave = None, None
    if data.is_dir():
        aparte.mkdir(parents=True, exist_ok=False)
        proteger_carpeta(aparte)
        for p in data.iterdir():
            if p.is_file() and p.name not in (VIVO, "operaciones.lock", ".acl_aplicada"):
                shutil.copy2(p, aparte / p.name)
        actual_reg = _leer_registro(data / ARCHIVO)
        if (data / CLAVE).is_file():
            actual_clave = (data / CLAVE).read_bytes()
    clave_respaldo = contenido[f"estado/{CLAVE}"]
    misma_clave = actual_clave is None or actual_clave == clave_respaldo

    # 2) se escriben clave, registro y puesto del respaldo; el .bak antiguo se quita para que no se use en su lugar
    data.mkdir(parents=True, exist_ok=True)
    proteger_carpeta(data)
    (data / (ARCHIVO + ".bak")).unlink(missing_ok=True)
    _escribir(data / CLAVE, clave_respaldo)
    _escribir(data / ARCHIVO, contenido[f"estado/{ARCHIVO}"])
    if f"estado/{PUESTO}" in contenido:
        _escribir(data / PUESTO, contenido[f"estado/{PUESTO}"])
    # ids de solicitud consumidos: se SUMAN (nunca se quitan los actuales)
    usadas = _state_dir() / "solicitudes_usadas"
    sumadas = 0
    for n, b in contenido.items():
        if n.startswith("solicitudes_usadas/"):
            p = usadas / Path(n).name
            if not p.exists():
                _escribir(p, b)
                sumadas += 1
    # configuración: solo si falta (la actual, más reciente, se conserva)
    config = {"restaurada": [], "conservada_actual": []}
    destinos = {"config/allowed_hosts.json": Path(settings.allowed_hosts_file)}
    for n in contenido:
        if n.startswith("perfiles/"):
            destinos[n] = Path(settings.profiles_dir) / n.removeprefix("perfiles/")
    for n, p in destinos.items():
        if n not in contenido:
            continue
        if p.exists():
            config["conservada_actual"].append(n)
        else:
            _escribir(p, contenido[n])
            config["restaurada"].append(n)

    # 3) se abre el registro restaurado, se suma lo actual y SIEMPRE queda la marca general
    reg = RegistroOperaciones(data)
    sumadas_ops = 0
    if actual_reg is not None and misma_clave:
        sumadas_ops = reg.incorporar(actual_reg["operaciones"], actual_reg["archivadas"])
    if actual_reg is not None and not misma_clave and actual_reg["operaciones"]:
        motivo = "restaurado_registro_actual_con_otra_clave"     # no se pueden sumar: revisar a mano lo apartado
    elif actual_reg is None and (aparte / ARCHIVO).exists():
        motivo = "restaurado_registro_actual_ilegible"
    else:
        motivo = "restaurado_desde_respaldo"
    reg.marcar_general(motivo)
    return {"restaurado_de": str(ruta), "respaldo_creado": man.get("creado"), "aparte": str(aparte) if aparte.exists() else None,
            "operaciones_del_respaldo": len(reg_respaldo["operaciones"]), "operaciones_actuales_sumadas": sumadas_ops,
            "solicitudes_usadas_sumadas": sumadas, "configuracion": config, "revision_general": motivo,
            "aviso": "No se enviará nada hasta que un operador revise lo ocurrido desde la fecha del respaldo: "
                     "cauce-forms operaciones revisar GENERAL --nota ... --confirmar GENERAL"}


def orden_estado(args: Any, settings: Any) -> int:
    def emit(o: dict) -> None:
        print(json.dumps(o, ensure_ascii=False, indent=1))
    try:
        if args.accion == "respaldar":
            emit(respaldar(settings, args.destino))
        elif args.accion == "verificar":
            man, cont = verificar(args.respaldo)
            emit({"valido": True, "creado": man.get("creado"), "archivos": len(cont), "operaciones": man.get("operaciones"),
                  "archivadas": man.get("archivadas")})
        else:
            if args.confirmar != "RESTAURAR":
                print("No se restauró nada: para confirmar escriba exactamente --confirmar RESTAURAR", file=sys.stderr)
                return 1
            emit(restaurar(settings, args.respaldo))
    except (RespaldoInvalido, OSError) as exc:
        print(f"No se hizo nada: {exc}", file=sys.stderr)
        return 1
    return 0
