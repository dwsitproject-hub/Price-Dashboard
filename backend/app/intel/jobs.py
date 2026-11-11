"""Background jobs (ops.jobs) for the intelligence sections, drained by the worker next to the price pipeline.

Handlers are registered by kind and return a small JSON-able result dict; any exception marks the job failed
with its message. A separate advisory lock from the price pipeline lets both run at once but keeps a second
worker process from draining the same queue.
"""
from __future__ import annotations

import json
import logging
import traceback
import uuid
from typing import Callable

from sqlalchemy import text

from app.core.db import get_engine

logger = logging.getLogger("app.jobs")

LOCK_KEY = 42002
KINDS = ("cftc_refresh", "calendar_refresh", "document_extract", "ai_narrative")
Handler = Callable[[dict], dict]
_handlers: dict[str, Handler] = {}


def register(kind: str):
    def deco(fn: Handler) -> Handler:
        _handlers[kind] = fn
        return fn
    return deco


def _load_handlers() -> None:
    # Imported for their @register side effect; done lazily to avoid import cycles.
    from app.intel import ai, calendar, cftc, news  # noqa: F401


def enqueue(kind: str, trigger: str = "manual", params: dict | None = None, user_id=None) -> uuid.UUID:
    """Queue a job, or return the id of an identical one that is already queued or running."""
    if kind not in KINDS:
        raise ValueError(kind)
    p = json.dumps(params or {}, sort_keys=True)
    with get_engine().begin() as c:
        existing = c.execute(text("SELECT id FROM ops.jobs WHERE kind = :k AND params = CAST(:p AS jsonb) "
                                  "AND status IN ('queued','running') ORDER BY queued_at LIMIT 1"),
                             {"k": kind, "p": p}).scalar()
        if existing:
            return existing
        return c.execute(text("INSERT INTO ops.jobs (kind, trigger, params, requested_by) "
                              "VALUES (:k, :t, CAST(:p AS jsonb), :u) RETURNING id"),
                         {"k": kind, "t": trigger, "p": p, "u": user_id}).scalar_one()


def _claim_next() -> tuple[uuid.UUID, str, dict] | None:
    with get_engine().begin() as c:
        row = c.execute(text(
            "UPDATE ops.jobs SET status = 'running', started_at = now() WHERE id = ("
            "  SELECT id FROM ops.jobs WHERE status = 'queued' ORDER BY queued_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, kind, params")).first()
    return (row.id, row.kind, row.params or {}) if row else None


def _finish(job_id, status: str, result: dict | None = None, error: str | None = None) -> None:
    with get_engine().begin() as c:
        c.execute(text("UPDATE ops.jobs SET status = :s, result = CAST(:r AS jsonb), error = :e, finished_at = now() "
                       "WHERE id = :id"),
                  {"s": status, "r": json.dumps(result) if result is not None else None, "e": error, "id": job_id})


def run_one(job_id, kind: str, params: dict) -> str:
    _load_handlers()
    handler = _handlers.get(kind)
    try:
        if handler is None:
            raise RuntimeError(f"No handler for job kind {kind!r}")
        result = handler(params)
        _finish(job_id, "succeeded", result=result)
        logger.info("job %s (%s) succeeded: %s", job_id, kind, result)
        return "succeeded"
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job
        msg = f"{exc}"[:2000] or type(exc).__name__
        _finish(job_id, "failed", error=msg)
        logger.error("job %s (%s) failed: %s\n%s", job_id, kind, msg, traceback.format_exc(limit=5))
        return "failed"


def drain(max_jobs: int = 50) -> int:
    """Run queued jobs one after another. Returns how many ran (0 if another worker holds the lock)."""
    conn = get_engine().connect()
    try:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
            conn.commit()
            return 0
        conn.commit()
        n = 0
        while n < max_jobs and (job := _claim_next()) is not None:
            run_one(*job)
            n += 1
        return n
    finally:
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            conn.commit()
        finally:
            conn.close()


def recover_interrupted() -> int:
    with get_engine().begin() as c:
        if not c.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY}).scalar():
            return 0
        return c.execute(text("UPDATE ops.jobs SET status = 'failed', finished_at = now(), "
                              "error = 'Interrupted (worker restarted)' WHERE status = 'running'")).rowcount or 0


def latest(kind: str) -> dict | None:
    with get_engine().connect() as c:
        r = c.execute(text("SELECT id, kind, status, trigger, result, error, queued_at, started_at, finished_at "
                           "FROM ops.jobs WHERE kind = :k ORDER BY queued_at DESC LIMIT 1"), {"k": kind}).mappings().first()
    return dict(r) if r else None
