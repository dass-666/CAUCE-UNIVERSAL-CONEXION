"""Construye el diagnóstico de red a partir de la configuración. Nunca impide que Forms arranque."""
from __future__ import annotations

from typing import Any, Callable, Optional

from .cliente_ne import CauceSessionClient, read_secret, storage_method
from .diagnostico import MOTOR_CONFIGURACION_INVALIDA, DiagnosticoRed
from .identidad import IdentidadError, Puesto


def crear_diagnostico(settings: Any, hay_inciertas: Optional[Callable[[], bool]] = None,
                      al_ver_marca: Optional[Callable[[str], None]] = None,
                      al_resolver: Optional[Callable[[str], None]] = None,
                      al_cambiar: Optional[Callable[[dict], None]] = None) -> tuple[DiagnosticoRed, dict]:
    """Devuelve (diagnóstico, info). ``info`` es apta para mostrar: nunca contiene la credencial ni su ruta completa."""
    info: dict[str, Any] = {"configurado": bool(settings.ne_token_file), "proteccion_credencial": None, "error": None}
    try:
        puesto = Puesto(settings.data_dir, settings.puesto or None)
    except IdentidadError as e:
        d = DiagnosticoRed(None, None)
        if settings.ne_token_file:
            d._fijar(motor=MOTOR_CONFIGURACION_INVALIDA, codigo="etiqueta_puesto_invalida")
        info["error"] = str(e)
        return d, info
    kw = dict(hay_inciertas=hay_inciertas, al_ver_marca=al_ver_marca, al_resolver=al_resolver, al_cambiar=al_cambiar)
    if not settings.ne_token_file:
        return DiagnosticoRed(None, puesto, **kw), info
    info["proteccion_credencial"] = storage_method(settings.ne_token_file)
    try:
        cliente = CauceSessionClient(read_secret(settings.ne_token_file), port=settings.ne_port)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        d = DiagnosticoRed(None, puesto, **kw)
        d._fijar(motor=MOTOR_CONFIGURACION_INVALIDA, codigo="credencial_ilegible")
        info["error"] = type(e).__name__
        return d, info
    d = DiagnosticoRed(cliente, puesto, conexion=settings.ne_connection_id or None,
                       intervalo_s=settings.ne_heartbeat_s, **kw)
    return d, info
