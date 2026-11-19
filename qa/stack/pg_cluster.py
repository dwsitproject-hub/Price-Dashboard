"""Throwaway PostgreSQL 18 cluster for the QA stack: init (once), start, and create the app's three roles.

    backend/.venv/Scripts/python qa/stack/pg_cluster.py start     # init if needed, start on 127.0.0.1:5546
    backend/.venv/Scripts/python qa/stack/pg_cluster.py stop
    backend/.venv/Scripts/python qa/stack/pg_cluster.py destroy   # stop and delete the data directory

Uses the native install (C:\\Program Files\\PostgreSQL\\18 or PG_BIN). Data lives in QA_PG_DATA (default: the
user's temp folder), never in the repo. Listens on loopback only, scram-sha-256 authentication.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import qa_env

qa_env.load()

PG_BIN = Path(os.environ.get("PG_BIN", r"C:\Program Files\PostgreSQL\18\bin"))
DATA = Path(os.environ.get("QA_PG_DATA", Path(tempfile.gettempdir()) / "aastar-qa-pg18"))
PORT = os.environ["QA_PG_PORT"]
SUPER_PW = os.environ["QA_PG_SUPERUSER_PASSWORD"]


def run(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(PG_BIN / args[0]), *args[1:]], check=True, **kw)


def psql(sql: str, db: str = "postgres") -> None:
    env = {**os.environ, "PGPASSWORD": SUPER_PW}
    run("psql", "-h", "127.0.0.1", "-p", PORT, "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1", "-q",
        "-c", sql, env=env)


def start() -> None:
    if not (DATA / "PG_VERSION").exists():
        DATA.mkdir(parents=True, exist_ok=True)
        pwfile = DATA.parent / "aastar-qa-pgpw.txt"
        pwfile.write_text(SUPER_PW, encoding="utf-8")
        try:
            run("initdb", "-D", str(DATA), "-U", "postgres", "-A", "scram-sha-256", f"--pwfile={pwfile}",
                "-E", "UTF8", "--locale=C", stdout=subprocess.DEVNULL)
        finally:
            pwfile.unlink(missing_ok=True)
    if not running():
        # The server inherits our handles; without DEVNULL the call would wait for the server to exit.
        run("pg_ctl", "-D", str(DATA), "-l", str(DATA / "server.log"), "-w",
            "-o", f"-p {PORT} -c listen_addresses=127.0.0.1", "start",
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for role, key, limit in (("aastar_owner", "QA_PG_OWNER_PASSWORD", -1), ("aastar_app", "QA_PG_APP_PASSWORD", 40),
                             ("aastar_ro", "QA_PG_RO_PASSWORD", 5)):
        pw = os.environ[key]
        psql(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
             f"CREATE ROLE {role} LOGIN; END IF; END $$; "
             f"ALTER ROLE {role} LOGIN PASSWORD '{pw}' CONNECTION LIMIT {limit};")
    print(f"QA PostgreSQL running on 127.0.0.1:{PORT} (data {DATA})")


def running() -> bool:
    """pg_ctl status: 0 = running (a stale postmaster.pid after a kill is not 'running')."""
    return subprocess.run([str(PG_BIN / "pg_ctl"), "-D", str(DATA), "status"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def stop() -> None:
    if running():
        run("pg_ctl", "-D", str(DATA), "-m", "fast", "stop")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "destroy":
        stop()
        shutil.rmtree(DATA, ignore_errors=True)
        print(f"removed {DATA}")
    else:
        raise SystemExit("usage: pg_cluster.py start|stop|destroy")
