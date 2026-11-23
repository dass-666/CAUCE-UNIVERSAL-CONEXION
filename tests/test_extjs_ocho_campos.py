"""Lab 13: ocho campos con componentes Ext JS 6.2 REALES (desplegable editable con valor previo, desplegable no
editable, fecha, textos, confirmaciones, identificador de una fuente sintética). Se comprueba el valor INTERNO que
registró cada componente (`getValue()`), no solo lo que se ve. La biblioteca no está en el repositorio: se descarga con
`python scripts/preparar_lab_extjs.py`; sin ella estas pruebas se omiten."""
from __future__ import annotations

import functools
import http.server
import json
import shutil
import threading
from pathlib import Path

import pytest

from cauce_form_connector import listas as LJ
from cauce_form_connector import puente_monitor as P
from cauce_form_connector.page_inspector import BrowserSession

from integ_helpers import LAB, fabrica_espia, settings_para

ROOT = Path(__file__).resolve().parents[1]
EXP = json.loads((ROOT / "sample_data/expediente_monitor_sintetico.json").read_text(encoding="utf-8"))
CFG = ROOT / "config/formularios/lab_extjs_ocho_campos.json"
PAGINA = "/13_extjs_ocho_campos.html"

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not (LAB / "vendor/extjs/ext-all.js").is_file(),
                                 reason="falta Ext JS: ejecute python scripts/preparar_lab_extjs.py")]


class _Servidor:
    """Sirve lab_forms/ en 127.0.0.1. `reemplazos` altera la página del laboratorio (para simular que cambió)."""

    def __init__(self):
        outer = self
        self.reemplazos: dict[str, str] = {}
        self.escrituras = 0

        class H(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                outer.escrituras += 1
                self.send_response(204)
                self.end_headers()

            def do_GET(self):
                if self.path.split("?")[0] != PAGINA:
                    return super().do_GET()
                cuerpo = (LAB / PAGINA[1:]).read_text(encoding="utf-8")
                for a, b in outer.reemplazos.items():
                    assert a in cuerpo
                    cuerpo = cuerpo.replace(a, b)
                datos = cuerpo.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(datos)))
                self.end_headers()
                self.wfile.write(datos)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(LAB)))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def cerrar(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def ext(tmp_path, monkeypatch):
    monkeypatch.setenv("CAUCE_FUENTES_DIR", str(tmp_path))
    shutil.copy(ROOT / "sample_data/fuente_identificadores_sintetica.json", tmp_path / "identificadores.json")
    s = _Servidor()
    yield s, tmp_path
    s.cerrar()


def _aprobar(srv, tmp, editar=None):
    cfg = P.cargar_formulario(CFG, puerto=srv.port)
    if editar:
        editar(cfg)
    st = settings_para(tmp)
    return cfg, st, P.aprobar_perfil(cfg, st, aprobado_por="rev_prueba")


def _rellenar(cfg, st, tmp, n, exp=EXP):
    visto = {}

    def persona_revisa_y_cierra(s):
        s.page.wait_for_timeout(300)
        visto["interno"] = s.page.evaluate("window.valoresInternos()")       # lo que guarda cada componente Ext JS
        visto["dom"] = s.page.evaluate(
            "Object.fromEntries([...document.querySelectorAll('input')].filter(e => e.name).map(e => [e.name, e.value]))")
        visto["teclas"] = s.page.evaluate("window.__keylog || {}")
        s.page.close()

    r = P.autorrellenar(cfg, exp, st, espera_revision_s=30, state_dir=tmp / n, fabrica_sesion=fabrica_espia,
                        gancho_revision=persona_revisa_y_cierra)
    return r, visto


def test_ocho_de_ocho_con_valores_internos_y_perfil_reutilizado(ext):
    srv, tmp = ext
    cfg, st, aprobado = _aprobar(srv, tmp)                       # UNA aprobación
    for vuelta in range(2):                                       # dos ejecuciones con el mismo perfil aprobado
        r, visto = _rellenar(cfg, st, tmp, f"e{vuelta}")
        res, i, d = r["informe"]["resultado"], visto["interno"], visto["dom"]
        assert (r["salida"], res["verificados"], res["total"], res["completo"]) == (0, 8, 8, True), r["informe"]
        assert P.info_perfil(cfg, st)["perfil_sha256"] == aprobado["perfil_sha256"]       # el perfil no se rehízo
        # valores INTERNOS de los componentes: códigos, no textos; fecha como fecha
        assert i["operacion"] == "1" and i["tramite"] == "R2" and i["f_nac"] == "1990-05-17"
        assert i["num_cliente"] == "CL-000123" and i["tel"] == i["tel2"] == EXP["telefono"]
        assert i["mail"] == i["mail2"] == EXP["correo"] and i["valido"] is True
        assert i["pulsacionesEnviar"] == 0 and r["respuesta"]["envio"]["realizado_por_conector"] is False
        # lo que se ve y los campos ocultos que enviaría el formulario
        assert d["operacion_txt"] == "Nueva cita" and d["operacion"] == "1"                # sustituyó «Reprogramar cita»
        assert d["tramite_txt"] == "Renovación" and d["tramite"] == "R2" and d["f_nac"] == "17/05/1990"
        assert any("operacion" in k or "combo" in k for k in visto["teclas"]), visto["teclas"]   # se tecleó de verdad
    assert srv.escrituras == 0 and "CL-000123" not in json.dumps(r)


def test_opcion_inexistente_se_rechaza_al_aprobar(ext):
    srv, tmp = ext

    def inventada(cfg):
        cfg["equivalencias"]["tipo_tramite"]["valores"]["renovacion"] = "Renovación exprés"
    with pytest.raises(P.ConfigInvalida, match="no son opciones"):
        _aprobar(srv, tmp, inventada)


def test_opcion_que_desaparece_no_cuenta_como_exito(ext):
    srv, tmp = ext
    cfg, st, _ = _aprobar(srv, tmp)
    srv.reemplazos = {"{cod: 'R2', nom: 'Renovación'}, ": ""}     # el formulario ya no ofrece «Renovación»
    r, visto = _rellenar(cfg, st, tmp, "e")
    res = r["informe"]["resultado"]
    assert r["salida"] == 2 and (res["verificados"], res["completo"]) == (7, False)
    assert [p["campo"] for p in res["pendientes"]] == ["tipo_tramite"]
    assert visto["interno"]["tramite"] is None                    # no eligió «la más parecida» ni dejó otra
    assert visto["interno"]["operacion"] == "1"                   # el resto sí quedó


def test_equivalencia_ausente_queda_pendiente(ext):
    srv, tmp = ext

    def sin_fila(cfg):
        del cfg["equivalencias"]["operacion"]["valores"]["nueva"]            # el expediente trae «nueva»
    cfg, st, _ = _aprobar(srv, tmp, sin_fila)
    r, visto = _rellenar(cfg, st, tmp, "e")
    res = r["informe"]["resultado"]
    assert (r["salida"], r["salida_contrato"], res["verificados"]) == (P.SALIDA_PARCIAL, 0, 7)
    assert [p["campo"] for p in res["pendientes"]] == ["operacion"]
    assert visto["interno"]["operacion"] == "2"                   # el valor que ya tenía la página: no se tocó


def test_datos_faltantes_quedan_pendientes(ext):
    srv, tmp = ext
    cfg, st, _ = _aprobar(srv, tmp)
    (tmp / "identificadores.json").unlink()                                   # sin fuente de identificadores
    r, visto = _rellenar(cfg, st, tmp, "e", exp={**EXP, "fecha_nacimiento": None})
    res = r["informe"]["resultado"]
    assert (r["salida"], res["verificados"]) == (P.SALIDA_PARCIAL, 6)
    assert {p["campo"] for p in res["pendientes"]} == {"num_cliente", "fecha_nacimiento"}
    assert visto["interno"]["num_cliente"] == "" and visto["interno"]["f_nac"] is None     # nada inventado


def test_el_analisis_con_clic_no_pulsa_el_boton_del_formulario(ext, tmp_path):
    srv, _ = ext
    s = BrowserSession(settings_para(tmp_path), headless=True)
    s.start()
    try:
        s.open_url(f"http://127.0.0.1:{srv.port}{PAGINA}")
        antes = s.page.evaluate("window.valoresInternos()")
        informe = LJ.descubrir(s, s.inspect().all_fields, modo="clic")
        despues = s.page.evaluate("window.valoresInternos()")
    finally:
        s.close()
    assert despues["pulsacionesEnviar"] == 0 and despues == antes and not s.analisis_alterado
    listas = {e["clave"]: e.get("opciones") for e in informe if e.get("opciones")}
    assert listas == {"alta::lista_operacion": 2, "alta::lista_tramite": 3}
    assert any("no se pulsa" in e.get("motivo", "") for e in informe)        # el botón «Enviar» queda sin pulsar
