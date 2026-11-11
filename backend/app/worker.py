"""Pipeline worker process: cron schedule + queued manual runs + housekeeping (TD §6, ADR-6).

    python -m app.worker
"""
from __future__ import annotations

import logging
import signal

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal, get_engine
from app.core.logging import configure_logging
from app.intel import jobs as intel_jobs
from app.pipeline import runner
from app.services import sso

logger = logging.getLogger("app.worker")


def scheduled_run() -> None:
    rid = runner.enqueue("incremental", "schedule")
    logger.info("scheduled run queued: %s", rid)
    drain_queue()


def drain_queue() -> None:
    while (rid := runner.next_queued()) is not None:
        status = runner.execute(rid)
        if status == "skipped":
            break
        if status in ("succeeded", "partial"):
            # Waste/Bio joins LSEG legs (FCPO, gasoil, FX, Arg soyoil): recompute the sheets on fresh prices.
            intel_jobs.enqueue("sheet_refresh", trigger="schedule")


def housekeeping() -> None:
    with get_engine().begin() as c:
        n = c.execute(text("DELETE FROM iam.sessions WHERE expires_at < now() - interval '7 days'")).rowcount
        c.execute(text("DELETE FROM ops.pipeline_runs WHERE queued_at < now() - interval '400 days'"))
    db = SessionLocal()
    m = sso.purge_expired_states(db)
    db.close()
    logger.info("housekeeping: %s sessions, %s oidc states purged", n, m)


def bootstrap_data_if_empty() -> None:
    with get_engine().connect() as c:
        has_prices = c.execute(text("SELECT EXISTS (SELECT 1 FROM market.price_daily)")).scalar()
        pending = c.execute(text("SELECT count(*) FROM ops.pipeline_runs WHERE status IN ('queued','running')"
                                 )).scalar()
    if not has_prices and not pending:
        kind = "full" if get_settings().lseg_configured else "demo_seed"
        logger.info("no market data yet — queuing %s run", kind)
        runner.enqueue(kind, "startup")


def drain_jobs() -> None:
    intel_jobs.drain()


def scheduled_intel(kind: str) -> None:
    if kind == "calendar_refresh" and not get_settings().claude_configured:
        return
    intel_jobs.enqueue(kind, trigger="schedule")


def bootstrap_intel_if_empty() -> None:
    """First start: load CFTC history, and the event calendar when Claude is configured."""
    with get_engine().connect() as c:
        has_cftc = c.execute(text("SELECT EXISTS (SELECT 1 FROM intel.cftc_positions)")).scalar()
        has_cal = c.execute(text("SELECT EXISTS (SELECT 1 FROM intel.calendar_events)")).scalar()
    if not has_cftc:
        intel_jobs.enqueue("cftc_refresh", trigger="startup")
    if not has_cal and get_settings().claude_configured:
        intel_jobs.enqueue("calendar_refresh", trigger="startup")
    with get_engine().connect() as c:
        pulled = c.execute(text("SELECT EXISTS (SELECT 1 FROM intel.sheet_sources WHERE status <> 'never')")).scalar()
    if not pulled:
        intel_jobs.enqueue("sheet_refresh", trigger="startup")


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    recovered = runner.recover_interrupted()
    if recovered:
        logger.warning("%s interrupted run(s) marked failed", recovered)
    recovered = intel_jobs.recover_interrupted()
    if recovered:
        logger.warning("%s interrupted job(s) marked failed", recovered)
    bootstrap_data_if_empty()
    bootstrap_intel_if_empty()

    sched = BlockingScheduler(timezone=s.pipeline_tz)
    sched.add_job(scheduled_run, CronTrigger.from_crontab(s.pipeline_cron, timezone=s.pipeline_tz),
                  id="daily_pipeline", max_instances=1, coalesce=True, misfire_grace_time=3600)
    sched.add_job(drain_queue, "interval", seconds=5, id="queue", max_instances=1, coalesce=True)
    # Intelligence jobs run in their own thread, so a long Claude call never delays the price pipeline.
    sched.add_job(drain_jobs, "interval", seconds=5, id="jobs", max_instances=1, coalesce=True)
    sched.add_job(scheduled_intel, CronTrigger.from_crontab(s.cftc_cron, timezone=s.pipeline_tz),
                  args=["cftc_refresh"], id="cftc", max_instances=1, coalesce=True, misfire_grace_time=6 * 3600)
    sched.add_job(scheduled_intel, CronTrigger.from_crontab(s.calendar_cron, timezone=s.pipeline_tz),
                  args=["calendar_refresh"], id="calendar", max_instances=1, coalesce=True, misfire_grace_time=3600)
    sched.add_job(scheduled_intel, CronTrigger.from_crontab(s.sheets_cron, timezone=s.pipeline_tz),
                  args=["sheet_refresh"], id="sheets", max_instances=1, coalesce=True, misfire_grace_time=3600)
    sched.add_job(housekeeping, "cron", hour=3, minute=15, id="housekeeping")
    signal.signal(signal.SIGTERM, lambda *_: sched.shutdown(wait=False))
    logger.info("worker started — schedule %r (%s)", s.pipeline_cron, s.pipeline_tz)
    sched.start()


if __name__ == "__main__":
    main()
