"""/api/health — liveness and readiness (PRD AVL-04)."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import APP_VERSION
from app.core.db import get_engine

router = APIRouter(prefix="/health", tags=["health"])
EXPECTED_REVISION = "0004_app_temp_privilege"


@router.get("")
def liveness() -> dict:
    return {"status": "ok", "version": APP_VERSION}


@router.get("/ready")
def readiness():
    try:
        with get_engine().connect() as c:
            rev = c.execute(text("SELECT version_num FROM public.alembic_version")).scalar()
        ok = rev == EXPECTED_REVISION
        return JSONResponse(status_code=200 if ok else 503,
                            content={"status": "ready" if ok else "migrations_pending", "db": "ok",
                                     "revision": rev})
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable", "db": "error"})
