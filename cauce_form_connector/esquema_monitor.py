"""Modelo de datos del `Expediente` de Cauce Monitor, tal como lo recuperó la auditoría de solo lectura (23/11).

Es PLANO: no existen `titular.*`, `contacto.*`, CURP ni RFC. Este módulo:
- describe cada campo (tipo, si es identificador o código, si puede proponerse automáticamente);
- reconoce qué campo de Monitor pide un control de formulario, con reglas explícitas y conservadoras;
- valida que un expediente (sintético) siga ese modelo.

Reglas que NO se relajan aquí:
- No se divide un nombre completo en nombre/apellidos: esa correspondencia queda «revisar».
- CURP, RFC, documentos y consentimientos no se proponen: Monitor no tiene esos datos y su autorización la verifica
  el propio flujo de Monitor. `id_externo` no se asume equivalente a ningún identificador del formulario.
- Los códigos (`tipo_tramite`, `operacion`, `region`, `sucursal`, `modo`) nunca se traducen solos: hace falta una
  tabla de equivalencias aprobada por una persona, aunque los textos parezcan coincidir.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .form_detector import strip_accents
from .matcher import field_concept
from .models import FieldDescriptor

# tipo: texto | fecha | telefono | email | identificador | codigo
ESQUEMA: dict[str, dict[str, Any]] = {
    "nombre": {"tipo": "texto", "descripcion": "nombre completo", "auto": True},
    "fecha_nacimiento": {"tipo": "fecha", "descripcion": "AAAA-MM-DD", "auto": True},
    "telefono": {"tipo": "telefono", "descripcion": "10 dígitos normalizados", "auto": True},
    "correo": {"tipo": "email", "descripcion": "correo normalizado", "auto": True},
    "id_externo": {"tipo": "identificador", "descripcion": "identificador opcional y PERSONAL", "auto": False,
                   "opcional": True, "personal": True},
    "id_registro_previo": {"tipo": "identificador", "descripcion": "identificador opcional", "auto": False, "opcional": True},
    "folio_anterior": {"tipo": "identificador", "descripcion": "cadena opcional", "auto": False, "opcional": True},
    "tipo_tramite": {"tipo": "codigo", "codigos": ["alta", "renovacion", "constancia"], "auto": False},
    "operacion": {"tipo": "codigo", "codigos": ["nueva", "reprogramar"], "auto": False},
    "region": {"tipo": "codigo", "codigos": None, "descripcion": "código de región (catálogo no disponible)", "auto": False},
    "sucursal": {"tipo": "codigo", "codigos": None, "descripcion": "código de sucursal (catálogo no disponible)", "auto": False},
    "fecha_solicitada": {"tipo": "fecha", "descripcion": "opcional", "auto": False, "opcional": True},
    "fechas_desde": {"tipo": "fecha", "descripcion": "opcional", "auto": False, "opcional": True},
    "fechas_hasta": {"tipo": "fecha", "descripcion": "opcional", "auto": False, "opcional": True},
    "modo": {"tipo": "codigo", "codigos": ["POR_DIA", "POR_HORARIO"], "auto": False},
}
CAMPOS = list(ESQUEMA)

# (regex sobre etiqueta+name+id+placeholder normalizados, campo de Monitor o None, decisión, motivo). En orden.
_REGLAS: list[tuple[str, Optional[str], str, str]] = [
    (r"\bcurp\b|\brfc\b|\bdni\b|\bnif\b|\bnie\b|pasaporte|cedula|documento|identificacion|\bine\b|\bnss\b",
     None, "humano", "documento de identidad: Monitor no tiene CURP/RFC y no se asume que id_externo sea equivalente"),
    (r"acept|consent|privacidad|terminos|condiciones|autorizo|boletin|newsletter|suscrib",
     None, "humano", "consentimiento o autorización: lo verifica el flujo de Monitor; el analizador no lo marca"),
    (r"contrasen|password", None, "humano", "credenciales: nunca se rellenan automáticamente"),
    (r"\bfolio\b", "folio_anterior", "revisar", "folio: confirmar que es el mismo folio que guarda Monitor (no se suponen "
                                                "equivalencias entre sistemas)"),
    (r"registro previo|registro anterior|numero de registro|\bid registro\b", "id_registro_previo", "revisar",
     "identificador de otro sistema: confirmar la equivalencia"),
    (r"tramite|tipo de servicio|servicio solicitado", "tipo_tramite", "revisar", "código"),
    (r"operacion|reprogram|tipo de cita", "operacion", "revisar", "código"),
    (r"sucursal|oficina|modulo|\bsede\b", "sucursal", "revisar", "código"),
    (r"\bregion\b|\bzona\b", "region", "revisar", "código"),
    (r"\bmodo\b|por dia|por horario", "modo", "revisar", "código"),
]
_FECHA_DESDE = re.compile(r"desde|inicio|a partir")
_FECHA_HASTA = re.compile(r"hasta|\bfin\b|limite")
_FECHA_CITA = re.compile(r"cita|solicitad|preferid|reserva|turno")


def _texto(f: FieldDescriptor) -> str:
    t = " ".join([f.label, f.name, f.id, f.placeholder, f.aria_label])
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", t)
    t = strip_accents(t).lower()
    return re.sub(r"\s+", " ", re.sub(r"[_\-.\[\]:*()¿?/]+", " ", t)).strip()


def reconocer(f: FieldDescriptor, campos_formulario: list[FieldDescriptor]) -> dict[str, Any]:
    """¿Qué campo de Monitor pide este control? Devuelve {campo, decision_maxima, motivo, evidencia}.
    `decision_maxima` es lo más que puede llegar a ser (automatico/revisar/humano); luego se comprueba el dato."""
    t = _texto(f)
    exacto = next((c for c in CAMPOS if c in (f.name, f.id)), None)

    for patron, campo, decision, motivo in _REGLAS:
        if re.search(patron, t):
            if campo and ESQUEMA[campo]["tipo"] == "codigo":
                return _codigo(campo, f)
            return {"campo": campo, "decision_maxima": decision, "motivo": motivo, "evidencia": f"texto «{t[:60]}»"}

    concepto, por = field_concept(f)
    if f.type in ("date", "datetime-local") or concepto in ("fecha", "fecha_nacimiento") or exacto in (
            "fecha_nacimiento", "fecha_solicitada", "fechas_desde", "fechas_hasta"):
        if concepto == "fecha_nacimiento" or exacto == "fecha_nacimiento":
            return {"campo": "fecha_nacimiento", "decision_maxima": "automatico", "motivo": "fecha de nacimiento",
                    "evidencia": por or "name"}
        if _FECHA_DESDE.search(t) or exacto == "fechas_desde":
            return {"campo": "fechas_desde", "decision_maxima": "revisar", "evidencia": por or "texto",
                    "motivo": "inicio de un rango de fechas: depende del modo (POR_DIA/POR_HORARIO); confirmar"}
        if _FECHA_HASTA.search(t) or exacto == "fechas_hasta":
            return {"campo": "fechas_hasta", "decision_maxima": "revisar", "evidencia": por or "texto",
                    "motivo": "fin de un rango de fechas: depende del modo (POR_DIA/POR_HORARIO); confirmar"}
        if _FECHA_CITA.search(t) or exacto == "fecha_solicitada":
            return {"campo": "fecha_solicitada", "decision_maxima": "revisar", "evidencia": por or "texto",
                    "motivo": "fecha de cita: confirmar que corresponde a fecha_solicitada de Monitor"}
        return {"campo": None, "decision_maxima": "humano", "evidencia": por or "tipo",
                "motivo": "fecha sin equivalente claro en Monitor"}

    if concepto == "email" or exacto == "correo":
        return {"campo": "correo", "decision_maxima": "automatico", "motivo": "correo", "evidencia": por or "name"}
    if concepto == "telefono" or exacto == "telefono":
        return {"campo": "telefono", "decision_maxima": "automatico", "motivo": "teléfono", "evidencia": por or "name"}

    hay_apellidos = any(field_concept(g)[0] in ("apellido", "apellido2") for g in campos_formulario if g.form_key == f.form_key)
    if concepto in ("apellido", "apellido2"):
        return {"campo": "nombre", "decision_maxima": "revisar", "evidencia": por,
                "motivo": "el formulario separa nombre y apellidos y Monitor solo guarda el nombre completo: no se divide "
                          "automáticamente"}
    if concepto == "nombre_completo":
        return {"campo": "nombre", "decision_maxima": "automatico", "motivo": "nombre completo", "evidencia": por}
    if concepto == "nombre" or exacto == "nombre":
        if hay_apellidos:
            return {"campo": "nombre", "decision_maxima": "revisar", "evidencia": por or "name",
                    "motivo": "el formulario separa nombre y apellidos y Monitor solo guarda el nombre completo: no se "
                              "divide automáticamente"}
        return {"campo": "nombre", "decision_maxima": "revisar", "evidencia": por or "name",
                "motivo": "«nombre» sin «completo»: confirmar que el formulario pide el nombre completo"}
    return {"campo": None, "decision_maxima": "humano", "evidencia": por or "",
            "motivo": "Monitor no tiene un dato equivalente"}


def _codigo(campo: str, f: FieldDescriptor) -> dict[str, Any]:
    codigos = ESQUEMA[campo]["codigos"]
    valores = {o.value for o in f.options if o.value}
    if codigos is None:
        nota = "catálogo de códigos de Monitor no disponible"
    elif valores and set(codigos) <= valores:
        nota = "los valores coinciden literalmente, pero el significado debe confirmarse"
    else:
        nota = "las opciones del formulario no coinciden con los códigos de Monitor"
    return {"campo": campo, "decision_maxima": "revisar", "evidencia": "texto",
            "motivo": f"código de Monitor ({campo}): requiere tabla de equivalencias aprobada; {nota}",
            "requiere_tabla": True}


def validar_expediente(exp: Any) -> list[str]:
    """Avisos si el expediente no sigue el modelo plano de Monitor. No lanza: el informe los muestra."""
    if not isinstance(exp, dict):
        return ["El expediente no es un objeto JSON."]
    avisos = []
    extra = sorted(k for k in exp if k not in ESQUEMA and not k.startswith("_"))
    if extra:
        avisos.append(f"El expediente trae claves que Monitor no tiene y se ignoran: {', '.join(extra)}.")
    faltan = sorted(k for k in ESQUEMA if k not in exp and not ESQUEMA[k].get("opcional"))
    if faltan:
        avisos.append(f"Al expediente le faltan campos obligatorios del modelo de Monitor: {', '.join(faltan)}.")
    for k, spec in ESQUEMA.items():
        v = exp.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, (dict, list)):
            avisos.append(f"«{k}» debe ser un valor plano, no un objeto o lista.")
        elif spec["tipo"] == "fecha" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)):
            avisos.append(f"«{k}» no tiene formato AAAA-MM-DD.")
        elif spec["tipo"] == "telefono" and not re.fullmatch(r"\d{10}", str(v)):
            avisos.append(f"«{k}» no son 10 dígitos normalizados.")
        elif spec["tipo"] == "codigo" and spec["codigos"] and v not in spec["codigos"]:
            avisos.append(f"«{k}» no es un código válido ({', '.join(spec['codigos'])}).")
    return avisos
