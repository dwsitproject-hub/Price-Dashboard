"""Test setup: an isolated database `aastar_test` on the same PostgreSQL 18 server, migrated and seeded.

Requires MIGRATION_DATABASE_URL (a role that can CREATE DATABASE) in backend/.env or the environment.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))


def _env_file_value(key: str) -> str | None:
    env = BACKEND / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


_admin_url = os.environ.get("MIGRATION_DATABASE_URL") or _env_file_value("MIGRATION_DATABASE_URL")
if not _admin_url:
    raise RuntimeError("Set MIGRATION_DATABASE_URL to run the tests.")
_base, _ = _admin_url.rsplit("/", 1)
TEST_DB_URL = f"{_base}/aastar_test"

os.environ.update({
    "DATABASE_URL": TEST_DB_URL, "MIGRATION_DATABASE_URL": TEST_DB_URL, "APP_ENV": "local",
    "BOOTSTRAP_ADMIN_EMAIL": "admin@test.local", "BOOTSTRAP_ADMIN_PASSWORD": "Adm1n!Passw0rd#",
    "DEMO_START_DATE": "2025-06-02", "LSEG_USERNAME": "", "LSEG_PASSWORD": "", "SSO_ENABLED": "false",
    "APP_ALLOWED_ORIGINS": "http://testserver,http://localhost:5181", "LOGIN_RATE_PER_MINUTE": "1000",
})


@pytest.fixture(scope="session", autouse=True)
def database():
    import psycopg
    from alembic import command
    from alembic.config import Config

    plain = _admin_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(plain, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS aastar_test WITH (FORCE)")
        c.execute("CREATE DATABASE aastar_test")
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")

    from app.core.db import session_scope
    from app.pipeline import runner
    from app.seed import bootstrap
    with session_scope() as db:
        bootstrap.run(db)
    rid = runner.enqueue("demo_seed", "cli")
    assert runner.execute(rid) == "succeeded"
    yield TEST_DB_URL


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.core.ratelimit import limiter
    from app.main import app
    limiter.reset()
    with TestClient(app, base_url="http://testserver") as c:
        c.headers["Origin"] = "http://testserver"
        yield c


def login(client, email: str, password: str):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    if r.status_code == 200:
        client.headers["X-CSRF-Token"] = client.cookies.get("aastar_csrf")
    return r


@pytest.fixture()
def admin_client(client):
    from app.services import settings as rs
    rs.invalidate()
    r = login(client, "admin@test.local", "Adm1n!Passw0rd#")
    assert r.status_code == 200, r.text
    return client
