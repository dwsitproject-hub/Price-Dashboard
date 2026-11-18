"""/api/health — liveness and readiness (PRD AVL-04)."""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import APP_VERSION, BACKEND_DIR
from app.core.db import get_engine

router = APIRouter(prefix="/health", tags=["health"])


@lru_cache(maxsize=1)
def expected_revision() -> str:
    """The head of the migrations shipped with this code: ready only once the database is migrated to it."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


@router.get("")
def liveness() -> dict:
    return {"status": "ok", "version": APP_VERSION}


@router.get("/ready")
def readiness():
    try:
        with get_engine().connect() as c:
            rev = c.execute(text("SELECT version_num FROM public.alembic_version")).scalar()
        ok = rev == expected_revision()
        return JSONResponse(status_code=200 if ok else 503,
                            content={"status": "ready" if ok else "migrations_pending", "db": "ok",
                                     "revision": rev})
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable", "db": "error"})
