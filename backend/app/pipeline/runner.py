"""Pipeline run execution: steps, single-run advisory lock, data-version publish (TD §6.1)."""
from __future__ import annotations

import logging
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text

from app.catalog.instruments import PRODUCTS, all_rics, futures_ric
from app.core.config import get_settings
from app.core.db import SessionLocal, get_engine
from app.engine import oi as oi_engine
from app.engine.conversions import build_frames
from app.engine.spreads import compute_all
from app.integrations.lseg.client import LsegClient
from app.models import DqFinding, PipelineRun, PipelineRunStep
from app.pipeline import quality, store
from app.seed import demo_data
from app.services import settings as runtime_settings

logger = logging.getLogger("app.pipeline")
LOCK_KEY = 42_001
CRITICAL = {"lseg_auth", "lseg_fetch", "demo_generate", "compute_series", "compute_oi", "publish"}


class StepFailed(Exception):
    pass


class RunContext:
    def __init__(self, run_id: uuid.UUID):
        self.run_id = run_id
        self.seq = 0
        self.warnings = 0
        self.noncritical_failed = False

    @contextmanager
    def step(self, name: str):
        self.seq += 1
        db = SessionLocal()
        row = PipelineRunStep(run_id=self.run_id, seq=self.seq, step=name, status="running",
                              started_at=datetime.now(UTC), metrics={})
        db.add(row)
        db.commit()
        t0 = time.perf_counter()
        holder: dict = {}
        try:
            yield holder
            row.status = "succeeded"
            row.message = holder.get("message")
        except Exception as exc:
            row.status = "failed"
            row.message = f"{type(exc).__name__}: {exc}"[:2000]
            logger.error("step %s failed: %s", name, exc)
            if name in CRITICAL:
                row.finished_at = datetime.now(UTC)
                row.metrics = {**holder.get("metrics", {}), "seconds": round(time.perf_counter() - t0, 2)}
                db.commit()
                db.close()
                raise StepFailed(row.message) from exc
            self.noncritical_failed = True
        row.finished_at = datetime.now(UTC)
        row.metrics = {**holder.get("metrics", {}), "seconds": round(time.perf_counter() - t0, 2)}
        db.commit()
        db.close()


def _update_run(run_id: uuid.UUID, **fields) -> None:
    db = SessionLocal()
    run = db.get(PipelineRun, run_id)
    for k, v in fields.items():
        setattr(run, k, v)
    db.commit()
    db.close()


def _fetch_and_store(ctx: RunContext, kind: str, demo: bool) -> int:
    s = get_settings()
    total_fetched = total_upserted = 0
    if demo:
        with ctx.step("demo_generate") as st:
            rows = demo_data.generate_rows(date.fromisoformat(s.demo_start_date), date.today())
            st["metrics"] = {"rows": len(rows)}
            st["message"] = "Synthetic demo data (no LSEG credentials configured)."
        with ctx.step("store_prices") as st:
            n = store.upsert_prices(store.normalise_lseg_rows(rows), "demo", ctx.run_id)
            st["metrics"] = {"upserted": n}
        _update_run(ctx.run_id, rows_fetched=len(rows), rows_upserted=n)
        return n

    db = SessionLocal()
    client = LsegClient(db)
    try:
        with ctx.step("lseg_auth"):
            client.token()
        today = date.today()
        if kind == "full":
            start = date.fromisoformat(s.lseg_history_start)
        else:
            start = today - timedelta(days=s.lseg_lookback_days)
        with ctx.step("lseg_fetch") as st:
            chunks = 0
            for rows in client.extract_chunked(all_rics(), start, today):
                chunks += 1
                norm = store.normalise_lseg_rows(rows)
                total_fetched += len(norm)
                total_upserted += store.upsert_prices(norm, "lseg", ctx.run_id)
            st["metrics"] = {"chunks": chunks, "fetched": total_fetched, "upserted": total_upserted,
                             "start": start.isoformat()}
            if total_fetched == 0:
                st["message"] = "No rows for the window (holiday stretch?) — existing data kept."
    finally:
        client.close()
        db.close()
    _update_run(ctx.run_id, rows_fetched=total_fetched, rows_upserted=total_upserted)
    return total_upserted


def _compute(ctx: RunContext) -> None:
    with ctx.step("compute_series") as st:
        long_df = store.load_prices_long()
        if long_df.empty:
            raise RuntimeError("market.price_daily is empty — nothing to compute.")
        frames = build_frames(long_df)
        series = compute_all(frames)
        upserted, deleted = store.write_series(series)
        st["metrics"] = {"series": len(series), "points_changed": upserted, "points_deleted": deleted,
                         "price_rows": len(long_df)}
        _update_run(ctx.run_id, series_computed=len(series))
    with ctx.step("compute_oi") as st:
        oi_rics = [futures_ric(p.code, c) for p in PRODUCTS.values() for c in range(1, p.oi_contracts + 1)]
        oi_long = store.load_prices_long(oi_rics, with_oi=True)
        cum = oi_engine.build_cumulative(oi_long)
        thr = oi_engine.build_arrow_thresholds(cum)
        n = store.write_oi(cum, thr)
        st["metrics"] = {"rows": n, "products": int(cum["product_code"].nunique()) if not cum.empty else 0}
    with ctx.step("quality_checks") as st:
        findings = quality.run_checks(long_df, cum)
        db = SessionLocal()
        for f in findings:
            db.add(DqFinding(run_id=ctx.run_id, **f))
        db.commit()
        db.close()
        ctx.warnings = sum(1 for f in findings if f["severity"] in ("warn", "error"))
        st["metrics"] = {"findings": len(findings), "warnings": ctx.warnings}


def _publish(ctx: RunContext) -> int:
    with ctx.step("publish") as st:
        with get_engine().begin() as c:
            v = c.execute(text("SELECT nextval('ops.data_version_seq')")).scalar_one()
            c.execute(text("UPDATE ops.pipeline_runs SET data_version = :v WHERE id = :id"),
                      {"v": v, "id": ctx.run_id})
        st["metrics"] = {"data_version": int(v)}
        return int(v)


def execute(run_id: uuid.UUID) -> str:
    """Execute one queued run. Returns final status (or 'skipped' if another run holds the lock)."""
    lock_conn = get_engine().connect()
    try:
        got = lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
        lock_conn.commit()
        if not got:
            return "skipped"
        db = SessionLocal()
        run = db.get(PipelineRun, run_id)
        if run is None or run.status != "queued":
            db.close()
            return "skipped"
        run.status, run.started_at = "running", datetime.now(UTC)
        kind = run.kind
        db.commit()
        db.close()

        ctx = RunContext(run_id)
        # Without LSEG credentials every fetching run uses the synthetic generator (demo mode).
        demo = kind == "demo_seed" or not get_settings().lseg_configured
        status, error = "succeeded", None
        try:
            if kind != "recompute":
                _fetch_and_store(ctx, kind, demo)
            _compute(ctx)
            _publish(ctx)
            if ctx.noncritical_failed or ctx.warnings:
                status = "partial" if ctx.noncritical_failed else "succeeded"
        except StepFailed as exc:
            status, error = "failed", str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            logger.error("run %s crashed: %s", run_id, traceback.format_exc())
        _update_run(run_id, status=status, error=error, finished_at=datetime.now(UTC),
                    warnings_count=ctx.warnings)
        if kind != "recompute" and status != "failed":
            db = SessionLocal()
            runtime_settings.put(db, "data.mode", "demo" if demo else "live")
            db.commit()
            db.close()
        logger.info("run %s (%s) finished: %s", run_id, kind, status)
        return status
    finally:
        try:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            lock_conn.commit()
        finally:
            lock_conn.close()


def enqueue(kind: str, trigger: str, user_id: uuid.UUID | None = None) -> uuid.UUID:
    db = SessionLocal()
    run = PipelineRun(kind=kind, trigger=trigger, triggered_by=user_id)
    db.add(run)
    db.commit()
    rid = run.id
    db.close()
    return rid


def next_queued() -> uuid.UUID | None:
    db = SessionLocal()
    rid = db.scalar(select(PipelineRun.id).where(PipelineRun.status == "queued").order_by(PipelineRun.queued_at)
                    .limit(1))
    db.close()
    return rid


def recover_interrupted() -> int:
    """Runs left 'running' by a crashed worker → failed (only when no worker holds the lock)."""
    with get_engine().connect() as c:
        got = c.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
        if not got:
            return 0
        n = c.execute(text("UPDATE ops.pipeline_runs SET status='failed', finished_at=now(), "
                           "error='Interrupted (worker restarted)' WHERE status='running'")).rowcount
        c.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        c.commit()
        return n or 0
