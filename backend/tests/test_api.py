"""API integration tests: auth, CSRF, RBAC, user administration, audit, market data, pipeline."""
from __future__ import annotations

import pytest
from conftest import login


def test_requires_session(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/market/catalog").status_code == 401
    assert client.get("/api/admin/users").status_code == 401


def test_bad_origin_rejected(client):
    r = client.post("/api/auth/login", json={"email": "x@y.z", "password": "p"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"


def test_generic_login_error(client):
    a = client.post("/api/auth/login", json={"email": "nobody@test.local", "password": "x"}).json()
    b = client.post("/api/auth/login", json={"email": "admin@test.local", "password": "x"}).json()
    assert a["error"]["code"] == b["error"]["code"] == "invalid_credentials"


def test_csrf_required(admin_client):
    token = admin_client.headers.pop("X-CSRF-Token")
    r = admin_client.post("/api/admin/pipeline/run", json={"kind": "recompute"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    admin_client.headers["X-CSRF-Token"] = token


def test_user_lifecycle_and_rbac(admin_client, client):
    r = admin_client.post("/api/admin/users", json={"email": "Viewer.One@aastar-test.com", "display_name": "Viewer One",
                                                    "role": "viewer", "auth_provider": "local",
                                                    "overrides": [{"module": "crush", "effect": "grant"},
                                                                  {"module": "parity", "effect": "deny"}]})
    assert r.status_code == 201, r.text
    body = r.json()
    temp, uid = body["temporary_password"], body["user"]["id"]
    assert temp and body["user"]["email"] == "viewer.one@aastar-test.com"
    assert "crush" in body["user"]["effective_modules"] and "parity" not in body["user"]["effective_modules"]
    assert admin_client.post("/api/admin/users", json={"email": "viewer.one@aastar-test.com", "display_name": "dup",
                                                       "role": "viewer"}).status_code == 409

    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app, base_url="http://testserver", headers={"Origin": "http://testserver"}) as v:
        r = login(v, "viewer.one@aastar-test.com", temp)
        assert r.status_code == 200 and "must_change_password" not in r.json()
        assert v.get("/api/market/catalog").status_code == 200          # no forced password change any more
        r = v.post("/api/auth/change-password", json={"current_password": temp, "new_password": "weak"})
        assert r.status_code == 422                                      # voluntary change still checks the policy
        r = v.post("/api/auth/change-password", json={"current_password": temp,
                                                      "new_password": "Viewer-Strong-Pass-7"})
        assert r.status_code == 200, r.text
        v.headers["X-CSRF-Token"] = v.cookies.get("aastar_csrf")
        mods = [m["code"] for m in v.get("/api/market/catalog").json()["modules"]]
        assert "crush" in mods and "parity" not in mods and "oi_volume" not in mods
        assert v.get("/api/market/sections/parity").status_code == 403
        assert v.get("/api/market/series/MALINDO_CPO").status_code == 403     # IDOR on series key
        assert v.get("/api/market/sections/crush").status_code == 200
        assert v.get("/api/market/series/CRUSH_c3/export.csv").status_code == 403  # no data.export
        assert v.get("/api/admin/users").status_code == 403

    # disable → sessions revoked, login refused
    assert admin_client.patch(f"/api/admin/users/{uid}", json={"status": "disabled"}).status_code == 200
    assert login(client, "viewer.one@aastar-test.com", "Viewer-Strong-Pass-7").status_code == 401
    actions = {a["action"] for a in admin_client.get("/api/admin/audit?page_size=200").json()["items"]}
    assert {"USER_CREATE", "LOGIN_SUCCESS", "PASSWORD_CHANGE", "USER_DISABLE"} <= actions


def test_lockout(admin_client, client):
    r = admin_client.post("/api/admin/users", json={"email": "lock@aastar-test.com", "display_name": "Lock",
                                                    "role": "analyst", "password": "Analyst-Pass-12345"})
    uid = r.json()["user"]["id"]
    for _ in range(5):
        assert login(client, "lock@aastar-test.com", "wrong-password").status_code == 401
    assert login(client, "lock@aastar-test.com", "Analyst-Pass-12345").status_code == 423
    assert admin_client.post(f"/api/admin/users/{uid}/unlock").status_code == 200
    assert login(client, "lock@aastar-test.com", "Analyst-Pass-12345").status_code == 200


def test_last_admin_and_self_protection(admin_client):
    me = admin_client.get("/api/auth/me").json()
    r = admin_client.patch(f"/api/admin/users/{me['id']}", json={"status": "disabled"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "self_disable"
    r = admin_client.patch(f"/api/admin/users/{me['id']}", json={"role": "viewer"})
    assert r.status_code == 422


def test_market_endpoints(admin_client):
    years = admin_client.get("/api/market/years").json()
    assert years["years"] and len(years["default"]) <= 3
    sec = admin_client.get("/api/market/sections/calendar_spreads?group=Futures&unit=local").json()
    assert len(sec["series"]) == 15
    s = sec["series"][0]
    assert s["key"] == "FCPO_c1c3" and s["unit"] == "MYR/MT" and s["traces"][0]["x"][0].startswith("2000-")
    ps = admin_client.get("/api/market/sections/product_spreads?group=Futures&tenor=c7").json()
    assert all(x["key"].endswith("_c7") for x in ps["series"])
    fc = admin_client.get("/api/market/forward-curves?kind=outright&snapshot=06-30").json()
    assert len(fc["curves"]) == 10 and fc["curves"][0]["traces"][0]["x"][:3] == ["c1", "c2", "c3"]
    oi = admin_client.get("/api/market/oi-cumulative?days=30").json()
    assert [t["product"] for t in oi["tables"]][:3] == ["FCPO", "BO", "LGO"] and len(oi["tables"][0]["rows"]) == 30
    ta = admin_client.get("/api/market/ta/FCPOc3?start_year=2025").json()
    assert ta["levels"] and len(ta["price"]) == len(ta["dates"])
    ov = admin_client.get("/api/market/overview").json()
    assert ov["tiles"][0]["ric"] == "FCPOc3" and ov["tiles"][0]["value"] > 0
    csv = admin_client.get("/api/market/series/BOPO_c3/export.csv")
    assert csv.status_code == 200 and csv.text.startswith("date,")
    assert admin_client.get("/api/market/sections/nope").status_code == 404
    assert admin_client.get("/api/market/sections/crush?years=abc").status_code == 422


def test_pipeline_queue(admin_client):
    r = admin_client.post("/api/admin/pipeline/run", json={"kind": "recompute"})
    assert r.status_code == 202
    assert admin_client.post("/api/admin/pipeline/run", json={"kind": "recompute"}).status_code == 409
    from app.pipeline import runner
    assert runner.execute(runner.next_queued()) == "succeeded"
    runs = admin_client.get("/api/admin/pipeline/runs").json()["items"]
    detail = admin_client.get(f"/api/admin/pipeline/runs/{runs[0]['id']}").json()
    assert [s["step"] for s in detail["steps"]] == ["compute_series", "compute_oi", "quality_checks", "publish"]
    assert detail["steps"][0]["metrics"]["points_changed"] == 0          # idempotent recompute


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-request-id"]


def test_hub_tile_launch_restarts_app_initiated_sso(client):
    # The Hub's tile launch arrives with its own state and the PKCE verifier in the URL; we never redeem it.
    r = client.get("/api/auth/sso/callback?code=hubcode&state=hubstate&code_verifier=leaked",
                   follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/api/auth/sso/login"
    assert "aastar_session" not in r.headers.get("set-cookie", "")


def test_unknown_state_without_verifier_is_rejected(client):
    r = client.get("/api/auth/sso/callback?code=x&state=not-ours", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login?error=sso_state_invalid"


def test_sso_email_link_requires_email_verified(client):
    # Hub SSO v2 §4: an existing AASTAR user is bound to a Hub sub by email only when email_verified is true.
    import uuid

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Role, User
    from app.services import sso
    from app.services.audit import RequestMeta

    db = SessionLocal()
    try:
        email = f"sso.{uuid.uuid4().hex[:8]}@aastar-test.com"
        viewer = db.scalar(select(Role).where(Role.code == "viewer"))
        db.add(User(email=email, display_name="SSO Test", role_id=viewer.id, auth_provider="sso"))
        db.commit()
        meta = RequestMeta(ip=None, user_agent="pytest")
        sub = str(uuid.uuid4())
        with pytest.raises(sso.SsoError) as exc:
            sso._resolve_user(db, {"sub": sub, "email": email, "email_verified": False}, meta)
        assert exc.value.code == "sso_email_unverified"
        assert db.scalar(select(User.hub_sub).where(User.email == email)) is None
        user = sso._resolve_user(db, {"sub": sub, "email": email, "email_verified": True}, meta)
        db.commit()
        assert user.hub_sub == sub
        # Once linked, sub alone identifies the user (no email_verified needed).
        assert sso._resolve_user(db, {"sub": sub, "email": email}, meta).id == user.id
    finally:
        db.close()


def test_earlier_password_can_be_reused_but_not_the_current_one(client):
    # The "not one of your last 5 passwords" rule was removed; only "differs from the current one" remains.
    import uuid

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.core.errors import ApiError
    from app.models import Role, User
    from app.services import auth
    from app.services.audit import RequestMeta

    first, second = "Kelapa-Sawit-2026!", "Minyak-Kedelai-2027!"
    db = SessionLocal()
    try:
        viewer = db.scalar(select(Role).where(Role.code == "viewer"))
        user = User(email=f"pw.{uuid.uuid4().hex[:8]}@aastar-test.com", display_name="PW Test", role_id=viewer.id,
                    auth_provider="local")
        db.add(user)
        auth.set_password(db, user, first)
        db.commit()
        meta = RequestMeta(ip=None, user_agent="pytest")
        auth.change_password(db, user, first, second, None, meta)
        auth.change_password(db, user, second, first, None, meta)    # back to an earlier password: allowed
        with pytest.raises(ApiError) as exc:
            auth.change_password(db, user, first, first, None, meta)
        assert exc.value.code == "password_reused"
    finally:
        db.close()
