"""Motor de escritura secuencial.

Regla del proyecto: los campos de texto se rellenan CARÁCTER POR CARÁCTER con
eventos de teclado (keydown / keypress / input / keyup por cada carácter) usando
`keyboard.press` (un carácter cada vez). Nunca se asigna `element.value` ni se usa el
portapapeles.

Caracteres fuera de la distribución de teclado de EE. UU. (ñ, á, é, ü, ¿...):
Playwright los inserta con `insertText`, que dispara `input` pero NO `keydown`/
`keyup`. Para que también generen eventos de teclado completos, en Chromium se
envían con `Input.dispatchKeyEvent` del protocolo DevTools (CDP), igual que lo
haría un teclado en español. Si CDP no está disponible (Firefox/WebKit) se usa
`insertText` carácter a carácter y se indica en el resultado.

Controles que no reciben texto usan su interacción normal:
- select   -> `select_option` (equivale a elegir la opción en la lista)
- checkbox -> `check` / `uncheck` (clic)
- radio    -> `check` sobre la opción elegida (clic)
- date/time/color/range -> `fill`, que es el mecanismo de Playwright para estos
  controles nativos (el usuario los usaría con su selector de calendario).

Seguridad: en `<input>` los saltos de línea se convierten en espacios, porque
pulsar Enter dentro de un input puede ENVIAR el formulario.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from .form_detector import strip_accents
from .models import CHOICE_TYPES, DATE_TYPES, FieldDescriptor, FieldOption

TRUE_WORDS = {"1", "true", "si", "sí", "s", "yes", "y", "x", "on", "acepto", "verdadero", "ok"}
FALSE_WORDS = {"0", "false", "no", "n", "off", "", "falso"}


class FillError(Exception):
    pass


def to_bool(value: str) -> Optional[bool]:
    v = strip_accents(str(value)).strip().lower()
    if v in {strip_accents(w) for w in TRUE_WORDS}:
        return True
    if v in FALSE_WORDS:
        return False
    return None


def normalize_date(value: str) -> str:
    """Convierte fechas habituales a ISO (AAAA-MM-DD), el formato de <input type=date>."""
    v = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    raise FillError(f"Fecha no reconocida (use AAAA-MM-DD o DD/MM/AAAA)")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(s or "").lower()).strip()


def choose_option(options: list[FieldOption], value: str) -> Optional[FieldOption]:
    """Elige la opción por valor exacto, luego por texto y por último por coincidencia parcial."""
    nv = _norm(value)
    if not nv:
        return None
    for o in options:
        if o.value == value:
            return o
    for o in options:
        if _norm(o.value) == nv or _norm(o.label) == nv:
            return o
    partial = [o for o in options if o.value and (nv in _norm(o.label) or _norm(o.label) in nv) and _norm(o.label)]
    return partial[0] if len(partial) == 1 else None


def is_us_layout_char(ch: str) -> bool:
    """Caracteres que Playwright sabe teclear con keydown/keyup reales."""
    return ch == "\n" or (ch.isascii() and ch.isprintable())


class SequentialTyper:
    def __init__(self, page: Any, delay_ms: int = 35):
        self.page = page
        self.delay_ms = max(0, int(delay_ms))
        self._cdp = None
        self._cdp_failed = False
        self.last_insert_text_fallbacks = 0

    def _cdp_session(self):
        if self._cdp is None and not self._cdp_failed:
            try:
                self._cdp = self.page.context.new_cdp_session(self.page)
            except Exception:
                self._cdp_failed = True
        return self._cdp

    def _press_char(self, ch: str) -> None:
        kb = self.page.keyboard
        if is_us_layout_char(ch):
            kb.press("Enter" if ch == "\n" else ch)
            return
        cdp = self._cdp_session()
        if cdp is not None:
            cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": ch, "text": ch, "unmodifiedText": ch})
            cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
        else:
            kb.insert_text(ch)  # solo evento input; se informa en el resultado
            self.last_insert_text_fallbacks += 1

    def locator(self, f: FieldDescriptor):
        return self.page.locator(f.selector).first

    def type_text(self, f: FieldDescriptor, value: str) -> int:
        """Escribe `value` tecla a tecla y sale del campo con Tab. Devuelve el nº de caracteres enviados."""
        n = self.type_chars(f, value)
        self.leave_field(f)
        return n

    def leave_field(self, f: FieldDescriptor) -> None:
        # Salir del campo con Tab dispara blur/change como haría una persona
        self.locator(f).press("Tab")

    def type_chars(self, f: FieldDescriptor, value: str) -> int:
        """Foco, limpieza con teclado y escritura carácter a carácter (sin salir del campo)."""
        loc = self.locator(f)
        if f.tag != "textarea":
            value = re.sub(r"[\r\n]+", " ", value)
        loc.scroll_into_view_if_needed()
        loc.click()
        # Limpiar contenido previo con teclado (seleccionar todo + borrar), no con .value = ""
        if loc.input_value() != "":
            loc.press("ControlOrMeta+a")
            loc.press("Backspace")
        self.last_insert_text_fallbacks = 0
        for i, ch in enumerate(value):
            self._press_char(ch)
            if self.delay_ms and i < len(value) - 1:
                self.page.wait_for_timeout(self.delay_ms)
        return len(value)

    def set_choice(self, f: FieldDescriptor, value: str) -> tuple[str, str]:
        """Aplica select/checkbox/radio. Devuelve (método, valor_aplicado)."""
        if f.type in ("select", "select-multiple"):
            values = [v.strip() for v in value.split("|")] if f.type == "select-multiple" else [value]
            chosen = []
            for v in values:
                opt = choose_option(f.options, v)
                if opt is None:
                    raise FillError("Ninguna opción del desplegable coincide con el dato")
                chosen.append(opt.value)
            self.locator(f).select_option(chosen if f.type == "select-multiple" else chosen[0])
            return "select_option", "|".join(chosen)
        if f.type == "checkbox":
            b = to_bool(value)
            if b is None:
                raise FillError("Valor no interpretable como sí/no para una casilla")
            loc = self.locator(f)
            loc.check() if b else loc.uncheck()
            return ("check" if b else "uncheck"), ("true" if b else "false")
        if f.type == "radio":
            opt = choose_option(f.options, value)
            if opt is None:
                raise FillError("Ninguna opción del grupo coincide con el dato")
            self.page.locator(f'{f.selector}[value="{opt.value}"]').first.check()
            return "check", opt.value
        raise FillError(f"Tipo no compatible con set_choice: {f.type}")

    def set_native(self, f: FieldDescriptor, value: str) -> str:
        v = normalize_date(value) if f.type == "date" else value.strip()
        self.locator(f).fill(v)
        return v

    def apply(self, f: FieldDescriptor, value: str) -> dict[str, Any]:
        if f.is_text and f.widget == "combo":
            from .combo import fill_combo
            return fill_combo(self, f, re.sub(r"[\r\n]+", " ", value))
        if f.is_text:
            n = self.type_text(f, value)
            applied = value if f.tag == "textarea" else re.sub(r"[\r\n]+", " ", value)
            return {"method": "teclado_secuencial", "keystrokes": n, "applied": applied,
                    "insert_text_fallbacks": self.last_insert_text_fallbacks}
        if f.type in CHOICE_TYPES:
            method, applied = self.set_choice(f, value)
            return {"method": method, "keystrokes": 0, "applied": applied}
        if f.type in DATE_TYPES:
            applied = self.set_native(f, value)
            return {"method": "fill_nativo", "keystrokes": 0, "applied": applied}
        raise FillError(f"Tipo de campo no automatizable: {f.type}")
