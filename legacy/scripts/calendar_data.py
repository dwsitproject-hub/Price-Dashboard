"""
Commodity / energy event calendar for the dashboard's "Calendar" tab.

Every daily run asks **Claude Sonnet** (with the web-search tool) to compile the
upcoming ~30 days of market-moving data releases and meetings across the world's
agri, energy, biofuel and macro agencies — WASDE/USDA, MPOB, EIA (petroleum
status incl. SPR, natural-gas storage), CFTC Commitments of Traders, OPEC/IEA,
EPA RFS, NOPA, CONAB, StatsCan, EU/FEDIOL, China customs, central banks, etc. —
each with its **official source URL**. The result is cached to a tidy CSV that
the dashboard reads at startup and renders as a sortable, filterable table.

Design mirrors the other non-critical external loaders (`tradable_prem_data.py`,
`gsheet_data.py`): fetch → cache CSV → `load_*`, and the fetch fails soft — a
missing key, exhausted Anthropic credit, or an empty pull logs a WARNING and
keeps the previous cache rather than wiping it (same spirit as `fetch.py`'s
holiday-window behavior). So it never blocks the daily price pull.

Authentication
--------------
Drives the **Claude Code CLI** (`claude -p …`) in headless print mode, which
runs on the user's **Max/Pro subscription** (OAuth) — NOT the pay-as-you-go
`ANTHROPIC_API_KEY` (that credit is exhausted; see CLAUDE.md §13). The call
explicitly drops `ANTHROPIC_API_KEY` from the child environment so Claude Code
uses the subscription rather than falling back to the dead API key. If the CLI
is missing or the pull fails, the tab shows the last cached calendar (or an
"awaiting first pull" note) and the update still completes.

Public API
----------
  fetch_calendar() -> Optional[int]
      Live Sonnet + web-search pull; writes the cache CSV. Returns the row count,
      or None if the pull failed / returned nothing (cache left untouched).
  load_calendar() -> pd.DataFrame
      Read the cache into a tidy DataFrame (empty, correctly-columned frame when
      the cache is missing).
  build_calendar_layout(df) -> dash component
      The Calendar tab body: a sortable/filterable dash_table.DataTable with a
      hyperlinked Source column and today / this-week highlighting.

Cache format (data/processed/calendar_events.csv): tidy rows of
  date, day, event, agency, region, category, source_url, notes, fetched_at
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import date as _date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

CACHE_PATH = ROOT / "data" / "processed" / "calendar_events.csv"

COLUMNS = ["date", "day", "event", "agency", "region", "category",
           "source_url", "notes", "fetched_at"]

# Claude Code CLI (headless print mode → subscription auth + built-in WebSearch).
CLAUDE_MODEL  = "sonnet"          # --model alias; runs on the Max/Pro plan
ALLOWED_TOOLS = "WebSearch,WebFetch"
CLI_TIMEOUT   = 600               # seconds; web search + compiling can take a while
HORIZON_DAYS  = 30

CATEGORIES = ["Agri", "Energy", "Biofuels", "Positioning", "Macro"]
REGIONS    = ["US", "MY", "ID", "BR", "AR", "CN", "EU", "IN", "Global"]


# ── Prompt ────────────────────────────────────────────────────────────────────

def _build_prompt(today: _date, end: _date) -> str:
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


# ── JSON parsing (mirrors doc_extract.py's fence/trailing-comma handling) ──────

def _parse_events(raw: str) -> list:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    # Fix trailing commas before ] or }
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    # If there's leading/trailing noise, isolate the outermost JSON object.
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]
    data = json.loads(raw)
    events = data.get("events", data) if isinstance(data, dict) else data
    return events if isinstance(events, list) else []


def _clean_rows(events: list, today: _date, end: _date, fetched_at: str) -> pd.DataFrame:
    """Validate + normalise model output into cache rows."""
    rows = []
    for e in events:
        if not isinstance(e, dict):
            continue
        d = pd.to_datetime(e.get("date"), errors="coerce")
        url = str(e.get("source_url", "")).strip()
        event = str(e.get("event", "")).strip()
        if pd.isna(d) or not event:
            continue
        # Keep to the requested horizon (allow a few days back for context).
        dd = d.date()
        if dd < today - timedelta(days=3) or dd > end + timedelta(days=3):
            continue
        cat = str(e.get("category", "")).strip().title()
        if cat not in CATEGORIES:
            cat = "Agri"
        rows.append({
            "date":       d.strftime("%Y-%m-%d"),
            "day":        d.strftime("%a"),
            "event":      event,
            "agency":     str(e.get("agency", "")).strip(),
            "region":     str(e.get("region", "")).strip() or "Global",
            "category":   cat,
            "source_url": url if url.startswith("http") else "",
            "notes":      str(e.get("notes", "")).strip(),
            "fetched_at": fetched_at,
        })
    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    df = (df.drop_duplicates(subset=["date", "event", "agency"])
            .sort_values(["date", "category", "event"])
            .reset_index(drop=True))
    return df


# ── Fetch ─────────────────────────────────────────────────────────────────────

def _run_claude_cli(prompt: str) -> Optional[str]:
    """Drive the Claude Code CLI in headless print mode and return the model's
    result text, or None on any failure.

    Runs on the user's Max/Pro **subscription**: we strip ``ANTHROPIC_API_KEY``
    from the child environment so Claude Code authenticates with its stored OAuth
    login instead of falling back to the (exhausted) pay-as-you-go API key.
    The prompt is passed on **stdin** so no shell-quoting of the long prompt is
    needed; the fixed command string carries no user data.
    """
    if shutil.which("claude") is None and shutil.which("claude.cmd") is None:
        logger.warning("Calendar: 'claude' CLI not found on PATH — skipping.")
        return None

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    cmd = (f'claude -p --output-format json '
           f'--allowedTools "{ALLOWED_TOOLS}" --model {CLAUDE_MODEL}')
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            shell=True, env=env, timeout=CLI_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Calendar: claude CLI timed out after %ds.", CLI_TIMEOUT)
        return None
    except Exception as exc:  # pragma: no cover
        logger.warning("Calendar: claude CLI failed to launch (%s).", exc)
        return None

    if proc.returncode != 0:
        logger.warning("Calendar: claude CLI exit %s: %s",
                       proc.returncode, (proc.stderr or "").strip()[:300])
        return None

    # --output-format json wraps the reply: {"result": "<text>", "is_error": ...}
    try:
        envelope = json.loads(proc.stdout)
    except Exception:
        return proc.stdout  # some builds emit plain text — parse it directly
    if envelope.get("is_error"):
        logger.warning("Calendar: claude CLI reported error: %s",
                       str(envelope.get("result"))[:300])
        return None
    return envelope.get("result", "")


def fetch_calendar() -> Optional[int]:
    """Live Sonnet + web-search pull (via the Claude Code CLI) → write the cache.

    Returns the number of events written, or None if the pull failed or returned
    nothing (in which case any existing cache is left untouched).
    """
    today = _date.today()
    end   = today + timedelta(days=HORIZON_DAYS)
    prompt = _build_prompt(today, end)

    text = _run_claude_cli(prompt)
    if not text:
        return None  # already logged; keep previous cache

    try:
        events = _parse_events(text)
    except Exception as exc:
        logger.warning("Calendar: could not parse model JSON (%s) — keeping cache.", exc)
        return None

    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    df = _clean_rows(events, today, end, fetched_at)
    if df.empty:
        logger.warning("Calendar: pull returned 0 usable events — keeping previous cache.")
        return None

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CACHE_PATH, index=False)
    logger.info("Calendar: wrote %d events to %s.", len(df), CACHE_PATH)
    return len(df)


# ── Load for dashboard ────────────────────────────────────────────────────────

def load_calendar() -> pd.DataFrame:
    """Read the cache into a tidy DataFrame; empty (correctly-columned) frame
    when the cache is missing."""
    if not CACHE_PATH.exists():
        logger.info("Calendar: no cache at %s.", CACHE_PATH)
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(CACHE_PATH, dtype=str).fillna("")
    except Exception as exc:
        logger.warning("Calendar: cache unreadable (%s).", exc)
        return pd.DataFrame(columns=COLUMNS)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[COLUMNS]


# ── Dashboard layout ──────────────────────────────────────────────────────────

_HDR_BG    = "#1F3864"   # dark blue, matches the dashboard's table headers
_TODAY_BG  = "#FFF3B0"   # amber — today's row
_WEEK_BG   = "#EAF1FB"   # pale blue — current 7-day window
_CAT_TINT  = {           # subtle left-edge tint per category (on the Category cell)
    "Agri":        "#E2EFDA",
    "Energy":      "#FCE4D6",
    "Biofuels":    "#E4DFEC",
    "Positioning": "#FFF2CC",
    "Macro":       "#DDEBF7",
}


def build_calendar_layout(df: pd.DataFrame):
    """Return the Calendar tab body — a sortable/filterable event table with a
    hyperlinked Source column and today / this-week highlighting."""
    from dash import dash_table, html

    if df is None or df.empty:
        return html.Div([
            html.H3("Commodity & Energy Event Calendar",
                    style={"color": _HDR_BG, "marginBottom": "8px"}),
            html.P("Awaiting the first daily pull. The calendar is compiled each "
                   "morning by the update job (Claude Sonnet + web search) and "
                   "cached; it will appear here after the next run of update.bat.",
                   style={"color": "#888", "maxWidth": "640px"}),
        ])

    today = _date.today()
    week_end = today + timedelta(days=7)
    fetched = df["fetched_at"].dropna().replace("", pd.NA).dropna()
    fetched_str = fetched.iloc[-1] if not fetched.empty else "n/a"

    # Build display records — a markdown "source" cell renders as a real link.
    records = []
    for _, r in df.iterrows():
        agency = r["agency"] or "source"
        url = r["source_url"]
        source_md = f"[{agency}]({url})" if url else agency
        records.append({
            "date":     r["date"],
            "day":      r["day"],
            "event":    r["event"],
            "region":   r["region"],
            "category": r["category"],
            "source":   source_md,
            "notes":    r["notes"],
        })

    columns = [
        {"name": "Date",     "id": "date"},
        {"name": "Day",      "id": "day"},
        {"name": "Event",    "id": "event"},
        {"name": "Region",   "id": "region"},
        {"name": "Category", "id": "category"},
        {"name": "Source",   "id": "source", "presentation": "markdown"},
        {"name": "Notes",    "id": "notes"},
    ]

    style_conditional = [
        # Zebra striping (first, so highlights below override it).
        {"if": {"row_index": "odd"}, "backgroundColor": "#F5F8FC"},
        # Current 7-day window.
        {"if": {"filter_query":
                f'{{date}} >= "{today:%Y-%m-%d}" && {{date}} <= "{week_end:%Y-%m-%d}"'},
         "backgroundColor": _WEEK_BG},
        # Today (wins over the week window).
        {"if": {"filter_query": f'{{date}} = "{today:%Y-%m-%d}"'},
         "backgroundColor": _TODAY_BG, "fontWeight": "bold"},
    ]
    # Category tint on the Category cell only.
    for cat, colour in _CAT_TINT.items():
        style_conditional.append({
            "if": {"filter_query": f'{{category}} = "{cat}"', "column_id": "category"},
            "backgroundColor": colour, "fontWeight": "600",
        })

    table = dash_table.DataTable(
        data=records,
        columns=columns,
        sort_action="native",
        filter_action="native",
        page_size=100,
        markdown_options={"html": False, "link_target": "_blank"},
        style_as_list_view=True,
        style_table={"width": "100%"},
        style_header={
            "backgroundColor": _HDR_BG, "color": "white", "fontWeight": "bold",
            "textAlign": "left", "border": "none", "fontSize": "15px",
            "padding": "9px 14px",
        },
        style_filter={"backgroundColor": "#EEF2F8", "height": "30px"},
        style_cell={
            "textAlign": "left", "padding": "8px 14px", "fontSize": "15px",
            "fontFamily": "Segoe UI, Arial, sans-serif", "lineHeight": "1.35",
            "whiteSpace": "normal", "height": "auto", "border": "none",
            "borderBottom": "1px solid #e6e6e6",
        },
        style_cell_conditional=[
            {"if": {"column_id": "date"},   "width": "104px"},
            {"if": {"column_id": "day"},    "width": "48px"},
            {"if": {"column_id": "event"},  "minWidth": "300px", "fontWeight": "500"},
            {"if": {"column_id": "region"}, "width": "64px"},
            {"if": {"column_id": "category"}, "width": "108px"},
            {"if": {"column_id": "source"}, "width": "92px"},
            {"if": {"column_id": "notes"},  "minWidth": "220px", "color": "#555"},
        ],
        style_data_conditional=style_conditional,
    )

    return html.Div([
        html.Div([
            html.H3("Commodity & Energy Event Calendar",
                    style={"color": _HDR_BG, "margin": "0", "fontSize": "20px",
                           "display": "inline-block"}),
            html.Span(f"  ·  next ~{HORIZON_DAYS} days  ·  refreshed {fetched_str}  ·  ",
                      style={"color": "#888", "fontSize": "13px"}),
            html.Span("verify each date via its linked source",
                      style={"color": "#888", "fontSize": "13px", "fontStyle": "italic"}),
        ], style={"marginBottom": "8px"}),
        table,
    ])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Fetching commodity/energy event calendar (Sonnet + web search)...")
    n = fetch_calendar()
    if n is None:
        print("No data written (missing key, failed pull, or empty result). "
              "Previous cache left untouched.")
    else:
        print(f"\nWrote {n} events to {CACHE_PATH}\n")
        out = load_calendar()
        with pd.option_context("display.max_rows", 200, "display.width", 160):
            print(out[["date", "day", "event", "agency", "region", "category"]].to_string(index=False))
