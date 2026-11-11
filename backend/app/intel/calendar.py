"""Commodity & energy event calendar — port of legacy scripts/calendar_data.py.

The legacy shelled out to the Claude Code CLI on a personal login. This calls the Claude API with its web-search
tool (same prompt, same parsing and validation), on the worker's weekday schedule or an admin's "Refresh". A
successful run replaces the cached events; a failed or empty run keeps the previous ones.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import text

from app.core.db import get_engine
from app.integrations import claude
from app.intel.jobs import register

HORIZON_DAYS = 30
CATEGORIES = ["Agri", "Energy", "Biofuels", "Positioning", "Macro"]
REGIONS = ["US", "MY", "ID", "BR", "AR", "CN", "EU", "IN", "Global"]
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 25}
SYSTEM = "You compile market data-release calendars from official sources, using web search."


def build_prompt(today: date, end: date) -> str:
    """Legacy _build_prompt, verbatim."""
    return f"""You are compiling a commodity & energy market data-release calendar for a \
palm-oil / vegoil-complex trader.

Today is {today:%A, %d %B %Y}. Use the web-search tool to look up the OFFICIAL \
release calendars and list every scheduled data release, report, or meeting with \
a confirmed date between {today:%Y-%m-%d} and {end:%Y-%m-%d} (inclusive) across \
these categories:

- Agri (grains & oilseeds / veg oils): USDA WASDE, USDA NASS Crop Production / \
Grain Stocks / Acreage / Fats & Oils, NOPA monthly crush, MPOB monthly palm-oil \
supply/demand, MPOA/SPPOMA estimates, SEA of India, GAPKI (Indonesia), CONAB \
(Brazil), StatsCan canola, EU/FEDIOL, China customs / Dalian.
- Energy / fossil fuels: EIA Weekly Petroleum Status Report (crude stocks + SPR), \
EIA Weekly Natural Gas Storage, EIA STEO, API inventories, OPEC & OPEC+ meetings \
and MOMR, IEA Oil Market Report, Baker Hughes rig count.
- Biofuels & policy: EPA RFS / RVO / RIN reports, EU biofuel / RED policy dates, \
Indonesia biodiesel B-mandate updates, US biodiesel tax-credit dates, ethanol data.
- Positioning: CFTC Commitments of Traders (weekly).
- Macro: major central-bank rate decisions (Fed FOMC, ECB, PBoC), China PMI, and \
other broad market-moving macro releases relevant to commodities.

For EACH event return an object with these exact fields:
  "date"       : ISO date "YYYY-MM-DD" (the official release/meeting date)
  "day"        : three-letter weekday, e.g. "Fri"
  "event"      : concise report/meeting name, e.g. "WASDE" or "EIA Weekly Petroleum Status"
  "agency"     : issuing body, e.g. "USDA", "MPOB", "EIA", "CFTC", "OPEC"
  "region"     : one of {REGIONS}
  "category"   : one of {CATEGORIES}
  "source_url" : the OFFICIAL page for that release calendar / report (https://...)
  "notes"      : short note (release time, what it covers) or "" if none

Rules:
- Only include events with a CONFIRMED official date. If you cannot verify a date \
from an official source, omit it — do not guess.
- source_url must be an official agency / exchange page, not a news article.
- Return ONLY a single JSON object, no prose, in exactly this shape:
  {{"events": [ {{ ...event... }}, ... ]}}
"""


def parse_events(raw: str) -> list:
    """Legacy _parse_events: strip fences and trailing commas, isolate the outermost object."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]
    data = json.loads(raw)
    events = data.get("events", data) if isinstance(data, dict) else data
    return events if isinstance(events, list) else []


def clean_rows(events: list, today: date, end: date) -> list[dict]:
    """Legacy _clean_rows: valid date and event within the horizon ±3 days, category normalised (else Agri),
    region defaults to Global (now also checked against REGIONS), http(s) source URLs only, de-duplicated."""
    rows, seen = [], set()
    for e in events:
        if not isinstance(e, dict):
            continue
        d = pd.to_datetime(e.get("date"), errors="coerce")
        event = str(e.get("event", "")).strip()
        if pd.isna(d) or not event:
            continue
        dd = d.date()
        if dd < today - timedelta(days=3) or dd > end + timedelta(days=3):
            continue
        cat = str(e.get("category", "")).strip().title()
        region = str(e.get("region", "")).strip()
        url = str(e.get("source_url", "")).strip()
        row = {"event_date": dd, "event": event[:200], "agency": str(e.get("agency", "")).strip()[:120],
               "region": region if region in REGIONS else "Global", "category": cat if cat in CATEGORIES else "Agri",
               "source_url": url if url.startswith(("http://", "https://")) else "",
               "notes": str(e.get("notes", "")).strip()}
        key = (row["event_date"], row["event"], row["agency"])
        if key not in seen:
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda r: (r["event_date"], r["category"], r["event"]))
    return rows


@register("calendar_refresh")
def refresh(params: dict) -> dict:
    today = date.today()
    end = today + timedelta(days=HORIZON_DAYS)
    res = claude.run(system=SYSTEM, content=build_prompt(today, end), max_tokens=32000, effort="medium",
                     tools=[WEB_SEARCH])
    try:
        rows = clean_rows(parse_events(res.text), today, end)
    except ValueError as exc:
        raise RuntimeError("Claude's answer was not the expected JSON; the previous calendar is kept.") from exc
    if not rows:
        raise RuntimeError("No usable events in Claude's answer; the previous calendar is kept.")
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM intel.calendar_events"))
        c.execute(text("INSERT INTO intel.calendar_events (event_date, event, agency, region, category, source_url, "
                       "notes, job_id) VALUES (:event_date, :event, :agency, :region, :category, :source_url, :notes, "
                       ":job_id)"), [{**r, "job_id": params.get("job_id")} for r in rows])
    return {"events": len(rows), "model": res.model, **res.usage}


def payload() -> dict:
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT event_date, event, agency, region, category, source_url, notes, fetched_at "
                              "FROM intel.calendar_events ORDER BY event_date, category, event")).mappings().all()
    events = [{**dict(r), "event_date": r["event_date"].isoformat(), "day": r["event_date"].strftime("%a")} for r in rows]
    fetched = max((r["fetched_at"] for r in rows), default=None)
    return {"events": events, "fetched_at": fetched.isoformat() if isinstance(fetched, datetime) else None,
            "today": date.today().isoformat(), "categories": CATEGORIES, "regions": REGIONS}
