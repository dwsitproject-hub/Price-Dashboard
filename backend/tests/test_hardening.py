"""Production-readiness fixes (qa/architecture findings): job recovery, no synthetic prices in production,
error envelope on routing errors, business dates, read-only role grants."""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text

from app.core.db import get_engine


def _report(c, label: str) -> str:
    return c.execute(text(
        "INSERT INTO intel.ai_reports (instrument, instrument_label, timeframe, sections, raw_text) "
        "VALUES ('FCPOc3', :l, '1Y', '{}'::jsonb, 'x') RETURNING id"), {"l": label}).scalar_one()


def test_recover_interrupted_fails_orphaned_reports_only():
    from app.intel import jobs
    with get_engine().begin() as c:
        orphan = _report(c, "qa-orphan")
        waiting = _report(c, "qa-waiting")
        c.execute(text("INSERT INTO ops.jobs (kind, status, params) VALUES ('ai_narrative', 'queued', "
                       "CAST(:p AS jsonb))"), {"p": json.dumps({"report_id": str(waiting)})})
    jobs.recover_interrupted()
    with get_engine().begin() as c:
        st = dict(c.execute(text("SELECT id, status FROM intel.ai_reports WHERE id IN (:a, :b)"),
                            {"a": orphan, "b": waiting}).all())
        c.execute(text("DELETE FROM ops.jobs WHERE params->>'report_id' = :w"), {"w": str(waiting)})
        c.execute(text("DELETE FROM intel.ai_reports WHERE id IN (:a, :b)"), {"a": orphan, "b": waiting})
    assert st[orphan] == "failed"          # its job is gone: it would show "pending" forever
    assert st[waiting] == "pending"        # its job is still queued: it will run


def test_production_refuses_synthetic_prices(monkeypatch):
    from app.core.config import get_settings
    from app.pipeline import runner
    s = get_settings()
    monkeypatch.setattr(s, "app_env", "prod")
    monkeypatch.setattr(s, "allow_demo_data", False)
    assert not s.lseg_configured
    rid = runner.enqueue("incremental", "cli")
    assert runner.execute(rid) == "failed"
    with get_engine().connect() as c:
        err = c.execute(text("SELECT error FROM ops.pipeline_runs WHERE id = :id"), {"id": rid}).scalar()
        demo_rows = c.execute(text("SELECT count(*) FROM ops.pipeline_run_steps WHERE run_id = :id "
                                   "AND step = 'demo_generate'"), {"id": rid}).scalar()
    assert "LSEG credentials are not configured" in err and demo_rows == 0


def test_routing_errors_use_the_error_envelope(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.put("/api/health")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


def test_business_today_uses_the_desk_time_zone():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.core.clock import business_today
    assert business_today() == datetime.now(ZoneInfo("Asia/Jakarta")).date()
    assert isinstance(business_today(), date)


def test_readonly_role_cannot_read_audit_or_tokens():
    with get_engine().connect() as c:
        if not c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'aastar_ro'")).scalar():
            return
        rows = c.execute(text(
            "SELECT t, has_table_privilege('aastar_ro', t, 'SELECT') FROM unnest(ARRAY["
            "'ops.audit_logs','ops.integration_tokens','ops.integration_secrets','market.series_points']) t")).all()
    priv = dict(rows)
    assert priv["ops.audit_logs"] is False and priv["ops.integration_tokens"] is False
    assert priv["ops.integration_secrets"] is False and priv["market.series_points"] is True


# ── functional-review fixes (qa/functional/findings.md) ──────────────────────────────────────────────────────

def _plant(**kw):
    p = {"name": "QA Plant", "company": "QA", "country": "Nowhere", "lat": 1.5, "lon": 103.8, "status": "op",
         "type": "standalone", "kt": 100}
    p.update(kw)
    return p


def test_atlas_rejects_bad_plants_with_422(admin_client):
    plants = admin_client.get("/api/intel/atlas").json()["plants"]
    for bad in (_plant(name="N" * 201), _plant(kt=1e12), _plant(safkt="Infinity"), _plant(kt="NaN"),
                _plant(kt=-5), _plant(name=None), _plant(country=None)):
        r = admin_client.put("/api/intel/atlas", json={"plants": plants + [bad]})
        assert r.status_code == 422 and r.json()["error"]["code"] == "bad_atlas", (bad, r.text)
    assert len(admin_client.get("/api/intel/atlas").json()["plants"]) == len(plants)


def test_user_display_name_is_stripped_and_sso_is_the_default(admin_client):
    r = admin_client.post("/api/admin/users", json={"email": "qa-blank@aastar-test.com", "display_name": "   ",
                                                    "role": "viewer"})
    assert r.status_code == 422
    r = admin_client.post("/api/admin/users", json={"email": "qa-ssodefault@aastar-test.com",
                                                    "display_name": "  SSO Default  ", "role": "viewer"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["auth_provider"] == "sso" and body["user"]["display_name"] == "SSO Default"
    assert not body.get("temporary_password")


def test_unsafe_request_without_origin_is_refused(admin_client):
    origin = admin_client.headers.pop("Origin")
    try:
        r = admin_client.put("/api/admin/settings", json={"values": {}})
        assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
        r = admin_client.put("/api/admin/settings", json={"values": {}}, headers={"Referer": origin + "/admin/settings"})
        assert r.status_code == 200          # the Referer's origin is accepted when Origin is absent
    finally:
        admin_client.headers["Origin"] = origin


def test_impossible_snapshot_day_is_refused(admin_client):
    assert admin_client.get("/api/market/forward-curves?snapshot=04-31").status_code == 422
    assert admin_client.get("/api/market/forward-curves?snapshot=02-29").status_code == 200
    assert admin_client.get("/api/sheets/waste-bio/curves/calendar?snapshot=06-31").status_code == 422


def test_reprocess_unknown_document_is_404(admin_client):
    r = admin_client.post("/api/intel/news/documents/0190a000-0000-7000-8000-000000000000/reprocess")
    assert r.status_code == 404


def test_audit_target_filter(admin_client):
    r = admin_client.get("/api/admin/audit?target=user&page_size=200")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items and all(a["target_type"] == "user" or "user" in (a["target_id"] or "") for a in items)
