"""Modelos de datos compartidos por todos los componentes.

Se usan dataclasses simples (sin dependencias) para que el contrato sea fácil
de serializar a JSON y de consumir desde otros sistemas (p. ej. Cauce Monitor).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# Tipos de control que reciben texto y que, por regla del proyecto, se escriben
# carácter por carácter mediante eventos de teclado.
TEXT_TYPES = {
    "text", "email", "tel", "password", "search", "url", "number", "textarea", "",
}
# Controles que NO reciben texto y usan su mecanismo normal de interacción.
CHOICE_TYPES = {"select", "select-multiple", "checkbox", "radio"}
DATE_TYPES = {"date", "datetime-local", "month", "week", "time", "color", "range"}
SKIP_TYPES = {"hidden", "submit", "button", "reset", "image", "file"}


@dataclass
class FieldOption:
    value: str
    label: str
    selected: bool = False


@dataclass
class FieldDescriptor:
    """Descripción de un campo detectado en un formulario."""

    key: str                      # identificador estable dentro del formulario
    selector: str                 # selector CSS para localizarlo
    tag: str                      # input | select | textarea
    type: str                     # text, email, select, checkbox, radio...
    name: str = ""
    id: str = ""
    label: str = ""
    placeholder: str = ""
    aria_label: str = ""
    autocomplete: str = ""
    required: bool = False
    disabled: bool = False
    readonly: bool = False
    visible: bool = True
    pattern: str = ""
    minlength: Optional[int] = None
    maxlength: Optional[int] = None
    min: str = ""
    max: str = ""
    step: str = ""
    inputmode: str = ""
    options: list[FieldOption] = field(default_factory=list)
    form_key: str = ""
    confirms: str = ""            # key del campo que este campo confirma (email2 -> email)
    blocks_paste: bool = False    # el campo tiene onpaste (solo informativo)

    @property
    def is_text(self) -> bool:
        return self.type in TEXT_TYPES

    @property
    def fillable(self) -> bool:
        return self.type not in SKIP_TYPES and not self.disabled and not self.readonly

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FieldDescriptor":
        d = dict(d)
        d["options"] = [FieldOption(**o) for o in d.get("options", [])]
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class FormDescriptor:
    key: str
    selector: str
    name: str = ""
    id: str = ""
    action: str = ""
    method: str = "get"
    fields: list[FieldDescriptor] = field(default_factory=list)
    submit_buttons: list[str] = field(default_factory=list)  # textos de botones de envío

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "selector": self.selector, "name": self.name, "id": self.id,
            "action": self.action, "method": self.method,
            "fields": [f.to_dict() for f in self.fields],
            "submit_buttons": list(self.submit_buttons),
        }


@dataclass
class SafetyReport:
    """Señales de que la página exige intervención humana."""

    blocked: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisResult:
    source: str                    # "html" | "url"
    source_label: str              # host+ruta o huella del HTML, nunca datos personales
    forms: list[FormDescriptor] = field(default_factory=list)
    safety: SafetyReport = field(default_factory=SafetyReport)
    engine: str = "static"         # static | browser
    warnings: list[str] = field(default_factory=list)

    @property
    def all_fields(self) -> list[FieldDescriptor]:
        return [f for form in self.forms for f in form.fields]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "source_label": self.source_label, "engine": self.engine,
            "forms": [f.to_dict() for f in self.forms], "safety": self.safety.to_dict(),
            "warnings": list(self.warnings),
        }


@dataclass
class Mapping:
    """Correspondencia propuesta entre un campo y una columna de datos."""

    field_key: str
    column: Optional[str]
    confidence: float
    reason: str
    status: str = "auto"   # auto | revisar | manual | sin_datos | ignorado

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FieldResult:
    field_key: str
    label: str
    type: str
    method: str              # teclado_secuencial | select_option | check | uncheck | fill_nativo | omitido
    status: str              # ok | ok_normalizado | diferente | error | omitido | invalido
    expected: str = ""
    actual: str = ""
    keystrokes: int = 0      # caracteres enviados uno a uno
    valid_html5: Optional[bool] = None
    validation_message: str = ""
    page_error: str = ""     # mensaje de error visible que la página asoció al campo
    detail: str = ""
    discovered_late: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FillReport:
    ok: bool
    stopped_before_submit: bool
    results: list[FieldResult] = field(default_factory=list)
    confirmations: list[dict[str, Any]] = field(default_factory=list)
    submit_buttons: list[str] = field(default_factory=list)
    blocked: bool = False
    blocked_reasons: list[str] = field(default_factory=list)
    duration_ms: int = 0
    screenshot: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "stopped_before_submit": self.stopped_before_submit,
            "results": [r.to_dict() for r in self.results],
            "confirmations": self.confirmations, "submit_buttons": self.submit_buttons,
            "blocked": self.blocked, "blocked_reasons": self.blocked_reasons,
            "duration_ms": self.duration_ms, "screenshot": self.screenshot,
        }
