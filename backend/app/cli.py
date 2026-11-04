"""Operator CLI.

    python -m app.cli bootstrap                 # reference data + first admin (idempotent)
    python -m app.cli run --kind demo_seed      # run a pipeline step synchronously
    python -m app.cli run --kind incremental|full|recompute
    python -m app.cli gen-keys                  # print fresh APP_SECRET_KEY / APP_ENCRYPTION_KEY
"""
from __future__ import annotations

import argparse
import base64
import json
import secrets

from cryptography.fernet import Fernet

from app.core.config import get_settings
from app.core.logging import configure_logging


def main() -> None:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap")
    r = sub.add_parser("run")
    r.add_argument("--kind", required=True, choices=["demo_seed", "incremental", "full", "recompute"])
    sub.add_parser("gen-keys")
    args = ap.parse_args()

    if args.cmd == "gen-keys":
        print(f"APP_SECRET_KEY={base64.urlsafe_b64encode(secrets.token_bytes(48)).decode()}")
        print(f"APP_ENCRYPTION_KEY={Fernet.generate_key().decode()}")
        return

    configure_logging(get_settings().log_level)
    if args.cmd == "bootstrap":
        from app.core.db import session_scope
        from app.seed import bootstrap
        with session_scope() as db:
            print(json.dumps(bootstrap.run(db), indent=2))
    elif args.cmd == "run":
        from app.pipeline import runner
        rid = runner.enqueue(args.kind, "cli")
        print(f"run {rid}: {runner.execute(rid)}")


if __name__ == "__main__":
    main()
