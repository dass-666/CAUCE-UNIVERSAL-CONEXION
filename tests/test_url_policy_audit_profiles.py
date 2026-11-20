"""Política de URLs, auditoría sin datos sensibles y perfiles reutilizables."""
import json

import pytest

from cauce_form_connector.audit import AuditLog, redact
from cauce_form_connector.config_store import ProfileStore
from cauce_form_connector.html_analyzer import analyze_html
from cauce_form_connector.url_policy import UrlNotAllowed, UrlPolicy, safe_url_label


def test_local_urls_allowed_by_default():
    p = UrlPolicy()
    assert p.check("http://127.0.0.1:8765/lab/x.html", False)
    assert p.check("http://localhost/x", False)
    assert p.check("file:///C:/formularios/x.html", False)


@pytest.mark.parametrize("url,auth", [("https://ejemplo.com/form", True), ("https://ejemplo.com/form", False),
                                      ("javascript:alert(1)", True), ("ftp://x/y", True)])
def test_external_or_strange_urls_rejected(url, auth):
    with pytest.raises(UrlNotAllowed):
        UrlPolicy().check(url, auth)


def test_allowlisted_host_needs_declaration():
    p = UrlPolicy(allowed_hosts={"intranet.ejemplo.com"})
    with pytest.raises(UrlNotAllowed):
        p.check("https://intranet.ejemplo.com/alta", False)
    assert p.check("https://forms.intranet.ejemplo.com/alta", True)
    with pytest.raises(UrlNotAllowed):
        p.check("https://intranet.ejemplo.com.atacante.net/alta", True)


def test_url_label_strips_query_and_fragment():
    assert safe_url_label("https://a.com/form?token=SECRETO#x") == "https://a.com/form"


def test_redaction():
    assert redact("contacto ana@example.com o 55 1234 5678") == "contacto [correo] o [número]"


def test_audit_drops_unknown_keys(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    log.log("x", field_key="f::email", value="ana@example.com", expected="secreto", detail="ana@example.com")
    line = json.loads((tmp_path / "a.jsonl").read_text(encoding="utf-8"))
    assert "value" not in line and "expected" not in line and line["detail"] == "[correo]"


def test_profile_roundtrip_and_reconcile(tmp_path):
    html = "<form id=f><input name=nombre><input name=email type=email></form>"
    a = analyze_html(html)
    store = ProfileStore(tmp_path)
    store.save("Mi formulario", a, {"f::nombre": "Nombre", "f::email": "Correo"})
    assert store.list()[0]["name"] == "Mi formulario"
    prof = store.load("Mi formulario")
    assert "values" not in json.dumps(prof)
    rec = store.reconcile(prof, a)
    assert rec["same_structure"] and rec["mapping"] == {"f::nombre": "Nombre", "f::email": "Correo"}
    # El formulario cambió: desaparece email y aparece telefono
    b = analyze_html("<form id=f><input name=nombre><input name=telefono type=tel></form>")
    rec = store.reconcile(prof, b)
    assert not rec["same_structure"] and rec["missing_fields"] == ["f::email"] and rec["new_fields"] == ["f::telefono"]


def test_profile_name_sanitized(tmp_path):
    a = analyze_html("<form><input name=x></form>")
    p = ProfileStore(tmp_path).save("../../etc/passwd", a, {})
    assert p.parent == tmp_path
