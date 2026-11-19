"""Write backend/.env.qa for the isolated QA stack (gitignored; test-only secrets generated here, never printed).

The QA database is a throwaway PostgreSQL 18 cluster on 127.0.0.1:5546 (qa/stack/pg_cluster.py), with the same
three roles as production (aastar_owner / aastar_app / aastar_ro) - the API connects as the least-privilege
aastar_app. Integrations are off: no LSEG credentials (demo prices), no Claude key, no Hub SSO, no Google key.

    backend/.venv/Scripts/python qa/stack/make_env.py
"""
from __future__ import annotations

import secrets
import string
from pathlib import Path

from cryptography.fernet import Fernet

BACKEND = Path(__file__).resolve().parents[2] / "backend"
QA_ENV = BACKEND / ".env.qa"
PORT = 5546


def password(prefix: str = "Qa-") -> str:
    alphabet = string.ascii_letters + string.digits
    return prefix + "".join(secrets.choice(alphabet) for _ in range(20)) + "7a"


pg_super, owner_pw, app_pw, ro_pw = (password("Pg") for _ in range(4))
values = {
    "APP_ENV": "prod",                      # production behaviour: no /api/docs
    "APP_BASE_URL": "http://localhost:5181",
    "APP_ALLOWED_ORIGINS": "http://localhost:5181,http://127.0.0.1:5181",
    "APP_RELEASE": "qa",
    "LOG_LEVEL": "INFO",
    "QA_PG_PORT": str(PORT),
    "QA_PG_SUPERUSER_PASSWORD": pg_super,
    "QA_PG_OWNER_PASSWORD": owner_pw,
    "QA_PG_APP_PASSWORD": app_pw,
    "QA_PG_RO_PASSWORD": ro_pw,
    "DATABASE_URL": f"postgresql+psycopg://aastar_app:{app_pw}@127.0.0.1:{PORT}/aastar",
    "MIGRATION_DATABASE_URL": f"postgresql+psycopg://aastar_owner:{owner_pw}@127.0.0.1:{PORT}/aastar",
    "APP_SECRET_KEY": secrets.token_urlsafe(48),
    "APP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "AUTH_LOCAL_ENABLED": "true",
    "BOOTSTRAP_ADMIN_EMAIL": "qa-admin@aastar-qa.local",
    "BOOTSTRAP_ADMIN_PASSWORD": password(),
    "BOOTSTRAP_ADMIN_NAME": "QA Administrator",
    "SSO_ENABLED": "false",
    "LSEG_USERNAME": "",
    "LSEG_PASSWORD": "",
    "ANTHROPIC_API_KEY": "",
    "GOOGLE_SERVICE_ACCOUNT_FILE": "",
    "DEMO_START_DATE": "2024-01-02",
    # Suites sign in many times; the real limit (10/min) is tested separately on a freshly started API.
    "LOGIN_RATE_PER_MINUTE": "1000",
    "WEB_CONCURRENCY": "1",
    "ALLOW_DEMO_DATA": "true",          # production behaviour otherwise; the QA copy runs on demo prices
}
QA_ENV.write_text("# QA stack only - throwaway values, gitignored. Written by qa/stack/make_env.py\n"
                  + "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
print(f"wrote {QA_ENV} ({len(values)} settings; admin {values['BOOTSTRAP_ADMIN_EMAIL']}, passwords in the file)")
