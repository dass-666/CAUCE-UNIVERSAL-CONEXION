"""Analizador de formularios externos: detección, decisiones, solo lectura, perfil compatible y vista previa.

Todo con páginas de laboratorio locales y datos FICTICIOS; ninguna prueba toca sitios de terceros.
"""
from __future__ import annotations

import functools
import http.server
import json
import re
import threading
from pathlib import Path

import pytest

from cauce_form_connector import analizador_formularios as A
from cauce_form_connector import esquema_monitor as M
from cauce_form_connector import puente_monitor as P
from cauce_form_connector.cli import main as cli_main
from cauce_form_connector.html_analyzer import analyze_html
from cauce_form_connector.url_policy import UrlNotAllowed

from integ_helpers import LAB, fabrica_espia, settings_para, Espia

ROOT = Path(__file__).resolve().parents[1]
EXP_MONITOR = json.loads((ROOT / "sample_data/expediente_monitor_sintetico.json").read_text(encoding="utf-8"))
# Valores del expediente que NUNCA deben aparecer en informe, perfil ni vista previa
SECRETOS = ["Zzmariana", "Zzejemplo", "example.com", "5587654321", "1990-05-17", "2026-12-15", "S001", "R01"]


class _Registro(http.server.SimpleHTTPRequestHandler):
    peticiones: list[tuple[str, str]] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        _Registro.peticiones.append(("GET", self.path))
        super().do_GET()

    def do_POST(self):  # si el analizador enviara algo, quedaría registrado aquí
        _Registro.peticiones.append(("POST", self.path))
        self.send_response(500)
        self.end_headers()


@pytest.fixture()
def sitio_registro():
    _Registro.peticiones = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Registro, directory=str(LAB)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _por_name(inf: dict) -> dict:
    return {c["name"]: c for c in inf["campos"]}


def _sin_secretos(texto: str) -> None:
    for s in SECRETOS:
        assert s not in texto, f"el valor «{s}» del expediente apareció en la salida"


# ------------------------------------------------------------------------------------------- navegador
@pytest.mark.browser
def test_detecta_todo_lo_pedido_en_formulario_externo_simulado(sitio_registro, tmp_path):
    inf = A.analizar_url(sitio_registro + "/05_reserva_externa.html", autorizado=False,
                         settings=settings_para(tmp_path), expediente=EXP_MONITOR)
    c = _por_name(inf)
    # Texto y numéricos, con selectores
    assert c["nombre"]["categoria"] == "texto" and c["nombre"]["selector"] == "#nombre"
    assert c["personas"]["categoria"] == "numerico" and c["personas"]["validaciones"]["max"] == "6"
    assert c["tel"]["categoria"] == "numerico_texto" and c["tel"]["validaciones"]["pattern"] == r"\d{10}"
    # Listas con opciones (select y radio)
    assert [o["valor"] for o in c["estado"]["opciones"]] == ["", "CMX", "JAL", "NLE"]
    assert c["tramite"]["categoria"] == "lista" and len(c["tramite"]["opciones"]) == 2
    # Fechas: nativa y en texto con formato
    assert c["f_nac"]["categoria"] == "fecha"
    assert c["fecha_cita"]["categoria"] == "fecha_texto" and c["fecha_cita"]["validaciones"]["formato_fecha"] == "dd/mm/aaaa"
    # Confirmación repetida
    assert c["correo2"]["categoria"] == "confirmacion" and c["correo2"]["confirma_a"] == c["correo"]["clave"]
    assert c["correo2"]["validaciones"]["bloquea_pegado"] is True
    # Validaciones visibles (mensajes asociados por aria-describedby, title, required)
    assert "La CURP debe tener 18 caracteres." in c["doc_identidad"]["validaciones"]["mensajes"]
    assert c["doc_identidad"]["validaciones"]["title"] == "18 caracteres en mayúsculas"
    assert c["nombre"]["obligatorio"] and not c["comentarios"]["obligatorio"]
    # Elementos dinámicos: creado por JS y bloque condicional oculto
    assert c["horario"]["dinamico"].get("creado_por_javascript") is True
    assert c["folio_anterior"]["dinamico"].get("oculto_condicional") is True
    assert c["folio_anterior"]["dinamico"].get("oculto_por") == "#bloque_folio"
    assert not c["nombre"]["dinamico"]
    assert inf["pagina"]["regiones_aria_live"] == 1 and inf["pagina"]["alertas_role_alert"] == 1

    # Decisiones: qué se rellena solo y qué necesita a una persona
    auto = {n for n, x in c.items() if x["decision"] == "automatico"}
    assert auto == {"f_nac", "correo", "correo2", "tel"}
    # nombre y apellidos separados: Monitor solo tiene el nombre completo -> no se divide
    assert c["nombre"]["decision"] == c["apellidos"]["decision"] == "revisar"
    assert "no se divide" in c["apellidos"]["motivo"]
    assert c["doc_identidad"]["decision"] == "humano" and c["doc_identidad"]["propuesta"] is None   # CURP: nunca
    assert c["acepto"]["decision"] == "humano" and c["acepto"]["propuesta"] is None                 # consentimiento
    assert c["estado"]["decision"] == "humano" and c["estado"]["propuesta"] is None   # «Estado» no es «region»
    assert c["tramite"]["decision"] == "revisar" and "tabla de equivalencias" in c["tramite"]["motivo"]
    assert c["fecha_cita"]["propuesta"]["ruta_expediente"] == "fecha_solicitada" and c["fecha_cita"]["decision"] == "revisar"
    assert c["comprobante"]["decision"] == "humano"                # archivos: persona
    assert c["horario"]["decision"] == "humano" and c["personas"]["decision"] == "humano"
    assert c["token_sesion"]["decision"] == "ignorado"

    # Solo lectura: una carga de la página y su CSS; ningún POST
    assert not [p for p in _Registro.peticiones if p[0] != "GET"]
    assert [p for p in _Registro.peticiones if p[1].endswith(".html")] == [("GET", "/05_reserva_externa.html")]
    assert inf["solo_lectura"] is True and inf["envio_realizado"] is False

    rutas = A.guardar(inf, tmp_path / "salida")
    for r in rutas.values():
        _sin_secretos(Path(r).read_text(encoding="utf-8"))


@pytest.mark.browser
def test_perfil_generado_funciona_con_autorrellenar(sitio_registro, tmp_path):
    """El borrador generado se carga con `cargar_formulario`, se aprueba y el motor existente lo rellena."""
    st = settings_para(tmp_path)
    url = sitio_registro + "/03_validaciones.html"
    inf = A.analizar_url(url, autorizado=False, settings=st, expediente=EXP_MONITOR)
    perfil = inf["perfil_borrador"]
    assert perfil["_borrador"]["estado"] == "pendiente_revision_humana"
    assert set(perfil["campos_formulario"].values()) == {
        "alta::nombre_completo", "alta::email", "alta::email_confirmacion", "alta::telefono", "alta::telefono_confirmacion"}
    assert set(perfil["campos_expediente"].values()) == {"nombre", "correo", "telefono"}   # rutas planas de Monitor
    c = _por_name(inf)
    assert c["codigo_postal"]["decision"] == c["ciudad"]["decision"] == "humano"      # Monitor no tiene esos datos
    assert c["acepta_privacidad"]["decision"] == "humano"
    ruta = tmp_path / "perfil_borrador.json"
    ruta.write_text(json.dumps(perfil, ensure_ascii=False), encoding="utf-8")

    cfg = P.cargar_formulario(ruta)
    assert P.autorrellenar(cfg, EXP_MONITOR, st, state_dir=tmp_path / "e")["salida"] == 6   # sin aprobar: no corre
    P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
    r = P.autorrellenar(cfg, EXP_MONITOR, st, espera_revision_s=0, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia)
    inf2 = r["informe"]
    assert r["respuesta"]["envio"]["realizado_por_conector"] is False
    assert r["respuesta"]["contrato"] == "cauce.conector.v1"
    assert len(inf2["campos"]) == 5 and all(x["correcto"] for x in inf2["campos"]), inf2
    assert all(x["coinciden"] for x in inf2["confirmaciones"])
    assert Espia.ultimo.valores["email_confirmacion"] == "zz.mariana@example.com"
    assert Espia.ultimo.valores["codigo_postal"] == "" and Espia.ultimo.valores["ciudad"] == ""   # no inventa datos
    assert "S001" not in json.dumps(Espia.ultimo.valores) and "renovacion" not in json.dumps(Espia.ultimo.valores)


@pytest.mark.browser
def test_captcha_todo_queda_para_la_persona(tmp_path):
    inf = A.analizar_url((LAB / "04_captcha_simulado.html").resolve().as_uri(), autorizado=False,
                         settings=settings_para(tmp_path), expediente=EXP_MONITOR)
    assert inf["bloqueo_humano"]["blocked"]
    assert {c["decision"] for c in inf["campos"]} <= {"humano", "ignorado"}
    assert inf["perfil_borrador"]["campos_formulario"] == {}


@pytest.mark.browser
def test_vista_previa_permite_revisar_y_descargar(sitio_registro, tmp_path):
    from cauce_form_connector.page_inspector import BrowserSession
    st = settings_para(tmp_path)
    inf = A.analizar_url(sitio_registro + "/05_reserva_externa.html", autorizado=False, settings=st, expediente=EXP_MONITOR)
    rutas = A.guardar(inf, tmp_path / "s")
    with BrowserSession(st, headless=True) as s:
        s.open_url(Path(rutas["vista_previa"]).resolve().as_uri())
        inicial = json.loads(s.page.text_content("#json"))
        assert set(inicial["campos_formulario"]) == set(inf["perfil_borrador"]["campos_formulario"])
        # La persona desmarca el teléfono e incluye el folio (revisar) con su ruta propuesta
        filas = [c for c in inf["campos"] if c["nombre_logico"]]
        i_tel = next(i for i, c in enumerate(filas) if c["name"] == "tel")
        i_doc = next(i for i, c in enumerate(filas) if c["name"] == "folio_anterior")
        s.page.uncheck(f'[data-i="{i_tel}"]')
        s.page.check(f'[data-i="{i_doc}"]')
        final = json.loads(s.page.text_content("#json"))
        # Las casillas de archivo están bloqueadas
        i_arch = next(i for i, c in enumerate(filas) if c["name"] == "comprobante")
        assert s.page.is_disabled(f'[data-i="{i_arch}"]')
    assert "telefono" not in final["campos_formulario"]
    assert final["campos_expediente"][filas[i_doc]["nombre_logico"]] == "folio_anterior"
    assert final["_borrador"]["estado"] == "revisado_pendiente_aprobacion"


@pytest.mark.browser
def test_cli_genera_los_cuatro_archivos(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CAUCE_PROFILES_DIR", str(tmp_path / "p"))
    rc = cli_main(["analizar-formulario", "--html", str(LAB / "05_reserva_externa.html"),
                   "--expediente", str(ROOT / "sample_data/expediente_monitor_sintetico.json"),
                   "--salida", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 0 and "no se envió nada" in out
    for n in ("informe.json", "informe.md", "perfil_borrador.json", "vista_previa.html"):
        assert (tmp_path / "out" / n).exists()
    _sin_secretos(out)


# ------------------------------------------------------------------------------------------- sin navegador
def test_url_externa_sin_autorizacion_no_se_abre(tmp_path, monkeypatch):
    import cauce_form_connector.page_inspector as PI
    monkeypatch.setattr(PI.BrowserSession, "start", lambda self: (_ for _ in ()).throw(AssertionError("no debía abrirse")))
    with pytest.raises(UrlNotAllowed):
        A.analizar_url("https://tramites.ejemplo.gob/reserva", autorizado=False, settings=settings_para(tmp_path))
    with pytest.raises(UrlNotAllowed):   # declarar autorización no basta: el dominio debe estar en la lista
        A.analizar_url("https://tramites.ejemplo.gob/reserva", autorizado=True, settings=settings_para(tmp_path))
    assert cli_main(["analizar-formulario", "--url", "https://tramites.ejemplo.gob/reserva", "--authorized"]) == 3


def _informe_estatico(html: str, exp: dict | None) -> dict:
    return A.construir_informe(analyze_html(html), {}, "http://127.0.0.1/x.html", expediente=exp)


def test_lab06_correspondencias_correctas_e_incorrectas():
    inf = _informe_estatico((LAB / "06_cita_monitor.html").read_text(encoding="utf-8"), EXP_MONITOR)
    c = _por_name(inf)
    ruta = lambda n: (c[n]["propuesta"] or {}).get("ruta_expediente")
    # Correctas y automáticas
    assert {n for n, x in c.items() if x["decision"] == "automatico"} == {
        "nombre_completo", "nacimiento", "telefono", "correo", "correo_confirmacion"}
    assert [ruta(n) for n in ("nombre_completo", "nacimiento", "telefono", "correo", "correo_confirmacion")] == [
        "nombre", "fecha_nacimiento", "telefono", "correo", "correo"]
    # Códigos: aunque los valores coincidan literalmente, hace falta tabla aprobada
    for n, campo in (("tipo_tramite", "tipo_tramite"), ("oficina", "sucursal"), ("zona", "region"), ("modo", "modo")):
        assert c[n]["decision"] == "revisar" and ruta(n) == campo and "tabla de equivalencias" in c[n]["motivo"]
    assert "coinciden literalmente" in c["tipo_tramite"]["motivo"]
    eq = {e["campo_monitor"]: e for e in inf["perfil_borrador"]["_borrador"]["equivalencias_pendientes"]}
    assert set(eq) == {"tipo_tramite", "sucursal", "region", "modo"}
    assert eq["modo"]["tabla"] == {"POR_DIA": None, "POR_HORARIO": None} and eq["sucursal"]["codigos_monitor"] == "catálogo no disponible"
    assert all(e["aprobada_por"] is None for e in eq.values())
    # Fechas de cita y rangos, identificadores: revisión
    assert (ruta("fecha_preferida"), ruta("desde"), ruta("hasta")) == ("fecha_solicitada", "fechas_desde", "fechas_hasta")
    assert {c[n]["decision"] for n in ("fecha_preferida", "desde", "hasta", "registro_previo")} == {"revisar"}
    assert ruta("registro_previo") == "id_registro_previo"
    # Nunca: RFC/consentimiento
    assert c["rfc"]["decision"] == "humano" and ruta("rfc") is None
    assert c["autorizo"]["decision"] == "humano" and ruta("autorizo") is None
    # El perfil solo lleva campos automáticos y rutas existentes en Monitor
    perfil = inf["perfil_borrador"]
    assert set(perfil["campos_expediente"].values()) <= set(M.ESQUEMA)
    assert not set(perfil["campos_expediente"].values()) & {"tipo_tramite", "sucursal", "region", "modo", "id_externo"}


def test_reglas_de_dato_incorrecto():
    html = """<form id="f">
      <label for="n">Nombre</label><input id="n" name="n">
      <label for="tel">Teléfono</label><input id="tel" name="tel" type="tel" pattern="\\d{8}">
      <label for="fn">Fecha de nacimiento (dd/mm/aaaa)</label><input id="fn" name="fn" placeholder="dd/mm/aaaa">
      <label for="cl">Identificador del cliente</label><input id="cl" name="cl">
      <label for="estado">Estado</label><select id="estado" name="estado"><option value="CMX">CDMX</option></select>
      <label for="pw">Contraseña</label><input id="pw" name="pw" type="password">
    </form>"""
    exp = dict(EXP_MONITOR, id_externo="ZZ-SINTETICO-1")
    c = _por_name(_informe_estatico(html, exp))
    assert c["n"]["decision"] == "revisar" and "completo" in c["n"]["motivo"]   # «Nombre» a secas: confirmar
    assert c["tel"]["decision"] == "revisar" and "patrón" in c["tel"]["motivo"]  # 10 dígitos de Monitor vs pattern 8
    assert c["fn"]["decision"] == "revisar" and "AAAA-MM-DD" in c["fn"]["motivo"]
    assert c["cl"]["decision"] == "humano" and c["cl"]["propuesta"] is None     # nunca id_externo por suposición
    assert c["estado"]["decision"] == "humano"
    assert c["pw"]["decision"] == "humano"


def test_expediente_que_no_sigue_el_modelo_de_monitor():
    viejo = {"titular": {"nombre_completo": "Zzx"}, "contacto": {"correo": "zz@example.com"}, "curp": "X"}
    assert M.validar_expediente(EXP_MONITOR) == []
    avisos = " ".join(M.validar_expediente(viejo))
    assert "titular" in avisos and "curp" in avisos and "faltan" in avisos
    malo = dict(EXP_MONITOR, telefono="55-8765", fecha_nacimiento="17/05/1990", modo="SEMANAL")
    assert len(M.validar_expediente(malo)) == 3
    html = '<form id="f"><label for="e">Correo</label><input id="e" name="e" type="email"></form>'
    inf = _informe_estatico(html, viejo)
    assert _por_name(inf)["e"]["decision"] == "revisar"              # el expediente no trae «correo» plano
    assert any("titular" in a for a in inf["advertencias"])
    sin = _informe_estatico(html, None)
    assert sin["perfil_borrador"]["campos_formulario"] == {}
    assert "No se indicó expediente" in " ".join(sin["advertencias"])


def test_expediente_sintetico_tiene_exactamente_el_modelo_de_monitor():
    assert list(EXP_MONITOR) == list(M.ESQUEMA)
    assert EXP_MONITOR["id_externo"] is None                          # identificador personal: ni siquiera sintético


def test_perfil_borrador_respeta_formato_de_config_formularios(tmp_path):
    html = (LAB / "03_validaciones.html").read_text(encoding="utf-8")
    inf = _informe_estatico(html, EXP_MONITOR)
    perfil = inf["perfil_borrador"]
    assert re.fullmatch(r"frm_[a-z0-9_]{1,40}", perfil["formulario_id"])
    assert re.fullmatch(r"[a-z0-9_]{1,40}", perfil["perfil"])
    assert set(perfil["campos_formulario"]) == set(perfil["campos_expediente"])
    ruta = tmp_path / "p.json"
    ruta.write_text(json.dumps(perfil), encoding="utf-8")
    cfg = P.cargar_formulario(ruta)                                   # el validador existente lo acepta
    valores, faltan = P.extraer_campos(EXP_MONITOR, cfg)
    assert faltan == [] and set(valores) == set(perfil["campos_formulario"])
    _sin_secretos(json.dumps(inf) + A.informe_markdown(inf) + A.vista_previa_html(inf))
