"""Bloque C · registro durable de operaciones (sin navegador ni motor)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from cauce_form_connector.operaciones import (ABIERTOS, CONFIRMADA, EN_CURSO, INCIERTA, REVISADA, OperacionBloqueada,
                                              RegistroOperaciones)

FILA = {"expediente": "EXP-0001", "nombre": "Ana Ficticia Uno", "correo": "ana.ficticia@example.test",
        "telefono": "5512345678"}


def _reg(tmp_path) -> RegistroOperaciones:
    return RegistroOperaciones(tmp_path / "datos")


def test_en_curso_se_guarda_en_disco_antes_de_actuar(tmp_path):
    r = _reg(tmp_path)
    op = r.iniciar(r.huella(FILA), "http://127.0.0.1/tramite", "tok_abcdefgh", None)
    doc = json.loads((tmp_path / "datos" / "operaciones.json").read_text())
    assert [o["estado"] for o in doc["operaciones"]] == [EN_CURSO]
    assert doc["operaciones"][0]["id"] == op["id"]


def test_reinicio_convierte_en_curso_en_incierta_y_bloquea_repetir(tmp_path):
    r = _reg(tmp_path)
    h = r.huella(FILA)
    op = r.iniciar(h, "x", None, None)
    r2 = _reg(tmp_path)                                  # «Forms se reinició» con la operación a medias
    o = r2.get(op["id"])
    assert o["estado"] == INCIERTA and o["motivo"] == "forms_reiniciado_durante_operacion"
    with pytest.raises(OperacionBloqueada) as e:
        r2.comprobar(h)
    assert e.value.codigo == "expediente_incierta"
    r3 = _reg(tmp_path)                                  # otro reinicio no la «olvida»
    assert r3.get(op["id"])["estado"] == INCIERTA


def test_confirmada_tambien_bloquea_un_segundo_envio_del_mismo_expediente(tmp_path):
    r = _reg(tmp_path)
    h = r.huella(FILA)
    op = r.iniciar(h, "x", None, None)
    r.cerrar(op["id"], CONFIRMADA, "acuse_verificado", "LAB-00001-ABCD")
    with pytest.raises(OperacionBloqueada) as e:
        r.comprobar(h)
    assert e.value.codigo == "expediente_confirmada"
    r.comprobar(r.huella(dict(FILA, expediente="EXP-0002")))   # otro expediente sí


def test_confirmada_exige_folio(tmp_path):
    r = _reg(tmp_path)
    op = r.iniciar(r.huella(FILA), "x", None, None)
    with pytest.raises(ValueError):
        r.cerrar(op["id"], CONFIRMADA, "clic", None)       # un clic no es una recepción


def test_huella_no_contiene_datos_y_depende_de_la_clave_local(tmp_path):
    r = _reg(tmp_path)
    h = r.huella(FILA)
    for v in FILA.values():
        assert v.lower() not in h.lower()
    otro = RegistroOperaciones(tmp_path / "otro")
    assert otro.huella(FILA) != h                         # sin la clave no se puede comparar
    assert _reg(tmp_path).huella(FILA) == h               # estable en el mismo puesto


def test_el_archivo_no_guarda_datos_personales(tmp_path):
    r = _reg(tmp_path)
    op = r.iniciar(r.huella(FILA), "http://127.0.0.1/tramite", "tok_abcdefgh", "bs_0123456789ab")
    r.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    r.revisar(op["id"], "Revisé el portal; llamar a ana.ficticia@example.test 5512345678", op["id"])
    texto = "".join(p.read_text("utf-8", "ignore") for p in (tmp_path / "datos").glob("operaciones.json*"))
    for v in FILA.values():
        assert v not in texto
    assert "[correo]" in texto and "[número]" in texto     # la nota del operador se sanea


def test_archivo_danado_usa_respaldo_y_pide_revision_general(tmp_path):
    r = _reg(tmp_path)
    op1 = r.iniciar(r.huella(FILA), "x", None, None)
    r.cerrar(op1["id"], CONFIRMADA, "acuse_verificado", "LAB-00001-ABCD")
    op2 = r.iniciar(r.huella(dict(FILA, expediente="EXP-0002")), "x", None, None)   # .bak = antes de op2
    p = tmp_path / "datos" / "operaciones.json"
    p.write_text(p.read_text()[:-20])                   # escritura cortada
    r2 = _reg(tmp_path)
    assert r2.estado_carga == "respaldo"
    assert r2.general and r2.general["motivo"] == "registro_recuperado_de_respaldo"
    with pytest.raises(OperacionBloqueada) as e:
        r2.comprobar(r2.huella(dict(FILA, expediente="EXP-0099")))
    assert e.value.codigo == "revision_general_pendiente"
    assert list((tmp_path / "datos").glob("operaciones.json.corrupt-*"))   # el dañado se aparta, no se borra
    assert r2.get(op2["id"]) is None or r2.get(op2["id"])["estado"] in ABIERTOS


def test_archivo_y_respaldo_danados_bloquean_todo_hasta_revision(tmp_path):
    r = _reg(tmp_path)
    r.iniciar(r.huella(FILA), "x", None, None)
    r.iniciar(r.huella(dict(FILA, expediente="EXP-0002")), "x", None, None)
    for n in ("operaciones.json", "operaciones.json.bak"):
        (tmp_path / "datos" / n).write_text("{basura")
    r2 = _reg(tmp_path)
    assert r2.estado_carga == "ilegible" and r2.general["motivo"] == "registro_ilegible"
    assert r2.hay_abiertas()
    with pytest.raises(OperacionBloqueada):
        r2.comprobar(r2.huella({"x": "1"}))
    with pytest.raises(ValueError):
        r2.revisar("GENERAL", "revisado", "general")       # confirmación exacta
    r2.revisar("GENERAL", "Revisé el portal y el registro anterior", "GENERAL")
    r2.comprobar(r2.huella({"x": "1"}))


def test_editar_el_archivo_a_mano_no_autoriza_nada(tmp_path):
    r = _reg(tmp_path)
    op = r.iniciar(r.huella(FILA), "x", None, None)
    r.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    p = tmp_path / "datos" / "operaciones.json"
    d = json.loads(p.read_text())
    d["operaciones"][0]["estado"] = "revisada"            # alguien intenta «aprobarla» editando el JSON
    p.write_text(json.dumps(d))
    r2 = _reg(tmp_path)
    assert r2.estado_carga == "respaldo"
    assert r2.general is not None                          # el resumen SHA-256 no cuadra: revisión general


def test_revision_exige_nota_y_confirmacion_y_solo_de_abiertas(tmp_path):
    r = _reg(tmp_path)
    op = r.iniciar(r.huella(FILA), "x", None, None)
    r.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    with pytest.raises(ValueError):
        r.revisar(op["id"], "ok", op["id"])
    with pytest.raises(ValueError):
        r.revisar(op["id"], "Revisé el acuse en el portal", "op_000000000000")
    o = r.revisar(op["id"], "Revisé el acuse en el portal", op["id"], resultado="no_recibido")
    assert o["estado"] == REVISADA
    with pytest.raises(ValueError):
        r.revisar(op["id"], "Otra vez", op["id"])


def test_resolucion_en_ne_solo_con_senal_positiva_de_la_misma_sesion(tmp_path):
    r = _reg(tmp_path)
    op = r.iniciar(r.huella(FILA), "x", None, "bs_0123456789ab")
    r.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    assert r.resuelta_por_operador_ne("bs_0123456789ab") == []      # el motor nunca la llegó a marcar
    r.marca_vista_en("bs_0123456789ab")
    assert r.resuelta_por_operador_ne("bs_ffffffffffff") == []      # otra sesión no la resuelve
    assert r.resuelta_por_operador_ne("bs_0123456789ab") == [op["id"]]
    assert r.get(op["id"])["estado"] == REVISADA


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="permisos POSIX (root ignora 0500)")
def test_sin_poder_escribir_no_se_inicia_la_operacion(tmp_path):
    r = _reg(tmp_path)
    d = tmp_path / "datos"
    os.chmod(d, 0o500)
    try:
        with pytest.raises(OSError):
            r.iniciar(r.huella(FILA), "x", None, None)
        assert r.lista() == []
    finally:
        os.chmod(d, 0o700)


def test_sin_poder_escribir_no_se_inicia_la_operacion_simulado(tmp_path, monkeypatch):
    """Igual que el anterior, pero forzando el fallo (vale también como root y en Windows)."""
    r = _reg(tmp_path)

    def falla(*a, **k):
        raise OSError("disco lleno (simulado)")
    monkeypatch.setattr(r, "_guardar", falla)
    with pytest.raises(OSError):
        r.iniciar(r.huella(FILA), "x", None, None)
    assert r.lista() == []


def test_revision_del_operador_desde_otro_proceso_no_se_pierde(tmp_path):
    """Forms (en marcha) y la orden de operador escriben el mismo archivo: lo revisado no vuelve a «incierta»."""
    import subprocess
    forms = _reg(tmp_path)
    op = forms.iniciar(forms.huella(FILA), "x", None, None)
    forms.cerrar(op["id"], INCIERTA, "resultado_desconocido")
    env = dict(os.environ, CAUCE_FORMS_DATA_DIR=str(tmp_path / "datos"), CAUCE_AUDIT_PATH=str(tmp_path / "audit.jsonl"))
    r = subprocess.run([sys.executable, "-m", "cauce_form_connector", "operaciones", "revisar", op["id"], "--nota",
                        "Comprobado en el portal: no consta", "--confirmar", op["id"], "--resultado", "no_recibido"], env=env, capture_output=True,
                       text=True, cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode == 0, r.stderr
    assert forms.get(op["id"])["estado"] == REVISADA          # Forms adopta la versión del operador
    otra = forms.iniciar(forms.huella(dict(FILA, expediente="EXP-0002")), "x", None, None)   # y escribe encima
    assert _reg(tmp_path).get(op["id"])["estado"] == REVISADA
    assert _reg(tmp_path).get(otra["id"]) is not None
    r = subprocess.run([sys.executable, "-m", "cauce_form_connector", "operaciones", "revisar", op["id"], "--nota",
                        "otra vez", "--confirmar", "op_mal"], env=env, capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode == 1 and "No se revisó nada" in r.stderr


def test_pide_revision_no_cuenta_la_operacion_viva_pero_si_la_de_un_forms_anterior(tmp_path):
    r = _reg(tmp_path)
    op = r.iniciar(r.huella(FILA), "x", None, None)
    assert r.hay_abiertas() and not r.pide_revision()         # en curso ahora: aún no hay nada que revisar
    assert _reg(tmp_path).pide_revision()                     # otro arranque la ve como incierta
    r2 = _reg(tmp_path)
    assert r2.get(op["id"])["estado"] == INCIERTA and r2.pide_revision()


def test_la_orden_de_operador_no_convierte_en_incierta_la_operacion_viva_de_forms(tmp_path):
    """Regresión: «operaciones listar» durante un envío cargaba el registro y pasaba la operación VIVA a incierta;
    el envío ya no podía cerrarse como confirmado. Con Forms vivo (dueño), el operador la ve «en curso»."""
    import subprocess
    forms = RegistroOperaciones(tmp_path / "datos", dueno=True)
    op = forms.iniciar(forms.huella(FILA), "x", None, None)
    env = dict(os.environ, CAUCE_FORMS_DATA_DIR=str(tmp_path / "datos"), CAUCE_AUDIT_PATH=str(tmp_path / "audit.jsonl"))
    r = subprocess.run([sys.executable, "-m", "cauce_form_connector", "operaciones", "listar"], env=env,
                       capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode == 0, r.stderr
    assert forms.get(op["id"])["estado"] == EN_CURSO
    forms.cerrar(op["id"], CONFIRMADA, "acuse_verificado", "LAB-00001-ABCD")      # el envío termina bien
    assert forms.get(op["id"])["estado"] == CONFIRMADA
    # Forms se cae con otra a medias (no suelta el candado: lo suelta el sistema al morir; aquí, soltar())
    op2 = forms.iniciar(forms.huella(dict(FILA, expediente="EXP-0002")), "x", None, None)
    forms.soltar()
    assert _reg(tmp_path).get(op2["id"])["estado"] == INCIERTA


def test_dos_forms_dueños_del_mismo_registro_se_detecta(tmp_path):
    a = RegistroOperaciones(tmp_path / "datos", dueno=True)
    b = RegistroOperaciones(tmp_path / "datos", dueno=True)
    assert not a.otro_forms_en_marcha and b.otro_forms_en_marcha
    assert {"event": "operaciones_otro_forms_en_marcha"} in b.eventos
    a.soltar()


def test_confirmadas_que_salen_de_la_lista_siguen_bloqueando(tmp_path, monkeypatch):
    """Revisión final (punto 4): antes, pasadas MAX_CERRADAS operaciones cerradas se BORRABAN las más antiguas,
    también las confirmadas, y ese expediente podía enviarse otra vez. Ahora su huella se archiva y sigue bloqueando."""
    import cauce_form_connector.operaciones as O
    monkeypatch.setattr(O, "MAX_CERRADAS", 2)
    r = _reg(tmp_path)
    filas = [dict(FILA, expediente=f"EXP-{i:04d}") for i in range(5)]
    for f in filas:
        op = r.iniciar(r.huella(f), "x", None, None)
        r.cerrar(op["id"], CONFIRMADA, "acuse_verificado", "LAB-00001-ABCD")
    assert len(r.lista()) == 2                                   # la lista se acota...
    r2 = _reg(tmp_path)                                          # ...y tras reiniciar
    for f in filas:                                              # todas siguen bloqueadas
        with pytest.raises(OperacionBloqueada) as e:
            r2.comprobar(r2.huella(f))
        assert e.value.codigo == "expediente_confirmada"
    doc = json.loads((tmp_path / "datos" / "operaciones.json").read_text())
    assert len(doc["confirmadas_archivadas"]) == 3
    doc["confirmadas_archivadas"] = {}                            # borrar el archivo a mano no lo desbloquea
    (tmp_path / "datos" / "operaciones.json").write_text(json.dumps(doc))
    r3 = _reg(tmp_path)
    assert r3.general is not None
    with pytest.raises(OperacionBloqueada):
        r3.comprobar(r3.huella(filas[0]))


def test_perder_huella_key_no_desactiva_en_silencio_la_proteccion(tmp_path):
    """Revisión final (punto 3): sin la clave, las huellas antiguas no casan. Antes se creaba otra clave en silencio
    y los expedientes ya enviados podían repetirse. Ahora se pide una revisión general y no se envía nada."""
    r = _reg(tmp_path)
    op = r.iniciar(r.huella(FILA), "x", None, None)
    r.cerrar(op["id"], CONFIRMADA, "acuse_verificado", "LAB-00001-ABCD")
    (tmp_path / "datos" / "huella.key").unlink()
    r2 = _reg(tmp_path)
    assert r2.general and r2.general["motivo"] == "clave_de_huella_distinta"
    with pytest.raises(OperacionBloqueada) as e:
        r2.comprobar(r2.huella(FILA))
    assert e.value.codigo == "revision_general_pendiente"
    (tmp_path / "datos" / "huella.key").write_bytes(b"corta")          # dañada: se aparta, no se borra
    r3 = _reg(tmp_path)
    assert list((tmp_path / "datos").glob("huella.key.danada-*"))
    assert r3.general is not None


def test_sin_poder_guardar_la_clave_no_se_envia(tmp_path, monkeypatch):
    r = _reg(tmp_path)
    monkeypatch.setattr(r, "_clave_persistida", False)
    with pytest.raises(OSError):
        r.iniciar(r.huella(FILA), "x", None, None)


@pytest.mark.skipif(sys.platform != "win32", reason="ACL de Windows")
def test_windows_permisos_efectivos_solo_usuario_y_system(tmp_path):
    """Revisión final (punto 3): en Windows 0o600 no protege nada; los permisos reales son la ACL. Se comprueban."""
    import subprocess
    r = RegistroOperaciones(tmp_path / "datos", dueno=True)
    r.iniciar(r.huella(FILA), "x", None, None)
    assert r.proteccion == "acl_usuario"
    for nombre in ("huella.key", "operaciones.json"):
        out = subprocess.run(["icacls", str(tmp_path / "datos" / nombre)], capture_output=True, text=True).stdout
        print(out)
        # lo heredado (I) viene solo de la carpeta de estado, que ya no hereda de arriba: nadie más tiene acceso
        for ajeno in ("Authenticated Users", "BUILTIN\\Users", "Everyone"):
            assert ajeno not in out, out
    r.soltar()
