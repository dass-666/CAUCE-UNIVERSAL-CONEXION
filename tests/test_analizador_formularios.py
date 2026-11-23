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
from cauce_form_connector import puente_monitor as P
from cauce_form_connector.cli import main as cli_main
from cauce_form_connector.html_analyzer import analyze_html
from cauce_form_connector.url_policy import UrlNotAllowed

from integ_helpers import LAB, fabrica_espia, settings_para, Espia

ROOT = Path(__file__).resolve().parents[1]
EXP_RESERVA = json.loads((ROOT / "sample_data/expediente_ficticio_reserva.json").read_text(encoding="utf-8"))
EXP_BASE = json.loads((ROOT / "sample_data/expediente_ficticio.json").read_text(encoding="utf-8"))
# Valores del expediente que NUNCA deben aparecer en informe, perfil ni vista previa
SECRETOS = ["Mariana", "Zzejemplo", "example.com", "5587654321", "ZZPM900517", "1990-05-17", "jamás", "06700"]


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
                         settings=settings_para(tmp_path), expediente=EXP_RESERVA)
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
    assert auto == {"nombre", "apellidos", "f_nac", "correo", "correo2", "tel", "estado"}
    assert c["doc_identidad"]["decision"] == "revisar"             # documento de identidad: siempre revisión
    assert c["acepto"]["decision"] == "revisar"                    # consentimiento: siempre revisión
    assert c["comprobante"]["decision"] == "humano"                # archivos: persona
    assert c["horario"]["decision"] == "humano" and c["personas"]["decision"] == "humano"
    assert c["token_sesion"]["decision"] == "ignorado"
    assert c["estado"]["propuesta"]["encaja_en_opciones"] is True  # "Ciudad de México" -> CMX

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
    inf = A.analizar_url(url, autorizado=False, settings=st, expediente=EXP_BASE)
    perfil = inf["perfil_borrador"]
    assert perfil["_borrador"]["estado"] == "pendiente_revision_humana"
    pendientes = {p["clave_campo"] for p in perfil["_borrador"]["pendientes_revision"]}
    assert "alta::acepta_privacidad" in pendientes                   # el consentimiento lo decide una persona
    assert set(perfil["campos_formulario"].values()) == {
        "alta::nombre_completo", "alta::email", "alta::email_confirmacion", "alta::telefono",
        "alta::telefono_confirmacion", "alta::codigo_postal", "alta::ciudad"}
    ruta = tmp_path / "perfil_borrador.json"
    ruta.write_text(json.dumps(perfil, ensure_ascii=False), encoding="utf-8")

    cfg = P.cargar_formulario(ruta)
    assert P.autorrellenar(cfg, EXP_BASE, st, state_dir=tmp_path / "e")["salida"] == 6   # sin aprobar: no corre
    P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")
    r = P.autorrellenar(cfg, EXP_BASE, st, espera_revision_s=0, state_dir=tmp_path / "e", fabrica_sesion=fabrica_espia)
    inf2 = r["informe"]
    assert r["respuesta"]["envio"]["realizado_por_conector"] is False
    assert len(inf2["campos"]) == 7 and all(x["correcto"] for x in inf2["campos"]), inf2
    assert all(x["coinciden"] for x in inf2["confirmaciones"])
    assert Espia.ultimo.valores["email_confirmacion"] == "mariana.zz@example.com"
    assert "CURP" not in json.dumps(Espia.ultimo.valores) and "NO-SE-USA" not in json.dumps(Espia.ultimo.valores)


@pytest.mark.browser
def test_captcha_todo_queda_para_la_persona(tmp_path):
    inf = A.analizar_url((LAB / "04_captcha_simulado.html").resolve().as_uri(), autorizado=False,
                         settings=settings_para(tmp_path), expediente=EXP_RESERVA)
    assert inf["bloqueo_humano"]["blocked"]
    assert {c["decision"] for c in inf["campos"]} <= {"humano", "ignorado"}
    assert inf["perfil_borrador"]["campos_formulario"] == {}


@pytest.mark.browser
def test_vista_previa_permite_revisar_y_descargar(sitio_registro, tmp_path):
    from cauce_form_connector.page_inspector import BrowserSession
    st = settings_para(tmp_path)
    inf = A.analizar_url(sitio_registro + "/05_reserva_externa.html", autorizado=False, settings=st, expediente=EXP_RESERVA)
    rutas = A.guardar(inf, tmp_path / "s")
    with BrowserSession(st, headless=True) as s:
        s.open_url(Path(rutas["vista_previa"]).resolve().as_uri())
        inicial = json.loads(s.page.text_content("#json"))
        assert set(inicial["campos_formulario"]) == set(inf["perfil_borrador"]["campos_formulario"])
        # La persona desmarca el teléfono e incluye la CURP con su ruta propuesta
        filas = [c for c in inf["campos"] if c["nombre_logico"]]
        i_tel = next(i for i, c in enumerate(filas) if c["name"] == "tel")
        i_doc = next(i for i, c in enumerate(filas) if c["name"] == "doc_identidad")
        s.page.uncheck(f'[data-i="{i_tel}"]')
        s.page.check(f'[data-i="{i_doc}"]')
        final = json.loads(s.page.text_content("#json"))
        # Las casillas de archivo están bloqueadas
        i_arch = next(i for i, c in enumerate(filas) if c["name"] == "comprobante")
        assert s.page.is_disabled(f'[data-i="{i_arch}"]')
    assert "telefono" not in final["campos_formulario"]
    assert final["campos_expediente"][filas[i_doc]["nombre_logico"]] == "titular.curp"
    assert final["_borrador"]["estado"] == "revisado_pendiente_aprobacion"


@pytest.mark.browser
def test_cli_genera_los_cuatro_archivos(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CAUCE_PROFILES_DIR", str(tmp_path / "p"))
    rc = cli_main(["analizar-formulario", "--html", str(LAB / "05_reserva_externa.html"),
                   "--expediente", str(ROOT / "sample_data/expediente_ficticio_reserva.json"),
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


def test_reglas_de_decision_sin_navegador():
    html = """<form id="f">
      <label for="pais">País</label><select id="pais" name="pais"><option value="ES">España</option></select>
      <label for="cp">Código postal</label><input id="cp" name="cp" pattern="\\d{4}">
      <label for="fn">Fecha de nacimiento</label><input id="fn" name="fn" type="date">
      <label for="pw">Contraseña</label><input id="pw" name="pw" type="password">
    </form>"""
    exp = {"domicilio": {"pais": "México", "cp": "06700"}, "titular": {"fecha_nacimiento": "no consta"}}
    c = _por_name(_informe_estatico(html, exp))
    assert c["pais"]["decision"] == "revisar" and "opción" in c["pais"]["motivo"]       # valor fuera de la lista
    assert c["cp"]["decision"] == "revisar" and "patrón" in c["cp"]["motivo"]           # no cumple pattern
    assert c["fn"]["decision"] == "revisar" and "fecha" in c["fn"]["motivo"]
    assert c["pw"]["decision"] == "humano"
    sin_exp = _informe_estatico(html, None)
    assert "No se indicó expediente" in " ".join(sin_exp["advertencias"])
    assert sin_exp["perfil_borrador"]["campos_formulario"] == {}


def test_perfil_borrador_respeta_formato_de_config_formularios(tmp_path):
    html = (LAB / "03_validaciones.html").read_text(encoding="utf-8")
    inf = _informe_estatico(html, EXP_BASE)
    perfil = inf["perfil_borrador"]
    assert re.fullmatch(r"frm_[a-z0-9_]{1,40}", perfil["formulario_id"])
    assert re.fullmatch(r"[a-z0-9_]{1,40}", perfil["perfil"])
    assert set(perfil["campos_formulario"]) == set(perfil["campos_expediente"])
    ruta = tmp_path / "p.json"
    ruta.write_text(json.dumps(perfil), encoding="utf-8")
    cfg = P.cargar_formulario(ruta)                                   # el validador existente lo acepta
    valores, faltan = P.extraer_campos(EXP_BASE, cfg)
    assert faltan == [] and "NO-SE-USA" not in json.dumps(valores)
    _sin_secretos(json.dumps(inf) + A.informe_markdown(inf) + A.vista_previa_html(inf))


def test_aplanar_ignora_listas_y_marca_vacios():
    plano = A.aplanar({"a": {"b": "x", "c": ""}, "l": [1, 2], "n": None})
    assert plano == {"a.b": "x", "a.c": "", "n": None}
