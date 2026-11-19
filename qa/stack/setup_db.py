"""(Re)create the QA database `aastar` on the throwaway cluster, migrate, bootstrap, load demo prices and
synthetic sheet data. Run qa/stack/pg_cluster.py start first.

    backend/.venv/Scripts/python qa/stack/setup_db.py

The database is set up like production (deploy/rds/bootstrap_roles.sql, minus role creation). Demo LSEG prices
come from the app's own demo generator; the four Google Sheet sources get the synthetic grids from
backend/tests/test_sheets.py (no network).
"""
from __future__ import annotations

import os
from unittest import mock

import qa_env

qa_env.load()

import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

PORT = os.environ["QA_PG_PORT"]
SUPER = f"postgresql://postgres:{os.environ['QA_PG_SUPERUSER_PASSWORD']}@127.0.0.1:{PORT}"
with psycopg.connect(SUPER + "/postgres", autocommit=True) as c:
    c.execute("DROP DATABASE IF EXISTS aastar WITH (FORCE)")
    c.execute("CREATE DATABASE aastar OWNER aastar_owner ENCODING 'UTF8' TEMPLATE template0")
with psycopg.connect(SUPER + "/aastar", autocommit=True) as c:
    for stmt in ("REVOKE ALL ON DATABASE aastar FROM PUBLIC",
                 "GRANT CONNECT ON DATABASE aastar TO aastar_app, aastar_ro",
                 "GRANT TEMPORARY ON DATABASE aastar TO aastar_app",
                 "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
                 "ALTER DATABASE aastar SET timezone TO 'UTC'",
                 "ALTER ROLE aastar_app SET statement_timeout = '120s'",
                 "ALTER ROLE aastar_ro SET default_transaction_read_only = on"):
        c.execute(stmt)
print("database aastar created (production role layout)")

cfg = Config(str(qa_env.BACKEND / "alembic.ini"))
cfg.set_main_option("script_location", str(qa_env.BACKEND / "migrations"))
command.upgrade(cfg, "head")

from app.core.db import session_scope  # noqa: E402
from app.pipeline import runner  # noqa: E402
from app.seed import bootstrap  # noqa: E402

with session_scope() as s:
    print("bootstrap:", bootstrap.run(s))
rid = runner.enqueue("demo_seed", "cli")
print("demo prices:", runner.execute(rid))

import test_sheets as T  # noqa: E402
from app.sheets import job, sources  # noqa: E402

grids = {
    "argus": {"prices": T._argus_grid(with_us=True)[0]},
    "market_price": {"premiums": T._premiums_grid()},
    "china_parity": T._china_history_grids(),
    "india_parity": {"local": T._india_local_rows(), "dashboard": T._india_dashboard_rows(),
                     "calculation": T._india_calc_grid()},
}
with mock.patch.object(sources, "fetch", lambda src, client: (grids[src.code], "upload")):
    res = job.refresh_sources()
print("sheets:", {k: v["status"] if isinstance(v, dict) else v for k, v in res.items()})
