"""Modo `cauce.conector.v1`: flujo, fallos seguros y cumplimiento del contrato (formularios y datos ficticios)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import cauce_form_connector.integracion as I
from cauce_form_connector import contrato_v1 as C
from cauce_form_connector.config_store import ProfileStore
from cauce_form_connector.engine import submit_with_human_confirmation

from integ_helpers import (CAMPOS, LAB, VALORES, Espia, Sitio, aprobar, fabrica_espia, fabrica_prohibida, solicitud,
                           settings_para)

pytestmark = pytest.mark.browser
HTML01 = (LAB / "01_tradicional.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sitio():
    s = Sitio()
    s.paginas["/f.html"] = HTML01
    s.paginas["/captcha.html"] = (LAB / "04_captcha_simulado.html").read_text(encoding="utf-8")
    yield s
    s.cerrar()


@pytest.fixture()
def ent(tmp_path, sitio):
    sitio.paginas["/f.html"] = HTML01
    sitio.redirecciones.clear()
    st = settings_para(tmp_path)
    info = aprobar(st, sitio.base + "/f.html")
    return {"s": st, "info": info, "state": tmp_path / "estado", "tmp": tmp_path, "sitio": sitio}


def correr(ent, sol, **kw):
    kw.setdefault("fabrica_sesion", fabrica_espia)
    resp, salida = I.ejecutar(json.dumps(sol), settings=ent["s"], state_dir=ent["state"], **kw)
    if resp is not None:        # Monitor aplicará exactamente estas reglas a lo que reciba
        assert C.validar_respuesta(resp, sol, salida) == [], resp
        assert C.SALIDA[resp["estado"]] == salida
    return resp, salida


def sin_valores(*objs):
    texto = "".join(json.dumps(o, ensure_ascii=False) if not isinstance(o, str) else o for o in objs)
    for v in VALORES.values():
        if len(v) >= 3:
            assert v not in texto
    assert "zz" not in texto.lower()


def humano_cierra(s):
    s.page.wait_for_timeout(300)
    s.vistos = s.page.evaluate("Object.fromEntries([...document.querySelectorAll('input,textarea,select')].map(e => [e.name||e.id, e.value]))")
    s.vistos_teclas = s.page.evaluate("window.__keylog || {}")
    s.page.close()


# ---------------------------------------------------------------- camino feliz y revisión humana
def test_preparado_y_cerrado_sin_envio_escribe_lo_aprobado(ent):
    resp, salida = correr(ent, solicitud(ent["info"], espera=20), gancho_revision=humano_cierra)
    assert (resp["estado"], salida) == ("preparado_revisado_sin_envio", 0)
    assert resp["revision_humana"]["resultado"] == "cerrada_sin_envio"
    assert resp["envio"] == {"realizado_por_conector": False, "envio_humano_observado": False}
    assert resp["formulario"]["estructura_coincide"] and not resp["errores"]
    assert {c["campo"] for c in resp["campos"]} == set(VALORES)
    esp = Espia.ultimo
    assert esp.vistos["nombre"] == VALORES["nombre"] and esp.vistos["email"] == VALORES["email"]
    assert esp.vistos["fecha_nacimiento"] == "" and esp.vistos["mensaje"] == ""      # lo no aprobado no se toca
    assert esp.vistos_teclas                                                         # hubo eventos de teclado reales
    sin_valores(resp)


def test_envio_humano_se_observa_pero_no_es_confirmacion(ent, monkeypatch):
    monkeypatch.setattr(I, "GRACIA_ACTIVACION_S", 0.5)

    def humano_envia(s):
        s.page.wait_for_timeout(800)
        s.page.locator("button[type=submit]").first.click()          # clic real (CDP), como una persona

    resp, salida = correr(ent, solicitud(ent["info"], espera=20), gancho_revision=humano_envia)
    assert (resp["estado"], salida) == ("enviado_por_persona_observado", 0)
    assert resp["envio"] == {"realizado_por_conector": False, "envio_humano_observado": True}
    assert resp["revision_humana"]["resultado"] == "envio_humano"


def test_submit_forzado_por_la_pagina_no_cuenta_como_persona(ent):
    """requestSubmit() desde un script (dentro de la ventana de activación y después de ella) no es envío humano."""
    pasos = {"n": 0}

    def pagina_maliciosa(s):
        if pasos["n"] == 0:      # programa dos envíos desde el propio script de la página (sin gesto de usuario propio)
            s.page.evaluate("""() => { const f = document.querySelector('form');
                setTimeout(() => f.requestSubmit(), 100); setTimeout(() => f.requestSubmit(), 6500); }""")
        pasos["n"] += 1

    resp, salida = correr(ent, solicitud(ent["info"], espera=8), gancho_revision=pagina_maliciosa)
    assert resp["estado"] == "revision_caducada" and salida == 7
    assert resp["envio"]["envio_humano_observado"] is False


def test_espera_cero_es_revision_caducada_sin_esperar(ent):
    resp, salida = correr(ent, solicitud(ent["info"], espera=0))
    assert (resp["estado"], salida) == ("revision_caducada", 7)
    assert resp["revision_humana"] == {"requerida": True, "resultado": "caducada", "duracion_s": 0}


def test_revision_que_vence_devuelve_caducada(ent):
    resp, salida = correr(ent, solicitud(ent["info"], espera=2))
    assert (resp["estado"], salida) == ("revision_caducada", 7)
    assert resp["revision_humana"]["duracion_s"] == 2 and resp["errores"] == [{"codigo": "REVISION_CADUCADA"}]


def test_el_conector_no_puede_enviar_en_modo_integracion(ent):
    visto = {}

    def intenta(s):
        try:
            submit_with_human_confirmation(s, "ENVIAR")
        except PermissionError:
            visto["bloqueado"] = True
        s.page.close()

    resp, _ = correr(ent, solicitud(ent["info"], espera=20), gancho_revision=intenta)
    assert visto == {"bloqueado": True} and resp["envio"]["realizado_por_conector"] is False
    assert Espia.ultimo.submit_events == []


def test_incidencias_dejan_la_ventana_abierta_hasta_que_la_persona_cierra(ent):
    """Con CAMPO_NO_VERIFICADO la ventana NO se cierra sola: se espera a que la persona revise y cierre."""
    sol = solicitud(ent["info"], espera=60, valores={**VALORES, "email": "zz-no-es-correo"})
    visto = {}

    def persona_cierra(s):
        visto["valor_intacto"] = s.page.locator("#email").input_value() == "zz-no-es-correo"   # no se borró nada
        s.page.close()

    resp, salida = correr(ent, sol, gancho_revision=persona_cierra)
    assert (resp["estado"], salida) == ("preparado_con_incidencias", 2)
    assert visto == {"valor_intacto": True}
    assert resp["revision_humana"]["resultado"] == "cerrada_sin_envio" and resp["envio"]["realizado_por_conector"] is False
    assert {"campo": "email", "estado": "invalido"} .items() <= next(c for c in resp["campos"] if c["campo"] == "email").items()
    sin_valores(resp)


# ---------------------------------------------------------------- idempotencia y caducidad
def test_solicitud_repetida_se_rechaza_sin_abrir_navegador(ent):
    sol = solicitud(ent["info"], espera=0)
    r1, s1 = correr(ent, sol)
    assert s1 == 7
    r2, s2 = correr(ent, sol, fabrica_sesion=fabrica_prohibida)
    assert (r2["estado"], s2) == ("rechazado_solicitud_invalida", 6)
    assert r2["errores"] == [{"codigo": "SOLICITUD_REPETIDA"}] and r2["campos"] == [] and r2["huella_preparacion"] == ""


def test_el_id_se_consume_aunque_el_perfil_sea_rechazado(ent):
    sol = solicitud(ent["info"], espera=0)
    sol["formulario"]["perfil_sha256"] = "0" * 64
    assert correr(ent, sol, fabrica_sesion=fabrica_prohibida)[1] == 3
    assert correr(ent, sol, fabrica_sesion=fabrica_prohibida)[0]["errores"] == [{"codigo": "SOLICITUD_REPETIDA"}]


def test_solicitud_caducada(ent):
    ahora = datetime.now(timezone.utc)
    sol = solicitud(ent["info"], ahora=ahora - timedelta(seconds=300))     # caduca_en = hace ~5 min - 595 s... ya pasó
    sol["caduca_en"] = (ahora - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp, salida = correr(ent, sol, fabrica_sesion=fabrica_prohibida)
    assert (resp["estado"], salida) == ("rechazado_solicitud_invalida", 6)
    assert resp["errores"] == [{"codigo": "SOLICITUD_CADUCADA"}]


def test_autorizacion_vencida(ent):
    sol = solicitud(ent["info"])
    sol["autorizacion"]["vigente_hasta"] = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    resp, salida = correr(ent, sol, fabrica_sesion=fabrica_prohibida)
    assert (resp["estado"], salida) == ("rechazado_no_autorizado", 3) and resp["errores"] == [{"codigo": "AUTORIZACION_CADUCADA"}]


def test_formulario_no_autorizado_por_id_o_por_dominio(ent):
    sol = solicitud(ent["info"])
    sol["autorizacion"]["formulario_id"] = "frm_otro"
    assert correr(ent, sol, fabrica_sesion=fabrica_prohibida)[0]["errores"] == [{"codigo": "FORMULARIO_NO_AUTORIZADO"}]
    sol = solicitud(ent["info"])
    sol["formulario"]["url"] = "https://sitio-externo.example/f.html"      # no está en allowed_hosts.json
    resp, salida = correr(ent, sol, fabrica_sesion=fabrica_prohibida)
    assert salida == 3 and resp["errores"] == [{"codigo": "FORMULARIO_NO_AUTORIZADO"}]


def test_solicitud_ilegible_o_con_campos_extra_solo_devuelve_codigo_6(ent):
    for malo in ("{no es json", json.dumps({"contrato": "otro"}), json.dumps({**solicitud(ent["info"]), "extra": 1}), "[]"):
        resp, salida = I.ejecutar(malo, settings=ent["s"], state_dir=ent["state"], fabrica_sesion=fabrica_prohibida)
        assert resp is None and salida == 6
    assert not (ent["state"] / "solicitudes_usadas").exists()        # nada se consumió


# ---------------------------------------------------------------- perfil (H3) y estructura (H4)
def test_perfil_alterado_un_byte(ent):
    ruta = ent["info"]["ruta"]
    ruta.write_bytes(ruta.read_bytes() + b" ")
    resp, salida = correr(ent, solicitud(ent["info"]), fabrica_sesion=fabrica_prohibida)
    assert (resp["estado"], salida) == ("rechazado_no_autorizado", 3) and resp["errores"] == [{"codigo": "PERFIL_NO_COINCIDE"}]


def test_perfil_antiguo_sin_aprobar_se_rechaza(ent):
    from cauce_form_connector.html_analyzer import analyze_html
    store = ProfileStore(ent["s"].profiles_dir)
    ruta = store.save("viejo", analyze_html(HTML01), {"registro::nombre": "Nombre"})
    assert store.load("viejo")["version"] == 1             # sigue siendo legible (compatibilidad de lectura)
    info = {**ent["info"], "perfil": "viejo", "sha": ProfileStore.sha256_of_file(ruta)}
    resp, salida = correr(ent, solicitud(info), fabrica_sesion=fabrica_prohibida)
    assert salida == 3 and resp["errores"] == [{"codigo": "PERFIL_NO_COINCIDE"}]


@pytest.mark.parametrize("cambio", [("formulario_id", "frm_otro"), ("url", "http://127.0.0.1:1/otra.html")])
def test_perfil_de_otro_formulario_o_url(ent, cambio):
    ruta = ent["info"]["ruta"]
    d = json.loads(ruta.read_text(encoding="utf-8"))
    d["integracion"][cambio[0]] = cambio[1]
    ruta.write_text(json.dumps(d), encoding="utf-8")          # un perfil editado Y re-huellado sigue sin servir
    info = {**ent["info"], "sha": ProfileStore.sha256_of_file(ruta)}
    resp, salida = correr(ent, solicitud(info), fabrica_sesion=fabrica_prohibida)
    assert salida == 3 and resp["errores"] == [{"codigo": "PERFIL_NO_COINCIDE"}]


def test_firma_esperada_que_no_es_la_del_perfil(ent):
    info = {**ent["info"], "firma": "v2:0000000000000000"}
    resp, salida = correr(ent, solicitud(info), fabrica_sesion=fabrica_prohibida)
    assert salida == 3 and resp["errores"] == [{"codigo": "PERFIL_NO_COINCIDE"}]


def test_campo_no_declarado_en_el_perfil(ent):
    sol = solicitud(ent["info"], valores={**VALORES, "mensaje": "zz hola"})
    resp, salida = correr(ent, sol, fabrica_sesion=fabrica_prohibida)
    assert (resp["estado"], salida) == ("rechazado_solicitud_invalida", 6) and resp["errores"] == [{"codigo": "CAMPO_NO_DECLARADO"}]


def test_cambio_de_etiqueta_detiene_sin_escribir_nada(ent):
    ent["sitio"].paginas["/f.html"] = HTML01.replace("Correo electrónico", "Número de documento")
    resp, salida = correr(ent, solicitud(ent["info"], espera=20))
    assert (resp["estado"], salida) == ("detenido_estructura_cambiada", 5)
    assert resp["formulario"]["estructura_coincide"] is False and resp["formulario"]["firma_estructura"] != ent["info"]["firma"]
    assert resp["formulario"]["cambios"]["modificados"] == [{"campo": "registro::email", "cambio": "etiqueta"}]
    assert resp["campos"] == [] and resp["huella_preparacion"] == ""
    esp = Espia.ultimo
    assert not any(esp.teclas.values()) and not any("zz" in v.lower() for v in esp.valores.values())   # 0 teclas, 0 valores
    sin_valores(resp)


def test_campo_nuevo_o_faltante_detiene(ent):
    ent["sitio"].paginas["/f.html"] = HTML01.replace('<input id="telefono" name="telefono" type="tel" pattern="[0-9 +]{8,16}" autocomplete="tel">',
                                                     '<input id="doc" name="documento" type="text">')
    resp, salida = correr(ent, solicitud(ent["info"], espera=20))
    c = resp["formulario"]["cambios"]
    assert salida == 5 and c["faltan"] == ["registro::telefono"] and c["nuevos"] == ["registro::documento"]


def test_pagina_con_captcha_no_se_rellena(ent, sitio):
    url = sitio.base + "/captcha.html"
    from cauce_form_connector.page_inspector import BrowserSession
    from cauce_form_connector.signature import structure_signature_v2
    with BrowserSession(ent["s"], headless=True) as s:
        s.open_url(url)
        a = s.inspect()
    assert a.safety.blocked
    clave = next(f.key for f in a.all_fields if f.type == "email")
    path, sha = ProfileStore(ent["s"].profiles_dir).save_integration(
        "perfil_cap", a, {"email": clave}, formulario_id="frm_cap", url=url, aprobado_por_ref="rev_lab")
    info = {"perfil": "perfil_cap", "sha": sha, "firma": structure_signature_v2(a.all_fields), "formulario_id": "frm_cap", "url": url}
    resp, salida = correr(ent, solicitud(info, valores={"email": "zz.mariana@example.com"}, espera=20))
    assert (resp["estado"], salida) == ("detenido_intervencion_humana", 4)
    assert resp["errores"] == [{"codigo": "INTERVENCION_HUMANA_REQUERIDA"}] and resp["campos"] == []
    assert not any(Espia.ultimo.teclas.values())


# ---------------------------------------------------------------- entorno: navegador, red, redirecciones, cierre
def test_navegador_no_disponible(ent):
    ent["s"].browser_path = "/ruta/que/no/existe/chromium"
    resp, salida = correr(ent, solicitud(ent["info"]), fabrica_sesion=lambda s: I.BrowserSession(s, headless=True))
    assert (resp["estado"], salida) == ("error_interno", 1) and resp["errores"] == [{"codigo": "NAVEGADOR_NO_DISPONIBLE"}]
    assert resp["campos"] == []


def test_url_distinta_de_la_aprobada_en_el_perfil(ent):
    sol = solicitud(ent["info"])
    sol["formulario"]["url"] = "http://127.0.0.1:19/f.html"
    resp, salida = correr(ent, sol, fabrica_sesion=fabrica_prohibida)
    assert salida == 3 and resp["errores"] == [{"codigo": "PERFIL_NO_COINCIDE"}]


def test_servidor_caido_tras_aprobar(ent, sitio):
    # aprobado sobre una URL; el servidor deja de responder -> error de página, sin datos
    otro = Sitio()
    otro.paginas["/f.html"] = HTML01
    info = aprobar(ent["s"], otro.base + "/f.html", nombre="perfil_caido")
    otro.cerrar()
    resp, salida = correr(ent, solicitud(info))
    assert (resp["estado"], salida) == ("error_interno", 1) and resp["errores"] == [{"codigo": "PAGINA_NO_DISPONIBLE"}]


def test_redireccion_inesperada_no_se_sigue(ent, sitio):
    sitio.paginas["/otra.html"] = HTML01
    sitio.redirecciones["/f.html"] = "/otra.html"
    resp, salida = correr(ent, solicitud(ent["info"], espera=20))
    assert (resp["estado"], salida) == ("error_interno", 1) and resp["errores"] == [{"codigo": "PAGINA_NO_DISPONIBLE"}]
    assert not any(Espia.ultimo.teclas.values())


def test_redireccion_a_dominio_no_autorizado_se_bloquea(ent, sitio):
    sitio.redirecciones["/f.html"] = "https://sitio-externo.example/f.html"
    resp, salida = correr(ent, solicitud(ent["info"], espera=20))
    assert salida == 1 and resp["errores"] == [{"codigo": "PAGINA_NO_DISPONIBLE"}]


def test_cierre_inesperado_del_navegador_nunca_es_exito_de_envio(ent):
    def mata(s):
        s.page.wait_for_timeout(200)
        s._browser.close()

    resp, salida = correr(ent, solicitud(ent["info"], espera=20), gancho_revision=mata)
    assert resp["estado"] in ("preparado_revisado_sin_envio", "error_interno") and resp["envio"]["envio_humano_observado"] is False


def test_caida_de_la_pagina_es_error_interno(ent):
    def cae(s):
        s.page.goto("chrome://crash")

    resp, salida = correr(ent, solicitud(ent["info"], espera=20), gancho_revision=cae)
    assert (resp["estado"], salida) == ("error_interno", 1) and resp["errores"] == [{"codigo": "ERROR_INTERNO"}]
    assert resp["campos"] == [] and resp["huella_preparacion"] == ""


def test_excepcion_inesperada_no_filtra_valores(ent, capsys):
    def explota(s):
        raise RuntimeError("fallo con " + VALORES["email"])

    resp, salida = correr(ent, solicitud(ent["info"], espera=20), gancho_revision=explota)
    out = capsys.readouterr()
    assert (resp["estado"], salida) == ("error_interno", 1)
    sin_valores(resp, out.out, out.err)            # stderr lleva solo el nombre de la clase de la excepción


# ---------------------------------------------------------------- proceso real: stdin/stdout/stderr y archivos
def _proceso(ent, entrada: str, extra_env=None):
    env = {**os.environ, "CAUCE_HEADLESS": "1", "CAUCE_PROFILES_DIR": ent["s"].profiles_dir,
           "CAUCE_STATE_DIR": str(ent["state"]), "CAUCE_AUDIT_PATH": str(ent["tmp"] / "audit.jsonl"),
           "PYTHONPATH": str(Path(__file__).resolve().parents[1]), **(extra_env or {})}
    cwd = ent["tmp"] / "cwd"
    cwd.mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-m", "cauce_form_connector", "preparar"], input=entrada, text=True,
                          capture_output=True, cwd=cwd, env=env, timeout=120), cwd


def test_proceso_real_stdout_json_unico_sin_valores_y_sin_archivos(ent):
    sol = solicitud(ent["info"], espera=0)
    p, cwd = _proceso(ent, json.dumps(sol))
    assert p.returncode == 7
    resp = json.loads(p.stdout)                   # un solo objeto JSON, sin texto adicional
    assert C.validar_respuesta(resp, sol, p.returncode) == []
    sin_valores(p.stdout, p.stderr)
    # sin archivos con datos: solo el registro del id (hora) y nada en el directorio de trabajo
    usados = list((ent["state"] / "solicitudes_usadas").iterdir())
    assert [u.name for u in usados] == [sol["solicitud_id"]]
    assert "zz" not in usados[0].read_text().lower() and list(cwd.iterdir()) == []
    audit = (ent["tmp"] / "audit.jsonl")
    assert not audit.exists() or "zz" not in audit.read_text().lower()
    # segunda ejecución con el mismo id: no se reutiliza
    p2, _ = _proceso(ent, json.dumps(sol))
    assert p2.returncode == 6 and json.loads(p2.stdout)["errores"] == [{"codigo": "SOLICITUD_REPETIDA"}]


def test_proceso_real_entrada_basura_solo_codigo_6(ent):
    p, _ = _proceso(ent, "zz.mariana@example.com no es json")
    assert p.returncode == 6 and p.stdout == "" and "zz" not in (p.stdout + p.stderr).lower()


# ---------------------------------------------------------------- verificación cruzada con el código REAL de Monitor
MONITOR = os.environ.get("MONITOR_REPO", "")


@pytest.mark.skipif(not MONITOR or not (Path(MONITOR) / "monitoreo" / "conector" / "contrato.py").exists(),
                    reason="defina MONITOR_REPO con una copia de Cauce-monitor-engine en el commit 9e7f88d")
def test_respuestas_aceptadas_por_el_registro_de_monitor(ent, monkeypatch):
    monkeypatch.syspath_prepend(MONITOR)
    from monitoreo.conector import contrato as MC
    from monitoreo.conector.registro import Registro
    assert MC.VERSION == C.VERSION and MC.SALIDA == C.SALIDA and MC.MAPA_ESTADO == C.MAPA_ESTADO
    esperado = {}
    casos = [("ok_caducada", solicitud(ent["info"], espera=0), "CADUCADA"),
             ("incidencias", solicitud(ent["info"], valores={**VALORES, "email": "zz-mal"}), "CON_INCIDENCIAS")]
    malo = solicitud(ent["info"])
    malo["formulario"]["perfil_sha256"] = "1" * 64
    casos.append(("perfil_alterado", malo, "RECHAZADA"))
    ent["sitio"].paginas["/f.html"] = HTML01
    reg = Registro()
    for nombre, sol, estado_monitor in casos:
        assert MC.validar_solicitud(sol, datetime.now(timezone.utc)) == []          # Monitor acepta lo que se envió
        ahora = datetime.now(timezone.utc)
        reg.crear(sol, "clave-" + nombre + "-12345", ahora)
        reg.mover(sol["solicitud_id"], "AUTORIZADA", ahora)
        reg.mover(sol["solicitud_id"], "LANZADA", ahora)
        resp, salida = correr(ent, sol)
        final = reg.recibir(sol, resp, datetime.now(timezone.utc), salida)
        assert final["estado"] == estado_monitor, (nombre, final["estado"], final["codigo"])
    # el segundo uso del mismo id lo rechaza el conector Y el registro de Monitor
    resp, salida = correr(ent, casos[0][1], fabrica_sesion=fabrica_prohibida)
    assert resp["errores"] == [{"codigo": "SOLICITUD_REPETIDA"}] and salida == 6


def test_consumo_de_id_es_atomico_entre_hilos(tmp_path):
    import threading
    resultados = []

    def intenta():
        try:
            I._consumir_id(tmp_path / "ids", "sol_0123456789abcdef", datetime.now(timezone.utc))
            resultados.append("ok")
        except I._Parar as p:
            resultados.append(p.codigos[0])

    hilos = [threading.Thread(target=intenta) for _ in range(12)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert sorted(resultados) == ["SOLICITUD_REPETIDA"] * 11 + ["ok"]
