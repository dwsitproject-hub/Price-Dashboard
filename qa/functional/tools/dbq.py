"""Read-only query helper for the QA database (credentials read from backend/.env.qa, never printed).

Usage: backend/.venv/Scripts/python qa/functional/tools/dbq.py "SELECT ..." ["SELECT ..."]
"""
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
env = {}
for line in (ROOT / "backend" / ".env.qa").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

engine = sa.create_engine(env["DATABASE_URL"])
with engine.connect() as c:
    c.execute(sa.text("SET TRANSACTION READ ONLY"))
    for q in sys.argv[1:]:
        print(">>", q)
        for r in c.execute(sa.text(q)).all():
            print(tuple(r))
