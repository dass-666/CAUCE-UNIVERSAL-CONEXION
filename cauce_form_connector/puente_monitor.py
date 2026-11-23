"""Puente Monitor -> conector: de un expediente + un perfil de formulario configurable a una solicitud
`cauce.conector.v1`, ejecutada por el motor existente (`integracion.ejecutar`). NO duplica el motor.

Qué hace: toma SOLO los campos que el perfil de formulario declara (minimización), construye la solicitud con la huella
y la firma del perfil aprobado, lanza `preparar` (ventana visible, sin envío) y devuelve un informe legible SIN valores.
Qué no hace: no envía, no aprueba perfiles por sí solo (la aprobación es un paso humano explícito) y no guarda datos.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from . import contrato_v1 as C
from . import esquema_monitor as M
from .config_store import ProfileStore
from .integracion import ejecutar
from .page_inspector import BrowserSession
from .settings import Settings, get_settings
from .signature import structure_signature_v2
from .url_policy import UrlPolicy

_ID_FRM = re.compile(r"frm_[a-z0-9_]{1,40}")
_ID_PERFIL = re.compile(r"[a-z0-9_]{1,40}")
_LOGICO = re.compile(r"[a-z][a-z0-9_]{0,39}")
_CODIGO = re.compile(r"[A-Za-z0-9_\-]{1,40}")
MANUAL = "@manual"            # campos_expediente: el dato lo escribe la persona en la ventana, para un solo uso
FUENTE = "@fuente:"           # campos_expediente: "@fuente:<nombre>[:<columna>]" -> fuente de datos local configurada
_FUENTE = re.compile(r"@fuente:([a-z0-9_]{1,40})(?::([a-z][a-z0-9_]{0,39}))?")
# Conversión explícita AAAA-MM-DD -> formato que pide el formulario. Solo estos, y solo si el perfil lo declara.
FORMATOS_FECHA = {"AAAA-MM-DD": "%Y-%m-%d", "DD/MM/AAAA": "%d/%m/%Y", "DD-MM-AAAA": "%d-%m-%Y",
                  "DD.MM.AAAA": "%d.%m.%Y", "MM/DD/AAAA": "%m/%d/%Y", "AAAA/MM/DD": "%Y/%m/%d"}


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
    cfg["equivalencias"] = equivalencias_normalizadas(cfg)
    cfg["formatos_fecha"] = formatos_normalizados(cfg)
    if url:
        cfg["url"] = url
    elif puerto is not None:
        cfg["url"] = cfg["url"].replace("{puerto}", str(puerto))
    if "{" in cfg["url"]:
        raise ConfigInvalida("La URL del formulario necesita --url o --puerto.")
    return cfg


def equivalencias_normalizadas(cfg: dict) -> dict:
    """Valida y normaliza las tablas `equivalencias` del perfil de formulario:
    {nombre_logico: {"campo_monitor": "tipo_tramite", "valores": {"<código de Monitor>": "<valor de la opción>"}}}.
    Reglas: el campo lógico existe y apunta a ese campo de Monitor; el campo de Monitor es un código; los códigos son
    del catálogo de Monitor (si se conoce) y cada valor es texto no vacío. Además, todo campo lógico que lea un código
    de Monitor DEBE tener tabla: un código nunca se escribe tal cual. Otras claves (comentarios) se ignoran."""
    crudo = cfg.get("equivalencias") or {}
    if not isinstance(crudo, dict):
        raise ConfigInvalida("«equivalencias» debe ser un objeto.")
    out: dict[str, dict] = {}
    for logico, t in crudo.items():
        campo = t.get("campo_monitor") if isinstance(t, dict) else None
        valores = t.get("valores") if isinstance(t, dict) else None
        spec = M.ESQUEMA.get(campo or "")
        if (logico not in cfg["campos_formulario"] or cfg["campos_expediente"].get(logico) != campo or not spec
                or spec["tipo"] != "codigo" or not isinstance(valores, dict) or not valores
                or not all(isinstance(k, str) and _CODIGO.fullmatch(k) and isinstance(v, str) and v.strip()
                           for k, v in valores.items())
                or (spec["codigos"] and not set(valores) <= set(spec["codigos"]))):
            raise ConfigInvalida(f"Tabla de equivalencias inválida para «{logico}» (revise campo_monitor, que coincida "
                                 "con campos_expediente y que los códigos existan en Monitor).")
        out[logico] = {"campo_monitor": campo, "valores": {k: valores[k] for k in sorted(valores)}}
    for logico, ruta in cfg["campos_expediente"].items():
        if ruta in M.ESQUEMA and M.ESQUEMA[ruta]["tipo"] == "codigo" and logico not in out:
            raise ConfigInvalida(f"«{logico}» lee el código de Monitor «{ruta}» y no tiene tabla de equivalencias.")
    return {k: out[k] for k in sorted(out)}


def formatos_normalizados(cfg: dict) -> dict:
    """`formatos_fecha`: {nombre_logico: "DD/MM/AAAA"}. Solo para campos que leen una fecha del expediente."""
    crudo = cfg.get("formatos_fecha") or {}
    if not isinstance(crudo, dict):
        raise ConfigInvalida("«formatos_fecha» debe ser un objeto.")
    for logico, fmt in crudo.items():
        spec = M.ESQUEMA.get(cfg["campos_expediente"].get(logico, ""))
        if logico not in cfg["campos_formulario"] or not spec or spec["tipo"] != "fecha" or fmt not in FORMATOS_FECHA:
            raise ConfigInvalida(f"Formato de fecha inválido para «{logico}» (formatos admitidos: "
                                 f"{', '.join(FORMATOS_FECHA)}; el campo debe leer una fecha del expediente).")
    return {k: crudo[k] for k in sorted(crudo)}


def leer_fuente(nombre: str, columna: str, expediente: dict, directorio: Optional[str | Path] = None) -> tuple[Optional[str], str]:
    """Busca un identificador legítimo en una fuente local configurada por la persona: `<dir>/<nombre>.json` con
    {"clave_expediente": "<campo de Monitor>", "registros": {"<valor de ese campo>": {"<columna>": "<dato>"}}}.
    Devuelve (dato, "") o (None, motivo exacto). El motivo nunca incluye valores. Nada se inventa ni se guarda."""
    ruta = Path(directorio or os.environ.get("CAUCE_FUENTES_DIR", "fuentes")) / f"{nombre}.json"
    try:
        fuente = json.loads(ruta.read_text(encoding="utf-8"))
        clave, registros = fuente["clave_expediente"], fuente["registros"]
        if clave not in M.ESQUEMA or not isinstance(registros, dict):
            raise ValueError
    except FileNotFoundError:
        return None, f"no existe la fuente de datos «{nombre}» ({nombre}.json en la carpeta de fuentes)"
    except (OSError, ValueError, KeyError, TypeError):
        return None, f"la fuente de datos «{nombre}» no tiene el formato esperado (clave_expediente y registros)"
    k = expediente.get(clave)
    if k in (None, ""):
        return None, f"el expediente no trae «{clave}», que es la clave para buscar en «{nombre}»"
    reg = registros.get(str(k))
    if not isinstance(reg, dict):
        return None, f"la fuente «{nombre}» no tiene registro para este expediente (buscado por «{clave}»)"
    v = reg.get(columna)
    if not isinstance(v, str) or not v.strip():
        return None, f"el registro de la fuente «{nombre}» no tiene «{columna}»"
    return v.strip(), ""


def campos_manuales(cfg: dict) -> list[str]:
    return [l for l, r in cfg["campos_expediente"].items() if r == MANUAL]


def _obtener(expediente: dict, ruta: str) -> Any:
    cur: Any = expediente
    for parte in ruta.split("."):
        if not isinstance(cur, dict) or parte not in cur:
            return None
        cur = cur[parte]
    return cur


def extraer_campos(expediente: dict, cfg: dict, manuales: Optional[dict[str, str]] = None,
                   motivos: Optional[dict[str, str]] = None) -> tuple[dict[str, str], list[str]]:
    """(valores a entregar, nombres lógicos sin dato). Solo se lee lo que el perfil declara; el resto del expediente
    no se toca. Un valor vacío o ausente NO se inventa: el campo queda para la persona. Los campos `@manual` toman el
    dato que la persona escribió para esta ejecución (`manuales`), que no se guarda en ningún sitio."""
    valores, faltan = {}, []
    equiv = cfg.get("equivalencias") or {}
    formatos = cfg.get("formatos_fecha") or {}
    manuales = manuales or {}
    motivos = motivos if motivos is not None else {}
    for logico in cfg["campos_formulario"]:
        ruta = cfg["campos_expediente"][logico]
        m = _FUENTE.fullmatch(ruta)
        if m:
            v, motivo = leer_fuente(m.group(1), m.group(2) or logico, expediente)
            if v is None:
                faltan.append(logico)
                motivos[logico] = motivo
            else:
                valores[logico] = v
            continue
        if ruta == MANUAL:
            v = str(manuales.get(logico) or "").strip()
            if not v or len(v) > 200 or "\n" in v:
                faltan.append(logico)
            else:
                valores[logico] = v
            continue
        v = _obtener(expediente, ruta)
        if v is None or (isinstance(v, str) and not v.strip()):
            faltan.append(logico)
            continue
        texto = ("si" if v else "no") if isinstance(v, bool) else str(v)
        if logico in equiv:
            # Código de Monitor -> valor de opción SOLO por la tabla aprobada; un código sin fila no se adivina
            texto = equiv[logico]["valores"].get(texto)
            if texto is None:
                faltan.append(logico)
                continue
        if logico in formatos:
            try:
                texto = datetime.strptime(texto, "%Y-%m-%d").strftime(FORMATOS_FECHA[formatos[logico]])
            except ValueError:            # fecha del expediente no válida: no se adivina
                faltan.append(logico)
                motivos[logico] = "la fecha del expediente no es AAAA-MM-DD; no se convierte"
                continue
        valores[logico] = texto
    return valores, faltan


def codigos_sin_equivalencia(expediente: dict, cfg: dict) -> list[str]:
    """Campos lógicos cuyo código de Monitor no está en la tabla aprobada (los completa la persona)."""
    out = []
    for logico, t in (cfg.get("equivalencias") or {}).items():
        v = _obtener(expediente, cfg["campos_expediente"][logico])
        if v not in (None, "") and str(v) not in t["valores"]:
            out.append(logico)
    return out


def aprobar_perfil(cfg: dict, settings: Settings, *, aprobado_por: str, autorizado: bool = False) -> dict:
    """Paso HUMANO: abre el formulario real, guarda el perfil aprobado y devuelve huella y firma que Monitor fija.
    Solo se debe llamar tras revisar los campos de `campos_formulario` frente al formulario."""
    if not re.fullmatch(r"rev_[a-z0-9_]{1,40}", aprobado_por):
        raise ConfigInvalida("aprobado_por debe tener la forma rev_xxx.")
    policy = UrlPolicy.load(settings.allowed_hosts_file)
    with BrowserSession(settings, headless=True) as s:
        s.open_url(policy.check(cfg["url"], autorizado), policy.guard(autorizado))
        analisis = s.inspect()
        alterado: list = []
        if not analisis.safety.blocked:
            # Misma comprobación que en el análisis y con el mismo modo; la fase de clic, solo en los campos del perfil.
            from .listas import MODOS, descubrir
            modo = cfg.get("sondeo") if cfg.get("sondeo") in MODOS else "clic"
            descubrir(s, analisis.all_fields, modo, solo_claves=set(cfg["campos_formulario"].values()))
            alterado = list(s.analisis_alterado)
    if analisis.safety.blocked:
        raise ConfigInvalida("La página exige intervención humana; no se aprueba un perfil sobre ella.")
    if alterado:
        raise ConfigInvalida("La comprobación con clic cambió el valor de un control del formulario; no se aprueba el "
                             "perfil. Analice en modo pasivo y revise el diagnóstico.")
    equiv = cfg.get("equivalencias") or {}
    por_clave = {f.key: f for f in analisis.all_fields}
    for logico, t in equiv.items():
        campo = por_clave.get(cfg["campos_formulario"][logico])
        opciones = {o.value for o in campo.options} if campo else set()
        # Una lista que solo muestra opciones al escribir no se puede comprobar aquí: su texto se verifica al rellenar
        # (solo se acepta una opción idéntica).
        al_escribir = bool(campo) and campo.type == "custom-select" and campo.tag == "input" and not campo.options
        if not campo or (not al_escribir and set(t["valores"].values()) - opciones):
            raise ConfigInvalida(f"La tabla de «{logico}» usa valores que no son opciones del formulario actual.")
    ruta, sha = ProfileStore(settings.profiles_dir).save_integration(
        cfg["perfil"], analisis, cfg["campos_formulario"], formulario_id=cfg["formulario_id"],
        url=cfg["url"].split("?")[0].split("#")[0], aprobado_por_ref=aprobado_por, equivalencias=equiv or None,
        formatos_fecha=cfg.get("formatos_fecha") or None)
    cobertura = {l: {"codigos_cubiertos": sorted(t["valores"]),
                     "codigos_sin_fila": sorted(set(M.ESQUEMA[t["campo_monitor"]]["codigos"] or []) - set(t["valores"]))}
                 for l, t in equiv.items()}
    return {"perfil": cfg["perfil"], "perfil_sha256": sha, "firma": structure_signature_v2(analisis.all_fields),
            "archivo": str(ruta), "aprobado_por": aprobado_por, "equivalencias": cobertura}


def info_perfil(cfg: dict, settings: Settings) -> Optional[dict]:
    """Huella y firma del perfil aprobado ya guardado (None si no existe o no es un perfil de integración)."""
    store = ProfileStore(settings.profiles_dir)
    try:
        path = store.path_of(cfg["perfil"])
        perfil = json.loads(path.read_bytes().decode("utf-8"))
        return {"perfil_sha256": store.sha256_of_file(path), "firma": perfil["structure_signature_v2"],
                "aprobado_por": perfil["integracion"]["aprobado_por_ref"],
                "equivalencias": perfil["integracion"].get("equivalencias") or {},
                "formatos_fecha": perfil["integracion"].get("formatos_fecha") or {}}
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


SALIDA_PARCIAL = 8      # propio de `autorrellenar`: el contrato terminó en 0 pero no todos los campos del perfil quedaron verificados


def resumir_resultado(cfg: dict, inf: dict) -> dict:
    """Cuenta inequívoca: campos del perfil, cuántos quedaron rellenados Y verificados, y cuáles quedan pendientes con
    su motivo. Un campo solo cuenta si el conector lo verificó (y su confirmación, si la tiene, coincide). Sin valores."""
    por_campo = {c["campo"]: c for c in inf.get("campos", [])}
    conf_mal = {c["campo"] for c in inf.get("confirmaciones", []) if not c["coinciden"]}
    motivos = inf.get("motivos", {})
    pendientes = []
    for logico in cfg["campos_formulario"]:
        c = por_campo.get(logico)
        if c and c["correcto"] and logico not in conf_mal:
            continue
        if logico in inf.get("sin_equivalencia", []):
            motivo = "el código de Monitor no está en la tabla de equivalencias aprobada"
        elif logico in inf.get("manual_sin_dato", []):
            motivo = "captura manual sin dato"
        elif logico in inf.get("sin_dato", []):
            motivo = motivos.get(logico, "el expediente no trae dato")
        elif c and not c["correcto"]:
            motivo = f"se intentó pero no quedó verificado ({c['estado']})"
        elif c:
            motivo = "su confirmación no coincide"
        else:
            motivo = "no se rellenó: el proceso se detuvo antes (vea el estado)"
        pendientes.append({"campo": logico, "etiqueta": str((c or {}).get("etiqueta", logico)).rstrip(": "), "motivo": motivo})
    total = len(cfg["campos_formulario"])
    return {"total": total, "verificados": total - len(pendientes), "pendientes": pendientes,
            "completo": total > 0 and not pendientes}


def _cerrar_resultado(cfg: dict, r: dict) -> dict:
    """Añade el resumen y distingue con el código 8 el caso «el contrato dijo 0 pero falta algún campo». El código del
    contrato se conserva en `salida_contrato`; los demás códigos (2…7) no cambian."""
    r["informe"]["resultado"] = resumir_resultado(cfg, r["informe"])
    r["salida_contrato"] = r["salida"]
    if r["salida"] == 0 and not r["informe"]["resultado"]["completo"]:
        r["salida"] = SALIDA_PARCIAL
    return r


def autorrellenar(cfg: dict, expediente: dict, settings: Optional[Settings] = None, *, autorizacion_ref: str = "aut_demo",
                  otorgada_por: str = "rev_demo", espera_revision_s: int = 300, pausa_tecla_ms: int = 40,
                  state_dir: Optional[str | Path] = None, manuales: Optional[dict[str, str]] = None, **kw: Any) -> dict:
    """Ejecuta el recorrido completo. Devuelve {"salida", "salida_contrato", "respuesta", "informe"}; el informe NO contiene valores.
    `salida` es 0 solo si TODOS los campos del perfil quedaron verificados; 8 si el contrato terminó bien pero falta alguno."""
    settings = settings or get_settings()
    info = info_perfil(cfg, settings)
    if info is None:
        return _cerrar_resultado(cfg, {"salida": 6, "respuesta": None, "informe": {"estado": "perfil_no_aprobado", "campos": [], "sin_dato": [],
                "nota": "No hay perfil aprobado: una persona debe revisar y aprobar el perfil primero."}})
    if (cfg.get("equivalencias") or {}) != info["equivalencias"]:
        return _cerrar_resultado(cfg, {"salida": 6, "respuesta": None, "informe": {"estado": "equivalencias_no_aprobadas", "campos": [],
                "sin_dato": [], "nota": "Las tablas de equivalencias del perfil de formulario no son las aprobadas: "
                                        "una persona debe revisarlas y volver a aprobar el perfil."}})
    if (cfg.get("formatos_fecha") or {}) != info.get("formatos_fecha", {}):
        return _cerrar_resultado(cfg, {"salida": 6, "respuesta": None, "informe": {"estado": "formatos_no_aprobados", "campos": [],
                "sin_dato": [], "nota": "Los formatos de fecha del perfil de formulario no son los aprobados: "
                                        "una persona debe revisarlos y volver a aprobar el perfil."}})
    motivos: dict[str, str] = {}
    valores, faltan = extraer_campos(expediente, cfg, manuales, motivos)
    sin_equiv = codigos_sin_equivalencia(expediente, cfg)
    if not valores:
        return _cerrar_resultado(cfg, {"salida": 6, "respuesta": None, "informe": {"estado": "expediente_sin_datos_utiles", "campos": [],
                "sin_dato": faltan, "nota": "El expediente no trae ninguno de los campos del perfil."}})
    sol = construir_solicitud(cfg, info, valores, autorizacion_ref=autorizacion_ref, otorgada_por=otorgada_por,
                              espera_revision_s=espera_revision_s, pausa_tecla_ms=pausa_tecla_ms)
    resp, salida = ejecutar(json.dumps(sol), settings=settings, state_dir=state_dir, **kw)
    manual = set(campos_manuales(cfg))
    inf = informe_legible(resp, cfg, settings, [f for f in faltan if f not in sin_equiv and f not in manual])
    inf["sin_equivalencia"] = sin_equiv
    inf["manual_sin_dato"] = [f for f in faltan if f in manual]
    inf["motivos"] = motivos
    return _cerrar_resultado(cfg, {"salida": salida, "respuesta": resp, "informe": inf})


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
        L.append(f"  [--] {s}: {inf.get('motivos', {}).get(s, 'el expediente no trae dato')}; lo completa la persona")
    for s in inf.get("manual_sin_dato", []):
        L.append(f"  [--] {s}: captura manual sin dato (no se escribió en la ventana); lo completa la persona")
    for s in inf.get("sin_equivalencia", []):
        L.append(f"  [--] {s}: el código de Monitor no está en la tabla aprobada; lo completa la persona")
    if "revision_humana" in inf:
        L.append(f"Revisión humana: {inf['revision_humana']}. El conector no envía (envío por conector: "
                 f"{inf['envio_realizado_por_conector']}).")
    if inf.get("nota"):
        L.append(inf["nota"])
    res = inf.get("resultado")
    if res:
        if res["completo"]:
            L.append(f"RESULTADO: COMPLETO. {res['verificados']} de {res['total']} campos rellenados y verificados.")
        else:
            L.append(f"RESULTADO: INCOMPLETO. {res['verificados']} de {res['total']} campos rellenados y verificados; "
                     + ("queda 1 pendiente:" if len(res["pendientes"]) == 1 else f"quedan {len(res['pendientes'])} pendientes:"))
            L += [f"  PENDIENTE {p['etiqueta']}: {p['motivo']}" for p in res["pendientes"]]
    return "\n".join(L)
