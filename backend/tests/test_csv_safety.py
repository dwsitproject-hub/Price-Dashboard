"""CSV exports neutralise spreadsheet formulas (pentest PT-INJ-04)."""
from __future__ import annotations

import csv
import io

from app.core import csvsafe


def test_cell_neutralises_formula_text_but_not_numbers():
    assert csvsafe.cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    for lead in ("+1", "-1+2", "@SUM(A1)", "\tx", "\rx"):
        assert csvsafe.cell(lead) == "'" + lead
    assert csvsafe.cell(-12.5) == -12.5            # negative spreads stay numeric
    assert csvsafe.cell("2026-01-02") == "2026-01-02"
    assert csvsafe.cell(None) is None


def test_audit_export_neutralises_failed_login_email(client, admin_client):
    payload = "=2+5+qa-csv-injection"
    r = client.post("/api/auth/login", json={"email": payload, "password": "wrong-password-123"})
    assert r.status_code == 401
    r = admin_client.get("/api/admin/audit/export.csv?action=LOGIN_FAILED")
    assert r.status_code == 200
    cells = [row[2] for row in csv.reader(io.StringIO(r.text))][1:]
    assert "'" + payload in cells
    assert payload not in cells
