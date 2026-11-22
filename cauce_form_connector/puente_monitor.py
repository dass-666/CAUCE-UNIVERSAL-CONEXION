"""Puente Monitor -> conector: de un expediente + un perfil de formulario configurable a una solicitud
`cauce.conector.v1`, ejecutada por el motor existente (`integracion.ejecutar`). NO duplica el motor.

Qué hace: toma SOLO los campos que el perfil de formulario declara (minimización), construye la solicitud con la huella
y la firma del perfil aprobado, lanza `preparar` (ventana visible, sin envío) y devuelve un informe legible SIN valores.
Qué no hace: no envía, no aprueba perfiles por sí solo (la aprobación es un paso humano explícito) y no guarda datos.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from . import contrato_v1 as C
from .config_store import ProfileStore
from .integracion import ejecutar
from .page_inspector import BrowserSession
from .settings import Settings, get_settings
from .signature import structure_signature_v2
from .url_policy import UrlPolicy

_ID_FRM = re.compile(r"frm_[a-z0-9_]{1,40}")
_ID_PERFIL = re.compile(r"[a-z0-9_]{1,40}")
_LOGICO = re.compile(r"[a-z][a-z0-9_]{0,39}")


class ConfigInvalida(ValueError):
    pass


def cargar_formulario(ruta: str | Path, *, url: Optional[str] = None, puerto: Optional[int] = None) -> dict:
    """Lee y valida un perfil de formulario configurable (JSON). `url` o `puerto` sustituyen a `{puerto}`."""
    try:
        cfg = json.loads(Path(ruta).read_text(encoding="utf-8"))
        campos_f, campos_e = cfg["campos_formulario"], cfg["campos_expediente"]
        ok = (cfg.get("version") == 1 and _ID_FRM.fullmatch(cfg["formulario_id"]) and _ID_PERFIL.fullmatch(cfg["perfil"])
              and isinstance(campos_f, dict) and campos_f and set(campos_f) == set(campos_e)
              and all(_LOGICO.fullmatch(k) for k in campos_f)
              and all(isinstance(v, str) for v in list(campos_f.values()) + list(campos_e.values())))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        ok = False
    if not ok:
        raise ConfigInvalida("Perfil de formulario inválido (revise version, formulario_id, perfil y que campos_formulario "
                             "y campos_expediente tengan los mismos nombres).")
    if url:
        cfg["url"] = url
    elif puerto is not None:
        cfg["url"] = cfg["url"].replace("{puerto}", str(puerto))
    if "{" in cfg["url"]:
        raise ConfigInvalida("La URL del formulario necesita --url o --puerto.")
    return cfg


def _obtener(expediente: dict, ruta: str) -> Any:
    cur: Any = expediente
    for parte in ruta.split("."):
        if not isinstance(cur, dict) or parte not in cur:
            return None
        cur = cur[parte]
    return cur


def extraer_campos(expediente: dict, cfg: dict) -> tuple[dict[str, str], list[str]]:
    """(valores a entregar, nombres lógicos sin dato). Solo se lee lo que el perfil declara; el resto del expediente
    no se toca. Un valor vacío o ausente NO se inventa: el campo queda para la persona."""
    valores, faltan = {}, []
    for logico in cfg["campos_formulario"]:
        v = _obtener(expediente, cfg["campos_expediente"][logico])
        if v is None or (isinstance(v, str) and not v.strip()):
            faltan.append(logico)
        else:
            valores[logico] = ("si" if v else "no") if isinstance(v, bool) else str(v)
    return valores, faltan


def aprobar_perfil(cfg: dict, settings: Settings, *, aprobado_por: str, autorizado: bool = False) -> dict:
    """Paso HUMANO: abre el formulario real, guarda el perfil aprobado y devuelve huella y firma que Monitor fija.
    Solo se debe llamar tras revisar los campos de `campos_formulario` frente al formulario."""
    if not re.fullmatch(r"rev_[a-z0-9_]{1,40}", aprobado_por):
        raise ConfigInvalida("aprobado_por debe tener la forma rev_xxx.")
    policy = UrlPolicy.load(settings.allowed_hosts_file)
    with BrowserSession(settings, headless=True) as s:
        s.open_url(policy.check(cfg["url"], autorizado), policy.guard(autorizado))
        analisis = s.inspect()
    if analisis.safety.blocked:
        raise ConfigInvalida("La página exige intervención humana; no se aprueba un perfil sobre ella.")
    ruta, sha = ProfileStore(settings.profiles_dir).save_integration(
        cfg["perfil"], analisis, cfg["campos_formulario"], formulario_id=cfg["formulario_id"],
        url=cfg["url"].split("?")[0].split("#")[0], aprobado_por_ref=aprobado_por)
    return {"perfil": cfg["perfil"], "perfil_sha256": sha, "firma": structure_signature_v2(analisis.all_fields),
            "archivo": str(ruta), "aprobado_por": aprobado_por}


def info_perfil(cfg: dict, settings: Settings) -> Optional[dict]:
    """Huella y firma del perfil aprobado ya guardado (None si no existe o no es un perfil de integración)."""
    store = ProfileStore(settings.profiles_dir)
    try:
        path = store.path_of(cfg["perfil"])
        perfil = json.loads(path.read_bytes().decode("utf-8"))
        return {"perfil_sha256": store.sha256_of_file(path), "firma": perfil["structure_signature_v2"],
                "aprobado_por": perfil["integracion"]["aprobado_por_ref"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def construir_solicitud(cfg: dict, info: dict, valores: dict[str, str], *, autorizacion_ref: str, otorgada_por: str,
                        vigente_dias: int = 1, espera_revision_s: int = 300, pausa_tecla_ms: int = 40,
                        ahora: Optional[datetime] = None) -> dict:
    ahora = ahora or datetime.now(timezone.utc)
    f = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "contrato": C.VERSION, "tipo": "preparar_formulario", "solicitud_id": "sol_" + secrets.token_hex(8),
        "emitida_en": f(ahora - timedelta(seconds=2)), "caduca_en": f(ahora + timedelta(seconds=300)),
        "autorizacion": {"ref": autorizacion_ref, "formulario_id": cfg["formulario_id"], "otorgada_por_ref": otorgada_por,
                         "vigente_hasta": (ahora + timedelta(days=vigente_dias)).date().isoformat(),
                         "consentimiento_vigente": True},
        "formulario": {"formulario_id": cfg["formulario_id"], "url": cfg["url"].split("?")[0].split("#")[0],
                       "perfil": cfg["perfil"], "perfil_sha256": info["perfil_sha256"],
                       "firma_estructura_esperada": info["firma"]},
        "campos": valores,
        "opciones": {"espera_revision_max_s": espera_revision_s, "pausa_tecla_ms": pausa_tecla_ms},
    }


def autorrellenar(cfg: dict, expediente: dict, settings: Optional[Settings] = None, *, autorizacion_ref: str = "aut_demo",
                  otorgada_por: str = "rev_demo", espera_revision_s: int = 300, pausa_tecla_ms: int = 40,
                  state_dir: Optional[str | Path] = None, **kw: Any) -> dict:
    """Ejecuta el recorrido completo. Devuelve {"salida", "respuesta", "informe"}; el informe NO contiene valores."""
    settings = settings or get_settings()
    info = info_perfil(cfg, settings)
    if info is None:
        return {"salida": 6, "respuesta": None, "informe": {"estado": "perfil_no_aprobado", "campos": [], "sin_dato": [],
                "nota": "No hay perfil aprobado: una persona debe revisar y aprobar el perfil primero."}}
    valores, faltan = extraer_campos(expediente, cfg)
    if not valores:
        return {"salida": 6, "respuesta": None, "informe": {"estado": "expediente_sin_datos_utiles", "campos": [],
                "sin_dato": faltan, "nota": "El expediente no trae ninguno de los campos del perfil."}}
    sol = construir_solicitud(cfg, info, valores, autorizacion_ref=autorizacion_ref, otorgada_por=otorgada_por,
                              espera_revision_s=espera_revision_s, pausa_tecla_ms=pausa_tecla_ms)
    resp, salida = ejecutar(json.dumps(sol), settings=settings, state_dir=state_dir, **kw)
    return {"salida": salida, "respuesta": resp, "informe": informe_legible(resp, cfg, settings, faltan)}


def informe_legible(resp: Optional[dict], cfg: dict, settings: Settings, sin_dato: list[str]) -> dict:
    """Resumen para la persona: etiqueta del campo, estado y nº de caracteres. Sin valores."""
    if resp is None:
        return {"estado": "solicitud_ilegible", "campos": [], "sin_dato": sin_dato}
    etiquetas: dict[str, str] = {}
    try:
        perfil = json.loads(ProfileStore(settings.profiles_dir).path_of(cfg["perfil"]).read_text(encoding="utf-8"))
        por_clave = {f["key"]: (f.get("label") or f.get("name") or f["key"]) for f in perfil["fields"]}
        etiquetas = {l: por_clave.get(k, k) for l, k in cfg["campos_formulario"].items()}
    except (OSError, ValueError, KeyError):
        pass
    campos = [{"campo": c["campo"], "etiqueta": etiquetas.get(c["campo"], c["campo"]), "estado": c["estado"],
               "caracteres": c["caracteres"], "correcto": c["estado"] in ("ok", "ok_normalizado")}
              for c in resp["campos"]]
    return {"estado": resp["estado"], "errores": [e["codigo"] for e in resp["errores"]], "campos": campos,
            "confirmaciones": resp["confirmaciones"], "sin_dato": sin_dato,
            "estructura_coincide": resp["formulario"]["estructura_coincide"],
            "envio_realizado_por_conector": resp["envio"]["realizado_por_conector"],
            "revision_humana": resp["revision_humana"]["resultado"]}


def texto_informe(inf: dict) -> str:
    L = [f"Estado: {inf['estado']}" + (f"  errores: {', '.join(inf['errores'])}" if inf.get("errores") else "")]
    for c in inf["campos"]:
        L.append(f"  [{'OK' if c['correcto'] else '!!'}] {c['etiqueta']}: {c['estado']} ({c['caracteres']} caracteres)")
    for c in inf.get("confirmaciones", []):
        L.append(f"  [{'OK' if c['coinciden'] else '!!'}] confirmación {c['campo']}: {'coincide' if c['coinciden'] else 'NO coincide'}")
    for s in inf.get("sin_dato", []):
        L.append(f"  [--] {s}: el expediente no trae dato; lo completa la persona")
    if "revision_humana" in inf:
        L.append(f"Revisión humana: {inf['revision_humana']}. El conector no envía (envío por conector: "
                 f"{inf['envio_realizado_por_conector']}).")
    if inf.get("nota"):
        L.append(inf["nota"])
    return "\n".join(L)
