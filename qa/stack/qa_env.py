"""Load backend/.env.qa into os.environ (overriding backend/.env) before anything imports app.*."""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def load() -> None:
    env = BACKEND / ".env.qa"
    if not env.exists():
        raise SystemExit("backend/.env.qa missing - run qa/stack/make_env.py first")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    sys.path.insert(0, str(BACKEND / "tests"))
