"""
CFTC Commitments of Traders (Disaggregated, Futures + Options Combined) —
spec positioning for the vegoil / biofuels complex.

Source: the user manually saves CFTC's "Disaggregated Commitments of Traders
— Options and Futures Combined" long-format report pages from cftc.gov each
week into Biofuels/CFTC/<category>/*.html (category = Ag / Petroleum / Nat
Gas — one subfolder per saved page, e.g.
https://www.cftc.gov/dea/options/ag_lof.htm). Each saved page is a plain
fixed-width text report for ~15-40 commodities, wrapped in a <pre> tag.

Every commodity block already carries BOTH the current week's levels and the
prior week's change (the "Changes in Commitments from: <date>" row), so a
single snapshot file already yields two points on the time axis; each new
weekly file the user drops in adds one more. We re-parse every .html file
under Biofuels/CFTC/ from scratch on every call rather than maintain a
stateful history CSV — the source is a handful of small local files, so this
is cheap and nothing can go stale or desync. Restart the dashboard after
adding a new weekly file to pick it up (same convention as every other tab
here — see CLAUDE.md §13).

Categories (CFTC's "disaggregated" classification): Producer/Merchant/
Processor/User, Swap Dealers, Managed Money, Other Reportables, plus
Nonreportable (small traders below the reporting threshold). "Managed Money"
is the standard proxy for funds/CTAs/macro specs — the number this tab is
built around.

Public API
----------
  parse_cftc_file(path) -> DataFrame   One saved report page -> tidy rows (one
                                        per commodity, "All" maturities).
  load_cftc() -> DataFrame             Scan + parse every source file, expand
                                        each row's embedded prior week into
                                        its own row, dedupe, compute net/%OI
                                        metrics.
  build_cftc_layout(df) -> dash component   The CFTC tab body.
"""

import re
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parent.parent
CFTC_DIR = ROOT.parent / "Biofuels" / "CFTC"

# Permanent, append-only history — every report_date we've ever parsed gets
# locked in here, for EVERY commodity (not just RELEVANT_PRODUCTS), so the
# trend survives even if the user later overwrites/replaces a raw .html file
# with the same name (standard browser "Save As" behaviour). Without this,
# history would only ever reach as far back as whatever raw files happen to
# still be sitting in Biofuels/CFTC/ at the moment the dashboard starts.
HISTORY_PATH = ROOT / "data" / "processed" / "cftc_history.csv"

_HEADER_RE = re.compile(r'^(?P<name>.+?)\s+-\s+(?P<exch>.+?)\s{2,}Code-(?P<code>\S+)\s*$')
_DATE_RE   = re.compile(r'Combined,\s*([A-Za-z]+ \d{1,2},\s*\d{4})')
_CHG_RE    = re.compile(r'Changes in Commitments from:\s*([A-Za-z]+ \d{1,2},\s*\d{4})')
_NUM_RE    = re.compile(r'-?[\d,]+\.?\d*')

_COLS = ["open_interest", "pm_long", "pm_short",
         "swap_long", "swap_short", "swap_spread",
         "mm_long", "mm_short", "mm_spread",
         "other_long", "other_short", "other_spread",
         "nonrept_long", "nonrept_short"]

# cftc_code -> friendly label, in the trader's preferred display order. Match
# on the Code (not the name) since CFTC truncates long commodity names to a
# fixed column width in this report.
RELEVANT_PRODUCTS = [
    ("037021", "CME Malaysian Crude Palm Oil"),
    ("007601", "CBOT Soybean Oil (BO)"),
    ("026603", "CBOT Soybean Meal (SM)"),
    ("005602", "CBOT Soybeans (S)"),
    ("135731", "ICE Canola / Rapeseed"),
    ("002602", "CBOT Corn"),
    ("022651", "NYMEX ULSD (Diesel / Gasoil proxy)"),
    ("067651", "NYMEX WTI Crude Oil"),
    ("06765T", "NYMEX Brent Crude (financial)"),
    ("111659", "NYMEX RBOB Gasoline"),
    ("025651", "NYMEX Ethanol"),
    ("023651", "NYMEX Henry Hub Natural Gas"),
]
_LABELS = dict(RELEVANT_PRODUCTS)


# ── Parsing ───────────────────────────────────────────────────────────────────

def _nums(line: str) -> list:
    out = []
    for tok in _NUM_RE.findall(line):
        tok = tok.replace(",", "")
        if tok in ("", "-", "."):
            continue
        out.append(float(tok) if "." in tok else int(tok))
    return out


def _parse_block(lines: list, start: int, end: int):
    """Parse one commodity block (lines[start:end), header line at `start`).
    Returns a dict of the "All" maturities row (current week), plus the
    embedded prior-week change if present, or None if the block doesn't
    match the expected shape."""
    header = _HEADER_RE.match(lines[start])
    if not header or start + 1 >= end:
        return None
    dm = _DATE_RE.search(lines[start + 1])
    if not dm:
        return None
    report_date = pd.to_datetime(dm.group(1))

    all_vals = None
    chg_vals = None
    chg_date = None
    block = lines[start + 2:end]
    for k, ln in enumerate(block):
        if all_vals is None and ln.lstrip().startswith("All") and ":" in ln:
            nums = _nums(ln)
            if len(nums) == 14:
                all_vals = nums
        if chg_date is None:
            cm = _CHG_RE.search(ln)
            if cm:
                chg_date = pd.to_datetime(cm.group(1))
                if k + 1 < len(block):
                    nums = _nums(block[k + 1])
                    if len(nums) == 14:
                        chg_vals = nums
    if all_vals is None:
        return None

    row = dict(zip(_COLS, all_vals))
    row.update({
        "commodity":    header.group("name").strip(),
        "exchange":     header.group("exch").strip(),
        "cftc_code":    header.group("code").strip(),
        "report_date":  report_date,
    })
    if chg_vals is not None:
        row["prior_report_date"] = chg_date
        for c, v in zip(_COLS, chg_vals):
            row[f"chg_{c}"] = v
    return row


def parse_cftc_file(path) -> pd.DataFrame:
    """One saved CFTC long-format report page -> tidy rows, one per commodity
    (the "All" maturities row only — the headline figure this report is
    normally read by)."""
    text = Path(path).read_text(encoding="cp1252", errors="replace")
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if _HEADER_RE.match(ln)]
    rows = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        row = _parse_block(lines, s, e)
        if row:
            rows.append(row)
    return pd.DataFrame(rows)


def _expand_with_prior(df: pd.DataFrame) -> pd.DataFrame:
    """Each row's embedded "Changes in Commitments" figures let us back out
    the prior week's levels (current - change) — synthesize that as its own
    row so a single file already plots two points on the time axis."""
    if df.empty or "prior_report_date" not in df.columns:
        return df
    has_chg = df["prior_report_date"].notna()
    prior_rows = []
    for _, r in df[has_chg].iterrows():
        prow = {"commodity": r["commodity"], "exchange": r["exchange"],
                "cftc_code": r["cftc_code"], "report_date": r["prior_report_date"]}
        ok = True
        for c in _COLS:
            cur, chg = r.get(c), r.get(f"chg_{c}")
            if pd.isna(cur) or pd.isna(chg):
                ok = False
                break
            prow[c] = cur - chg
        if ok:
            prior_rows.append(prow)
    if not prior_rows:
        return df
    return pd.concat([df, pd.DataFrame(prior_rows)], ignore_index=True, sort=False)


def _load_history() -> pd.DataFrame:
    if not HISTORY_PATH.exists():
        return pd.DataFrame(columns=["commodity", "exchange", "cftc_code", "report_date"] + _COLS)
    hist = pd.read_csv(HISTORY_PATH)
    hist["report_date"] = pd.to_datetime(hist["report_date"], errors="coerce")
    return hist


def load_cftc() -> pd.DataFrame:
    """Scan Biofuels/CFTC/*/*.html, parse every snapshot, expand each row's
    embedded prior week into its own row, merge with the permanent history
    file (`HISTORY_PATH`), dedupe to one row per (cftc_code, report_date),
    write the merged result back so nothing already captured is ever lost,
    and compute net / %OI metrics. Returns a tidy, date-sorted DataFrame —
    empty (correctly-columned) if there's no source data and no history yet."""
    out_cols = ["commodity", "exchange", "cftc_code", "report_date"] + _COLS
    history = _load_history()

    frames = [history] if not history.empty else []
    if CFTC_DIR.exists():
        for f in sorted(CFTC_DIR.glob("*/*.html")):
            try:
                parsed = parse_cftc_file(f)
            except Exception:
                continue
            if not parsed.empty:
                frames.append(_expand_with_prior(parsed))
    if not frames:
        return pd.DataFrame(columns=out_cols)

    df = pd.concat(frames, ignore_index=True, sort=False)
    for c in _COLS:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = (df.sort_values("report_date")
            .drop_duplicates(subset=["cftc_code", "report_date"], keep="last"))

    # Persist the merged set — every (commodity, report_date) we've ever
    # seen, across every run — so next time this runs even with a raw file
    # removed/overwritten, that week's numbers are still here.
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    df[out_cols].to_csv(HISTORY_PATH, index=False)

    oi = df["open_interest"].replace(0, pd.NA)
    df["mm_net"]          = df["mm_long"] - df["mm_short"]
    df["swap_net"]        = df["swap_long"] - df["swap_short"]
    df["pm_net"]          = df["pm_long"] - df["pm_short"]
    df["other_net"]       = df["other_long"] - df["other_short"]
    df["mm_net_pct_oi"]   = df["mm_net"] / oi * 100
    df["swap_net_pct_oi"] = df["swap_net"] / oi * 100
    df["pm_net_pct_oi"]   = df["pm_net"] / oi * 100

    return df.sort_values(["commodity", "report_date"]).reset_index(drop=True)


# ── Dashboard layout ──────────────────────────────────────────────────────────

_HDR_BG = "#1F3864"          # dark blue, matches the dashboard's table headers
_BLUE   = "#2a78d6"          # net long / Managed Money
_RED    = "#e34948"          # net short
_ORANGE = "#eb6834"          # Swap Dealers
_AQUA   = "#1baf7a"          # Producer/Merchant
_GRID   = "#e8e8e8"


def _product_frame(df: pd.DataFrame, code: str) -> pd.DataFrame:
    return df[df["cftc_code"] == code].sort_values("report_date")


def _latest_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """One row per tracked product, at its latest available report_date,
    plus the week-over-week change in each of the 3 headline categories'
    net positions (Managed Money, Swap Dealers, Producer/Merchant)."""
    rows = []
    for code, label in RELEVANT_PRODUCTS:
        pf = _product_frame(df, code)
        if pf.empty:
            continue
        last = pf.iloc[-1].to_dict()
        prev = pf.iloc[-2].to_dict() if len(pf) > 1 else None
        last["label"] = label
        for col in ("mm_net", "swap_net", "pm_net"):
            last[f"wow_{col}"] = (last[col] - prev[col]) if prev else None
        rows.append(last)
    return pd.DataFrame(rows)


# The 3 headline categories shown in the comparison charts, in the order
# they're always displayed — Managed Money first since it's the standard
# "fund/spec positioning" number (see the trader's own question this
# answered: Swap Dealer net mostly reflects OTC/index-flow plumbing rather
# than a discretionary view, and Producer/Merchant net is close to the
# mirror image of Managed Money in most of these markets — both still worth
# their own chart, just read differently, which is why they're kept as
# separate categories rather than collapsed into one "non-MM" bucket (that
# would also silently drop Other Reportables/Nonreportable, which are real
# categories of their own, not folded in here).
_HEADLINE_CATEGORIES = [
    ("mm_net",   "mm_net_pct_oi",   "wow_mm_net",   "Managed Money",       _BLUE),
    ("swap_net", "swap_net_pct_oi", "wow_swap_net", "Swap Dealers",        _ORANGE),
    ("pm_net",   "pm_net_pct_oi",   "wow_pm_net",   "Producer/Merchant",   _AQUA),
]


def make_change_chart(snap: pd.DataFrame, wow_col: str, category_label: str,
                      pos_color: str = _BLUE) -> go.Figure:
    """Week-over-week change in a category's net position, one bar per
    tracked product — "where did this category add or reduce this week"."""
    d = snap.dropna(subset=[wow_col]).sort_values(wow_col) if not snap.empty else snap
    fig = go.Figure()
    if d.empty:
        fig.update_layout(
            title=dict(text=f"{category_label} — Net Position Change (needs a 2nd weekly file)",
                       font=dict(size=13)),
            height=120, plot_bgcolor="white", paper_bgcolor="white",
        )
        return fig
    colors = [pos_color if v >= 0 else _RED for v in d[wow_col]]
    fig.add_trace(go.Bar(
        x=d[wow_col], y=d["label"], orientation="h",
        marker=dict(color=colors),
        hovertemplate=f"%{{y}}<br>Δ Net {category_label}: " + "%{x:+,.0f} contracts<extra></extra>",
    ))
    fig.add_vline(x=0, line_width=1, line_color="#999")
    fig.update_layout(
        title=dict(text=f"{category_label} — Net Position Change, Most Recent Week (contracts)",
                   font=dict(size=14)),
        xaxis=dict(title="Δ Net Long Contracts (Long − Short)", showgrid=True,
                   gridcolor=_GRID, zeroline=False),
        yaxis=dict(automargin=True),
        margin=dict(l=10, r=20, t=40, b=40),
        plot_bgcolor="white", paper_bgcolor="white",
        height=90 + 32 * len(d),
        showlegend=False,
    )
    return fig


def make_net_level_chart(snap: pd.DataFrame, net_col: str, category_label: str,
                         pos_color: str = _BLUE) -> go.Figure:
    """Current snapshot: a category's net position LEVEL (contracts, Long −
    Short) — not the weekly change, not normalised by OI — so you can see
    where positioning actually STANDS right now. This is the chart that
    answers "they went short this week, but are they still net long
    overall?": a red (reduced) bar in the Weekly Change chart can sit right
    next to a large positive bar here, meaning a big net long got trimmed
    but hasn't flipped."""
    d = snap.dropna(subset=[net_col]).sort_values(net_col) if not snap.empty else snap
    fig = go.Figure()
    if d.empty:
        fig.update_layout(title=dict(text=f"{category_label} — Net Position (no data yet)", font=dict(size=13)),
                          height=120, plot_bgcolor="white", paper_bgcolor="white")
        return fig
    colors = [pos_color if v >= 0 else _RED for v in d[net_col]]
    fig.add_trace(go.Bar(
        x=d[net_col], y=d["label"], orientation="h",
        marker=dict(color=colors),
        hovertemplate=f"%{{y}}<br>Net {category_label}: " + "%{x:+,.0f} contracts<extra></extra>",
    ))
    fig.add_vline(x=0, line_width=1, line_color="#999")
    fig.update_layout(
        title=dict(text=f"{category_label} — Current Net Position (contracts, Long − Short)",
                   font=dict(size=14)),
        xaxis=dict(title="Net Long Contracts (Long − Short)", showgrid=True,
                   gridcolor=_GRID, zeroline=False),
        yaxis=dict(automargin=True),
        margin=dict(l=10, r=20, t=40, b=40),
        plot_bgcolor="white", paper_bgcolor="white",
        height=90 + 32 * len(d),
        showlegend=False,
    )
    return fig


def make_pct_oi_chart(snap: pd.DataFrame, pct_col: str, category_label: str,
                      pos_color: str = _BLUE) -> go.Figure:
    """Current snapshot: a category's net position as % of Open Interest,
    one bar per product — normalises by OI so products of very different
    size are directly comparable (e.g. a thin MCPO book vs. deep CBOT BO)."""
    d = snap.dropna(subset=[pct_col]).sort_values(pct_col) if not snap.empty else snap
    fig = go.Figure()
    if d.empty:
        fig.update_layout(title=dict(text=f"{category_label} — Net % of OI (no data yet)", font=dict(size=13)),
                          height=120, plot_bgcolor="white", paper_bgcolor="white")
        return fig
    colors = [pos_color if v >= 0 else _RED for v in d[pct_col]]
    fig.add_trace(go.Bar(
        x=d[pct_col], y=d["label"], orientation="h",
        marker=dict(color=colors),
        hovertemplate=f"%{{y}}<br>Net {category_label} / OI: " + "%{x:+.1f}%<extra></extra>",
    ))
    fig.add_vline(x=0, line_width=1, line_color="#999")
    fig.update_layout(
        title=dict(text=f"{category_label} — Net Position as % of Open Interest (current)",
                   font=dict(size=14)),
        xaxis=dict(title="Net Long % of Open Interest", showgrid=True,
                   gridcolor=_GRID, zeroline=False),
        yaxis=dict(automargin=True),
        margin=dict(l=10, r=20, t=40, b=40),
        plot_bgcolor="white", paper_bgcolor="white",
        height=90 + 32 * len(d),
        showlegend=False,
    )
    return fig


def make_product_change_bar(label: str, row: dict) -> go.Figure:
    """"By Product" view: one chart per product, one bar per category
    (Managed Money / Swap Dealers / Producer-Merchant) — this week's Δ net
    position for THIS product, all 3 categories side by side, so you can
    see where different trader types are placing their bets on the SAME
    product. Color carries category identity (not sign, unlike the "By
    Trader Type" charts) so the same category reads as the same color
    across every product's small multiple."""
    cats = [(wow_col, cat_label, color) for (_, _, wow_col, cat_label, color) in _HEADLINE_CATEGORIES]
    x = [c[1] for c in cats]
    y = [row.get(c[0]) for c in cats]
    colors = [c[2] for c in cats]
    fig = go.Figure(go.Bar(
        x=x, y=y, marker=dict(color=colors),
        hovertemplate="%{x}<br>Δ Net: %{y:+,.0f} contracts<extra></extra>",
    ))
    fig.add_hline(y=0, line_width=1, line_color="#999")
    fig.update_layout(
        title=dict(text=label, font=dict(size=13)),
        yaxis=dict(title="Δ Net Contracts (WoW)", showgrid=True, gridcolor=_GRID, zeroline=False),
        margin=dict(l=55, r=20, t=36, b=30),
        plot_bgcolor="white", paper_bgcolor="white",
        height=300, showlegend=False,
    )
    return fig


def make_product_net_level_bar(label: str, row: dict) -> go.Figure:
    """"By Product" view: one chart per product, one bar per category —
    each category's CURRENT net position LEVEL (contracts, not the weekly
    change and not normalised by OI) for THIS product, so e.g. "Managed
    Money went short this week but is still net long overall" is visible
    at a glance, right next to the weekly-change chart above it."""
    cats = [(net_col, cat_label, color) for (net_col, _, _, cat_label, color) in _HEADLINE_CATEGORIES]
    x = [c[1] for c in cats]
    y = [row.get(c[0]) for c in cats]
    colors = [c[2] for c in cats]
    fig = go.Figure(go.Bar(
        x=x, y=y, marker=dict(color=colors),
        hovertemplate="%{x}<br>Net: %{y:+,.0f} contracts<extra></extra>",
    ))
    fig.add_hline(y=0, line_width=1, line_color="#999")
    fig.update_layout(
        title=dict(text=label, font=dict(size=13)),
        yaxis=dict(title="Net Contracts (Long − Short)", showgrid=True, gridcolor=_GRID, zeroline=False),
        margin=dict(l=55, r=20, t=36, b=30),
        plot_bgcolor="white", paper_bgcolor="white",
        height=300, showlegend=False,
    )
    return fig


def make_product_pct_oi_bar(label: str, row: dict) -> go.Figure:
    """"By Product" view: one chart per product, one bar per category —
    each category's CURRENT net position as % of Open Interest for THIS
    product, so you can see at a glance which trader type is most
    "crowded" on this specific market right now."""
    cats = [(pct_col, cat_label, color) for (_, pct_col, _, cat_label, color) in _HEADLINE_CATEGORIES]
    x = [c[1] for c in cats]
    y = [row.get(c[0]) for c in cats]
    colors = [c[2] for c in cats]
    fig = go.Figure(go.Bar(
        x=x, y=y, marker=dict(color=colors),
        hovertemplate="%{x}<br>Net / OI: %{y:+.1f}%<extra></extra>",
    ))
    fig.add_hline(y=0, line_width=1, line_color="#999")
    fig.update_layout(
        title=dict(text=label, font=dict(size=13)),
        yaxis=dict(title="Net % of Open Interest", showgrid=True, gridcolor=_GRID, zeroline=False),
        margin=dict(l=55, r=20, t=36, b=30),
        plot_bgcolor="white", paper_bgcolor="white",
        height=300, showlegend=False,
    )
    return fig


def make_product_history_chart(label: str, pf: pd.DataFrame) -> go.Figure:
    """Per-product small multiple: Managed Money / Swap Dealer / Producer-
    Merchant net position over time — grows a new point each week as the
    user drops in a new source file."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=pf["report_date"], y=pf["mm_net"], mode="lines+markers",
                              name="Managed Money", line=dict(color=_BLUE, width=2),
                              marker=dict(size=6)))
    fig.add_trace(go.Scatter(x=pf["report_date"], y=pf["swap_net"], mode="lines+markers",
                              name="Swap Dealers", line=dict(color=_ORANGE, width=2),
                              marker=dict(size=6)))
    fig.add_trace(go.Scatter(x=pf["report_date"], y=pf["pm_net"], mode="lines+markers",
                              name="Producer/Merchant", line=dict(color=_AQUA, width=2),
                              marker=dict(size=6)))
    fig.add_hline(y=0, line_width=1, line_color="#ccc")
    fig.update_layout(
        title=dict(text=label, font=dict(size=13)),
        xaxis=dict(showgrid=True, gridcolor=_GRID),
        yaxis=dict(title="Net Contracts (Long − Short)", showgrid=True,
                   gridcolor=_GRID, zeroline=False),
        legend=dict(orientation="h", y=-0.22, font=dict(size=9)),
        margin=dict(l=55, r=20, t=36, b=30),
        plot_bgcolor="white", paper_bgcolor="white",
        hovermode="x unified",
        height=300,
    )
    return fig


def _net_cell_style(val, base):
    if pd.isna(val):
        return "n/a", {**base, "color": "#9e9e9e"}
    color = _BLUE if val >= 0 else _RED
    return f"{val:+,.0f}", {**base, "color": color, "fontWeight": "600"}


def _cftc_snapshot_table(snap: pd.DataFrame):
    from dash import html

    th_s = {"padding": "6px 12px", "textAlign": "right", "fontWeight": "600",
            "fontSize": "12px", "color": "white", "backgroundColor": _HDR_BG,
            "border": "none"}
    td_s = {"padding": "5px 12px", "textAlign": "right", "fontSize": "12px",
            "borderBottom": "1px solid #eee"}

    header = html.Thead(html.Tr([
        html.Th("Product",          style={**th_s, "textAlign": "left"}),
        html.Th("Report Date",      style=th_s),
        html.Th("Open Interest",    style=th_s),
        html.Th("MM Net",           style=th_s),
        html.Th("MM Net %OI",       style=th_s),
        html.Th("WoW Δ MM Net",     style=th_s),
        html.Th("Swap Net",         style=th_s),
        html.Th("Swap Net %OI",     style=th_s),
        html.Th("WoW Δ Swap Net",   style=th_s),
        html.Th("Prod/Merch Net",   style=th_s),
        html.Th("Prod/Merch %OI",   style=th_s),
        html.Th("WoW Δ Prod/Merch", style=th_s),
    ]))

    def _pct_str(v):
        return "n/a" if pd.isna(v) else f"{v:+.1f}%"

    rows = []
    for i, (_, r) in enumerate(snap.iterrows()):
        bg = "#F5F8FC" if i % 2 else "white"
        cell = {**td_s, "backgroundColor": bg}
        mm_net_str, mm_net_style     = _net_cell_style(r.get("mm_net"), cell)
        mm_wow_str, mm_wow_style     = _net_cell_style(r.get("wow_mm_net"), cell)
        swap_net_str, swap_net_style = _net_cell_style(r.get("swap_net"), cell)
        swap_wow_str, swap_wow_style = _net_cell_style(r.get("wow_swap_net"), cell)
        pm_net_str, pm_net_style     = _net_cell_style(r.get("pm_net"), cell)
        pm_wow_str, pm_wow_style     = _net_cell_style(r.get("wow_pm_net"), cell)

        rows.append(html.Tr([
            html.Td(r["label"], style={**cell, "textAlign": "left", "fontWeight": "600"}),
            html.Td(pd.Timestamp(r["report_date"]).strftime("%Y-%m-%d"), style=cell),
            html.Td(f"{r['open_interest']:,.0f}", style=cell),
            html.Td(mm_net_str, style=mm_net_style),
            html.Td(_pct_str(r.get("mm_net_pct_oi")), style=cell),
            html.Td(mm_wow_str, style=mm_wow_style),
            html.Td(swap_net_str, style=swap_net_style),
            html.Td(_pct_str(r.get("swap_net_pct_oi")), style=cell),
            html.Td(swap_wow_str, style=swap_wow_style),
            html.Td(pm_net_str, style=pm_net_style),
            html.Td(_pct_str(r.get("pm_net_pct_oi")), style=cell),
            html.Td(pm_wow_str, style=pm_wow_style),
        ]))

    return html.Div(
        html.Table([header, html.Tbody(rows)],
                   style={"width": "100%", "borderCollapse": "collapse"}),
        style={"overflowX": "auto"},
    )


def build_cftc_layout(df: pd.DataFrame):
    """Return the CFTC tab body: for each of the 3 headline categories
    (Managed Money, Swap Dealers, Producer/Merchant) a weekly-change chart
    and a %OI snapshot chart, then per-product history small multiples and
    a snapshot table — everything derived from `df` (the output of
    `load_cftc()`). The 3 categories are kept separate rather than merged
    into one "non-Managed-Money" bucket — see `_HEADLINE_CATEGORIES`."""
    from dash import dcc, html

    if df is None or df.empty:
        rel_dir = CFTC_DIR.relative_to(ROOT.parent) if CFTC_DIR.exists() else CFTC_DIR
        return html.Div([
            html.H3("CFTC Commitments of Traders — Spec Positioning",
                    style={"color": _HDR_BG}),
            html.P(f"No CFTC report files found under {rel_dir}. Save the CFTC "
                   "'Disaggregated Commitments of Traders — Options and Futures "
                   "Combined' long-format page (Ag / Petroleum / Nat Gas) there "
                   "each week and restart the dashboard.",
                   style={"color": "#888", "maxWidth": "640px"}),
        ])

    snap = _latest_snapshot(df)
    dates = sorted(df["report_date"].dropna().unique())
    as_of = pd.Timestamp(dates[-1]).strftime("%b %d, %Y") if dates else "n/a"

    # ── "By Trader Type" view: one chart per category (Managed Money / Swap
    # Dealers / Producer-Merchant), each comparing all 12 tracked products —
    # "which category is doing what, across the whole complex".
    by_trader_view = html.Div([
        html.H4("Weekly Position Change (contracts)",
                style={"color": _HDR_BG, "fontSize": "15px", "marginBottom": "8px"}),
        *[dcc.Graph(figure=make_change_chart(snap, wow_col, cat_label, pos_color),
                    config={"displayModeBar": False}, style={"marginBottom": "8px"})
          for (_, _, wow_col, cat_label, pos_color) in _HEADLINE_CATEGORIES],

        html.H4("Current Net Position (contracts)",
                style={"color": _HDR_BG, "fontSize": "15px", "marginTop": "16px", "marginBottom": "8px"}),
        html.P("Where positioning actually stands right now, in contracts — a category "
               "can show a red (reduced) bar above for this week and still be sitting on "
               "a large position here, e.g. \"trimmed longs but still heavily net long\".",
               style={"color": "#888", "fontSize": "12px", "marginTop": "0", "marginBottom": "8px"}),
        *[dcc.Graph(figure=make_net_level_chart(snap, net_col, cat_label, pos_color),
                    config={"displayModeBar": False}, style={"marginBottom": "8px"})
          for (net_col, _, _, cat_label, pos_color) in _HEADLINE_CATEGORIES],

        html.H4("Net Position vs. Open Interest (current)",
                style={"color": _HDR_BG, "fontSize": "15px", "marginTop": "16px", "marginBottom": "8px"}),
        *[dcc.Graph(figure=make_pct_oi_chart(snap, pct_col, cat_label, pos_color),
                    config={"displayModeBar": False}, style={"marginBottom": "8px"})
          for (_, pct_col, _, cat_label, pos_color) in _HEADLINE_CATEGORIES],
    ], style={"padding": "16px 0"})

    # ── "By Product" view: one small-multiple chart PER PRODUCT, each with
    # all 3 categories side by side — "for this one market, who's doing
    # what" — plus the existing per-product history lines, which are
    # already this same "by product" idiom over time.
    change_bars, level_bars, pct_bars, history_charts = [], [], [], []
    for code, label in RELEVANT_PRODUCTS:
        pf = _product_frame(df, code)
        if pf.empty:
            continue
        row = pf.iloc[-1].to_dict()
        prev = pf.iloc[-2].to_dict() if len(pf) > 1 else None
        for col in ("mm_net", "swap_net", "pm_net"):
            row[f"wow_{col}"] = (row[col] - prev[col]) if prev else None
        change_bars.append(dcc.Graph(figure=make_product_change_bar(label, row),
                                      config={"displayModeBar": False}))
        level_bars.append(dcc.Graph(figure=make_product_net_level_bar(label, row),
                                     config={"displayModeBar": False}))
        pct_bars.append(dcc.Graph(figure=make_product_pct_oi_bar(label, row),
                                   config={"displayModeBar": False}))
        history_charts.append(dcc.Graph(figure=make_product_history_chart(label, pf),
                                         config={"displayModeBar": False}))

    by_product_view = html.Div([
        html.H4("Weekly Position Change, by Product (contracts)",
                style={"color": _HDR_BG, "fontSize": "15px", "marginBottom": "10px"}),
        html.Div(change_bars, style={"display": "grid", "gridTemplateColumns": "repeat(3, 1fr)",
                                     "gap": "16px", "marginBottom": "24px"}),

        html.H4("Current Net Position, by Product (contracts)",
                style={"color": _HDR_BG, "fontSize": "15px", "marginBottom": "10px"}),
        html.Div(level_bars, style={"display": "grid", "gridTemplateColumns": "repeat(3, 1fr)",
                                    "gap": "16px", "marginBottom": "24px"}),

        html.H4("Net Position vs. Open Interest, by Product (current)",
                style={"color": _HDR_BG, "fontSize": "15px", "marginBottom": "10px"}),
        html.Div(pct_bars, style={"display": "grid", "gridTemplateColumns": "repeat(3, 1fr)",
                                  "gap": "16px", "marginBottom": "24px"}),

        html.H4("Net Position History, by Product",
                style={"color": _HDR_BG, "fontSize": "15px", "marginBottom": "10px"}),
        html.Div(history_charts, style={"display": "grid", "gridTemplateColumns": "repeat(3, 1fr)",
                                        "gap": "16px", "marginBottom": "16px"}),
    ], style={"padding": "16px 0"})

    return html.Div([
        html.Div([
            html.H3("CFTC Commitments of Traders — Spec Positioning",
                    style={"color": _HDR_BG, "margin": "0", "fontSize": "20px",
                           "display": "inline-block"}),
            html.Span(f"  ·  Disaggregated, Futures+Options Combined  ·  "
                      f"latest report {as_of}  ·  {len(dates)} week(s) on file  ·  ",
                      style={"color": "#888", "fontSize": "13px"}),
            html.Span('"Managed Money" = the standard funds / CTA / macro-spec proxy; '
                      '"Swap Dealers" mostly reflects OTC/index-flow plumbing rather than '
                      'a discretionary view; "Producer/Merchant" is the commercial hedging side',
                      style={"color": "#888", "fontSize": "13px", "fontStyle": "italic"}),
        ], style={"marginBottom": "4px"}),

        dcc.Tabs([
            dcc.Tab(label="By Trader Type", children=[by_trader_view]),
            dcc.Tab(label="By Product", children=[by_product_view]),
        ]),

        html.H4("Snapshot Table", style={"color": _HDR_BG, "fontSize": "15px",
                                          "marginTop": "20px", "marginBottom": "10px"}),
        _cftc_snapshot_table(snap),

        html.P("Source: user-saved CFTC 'Disaggregated Commitments of Traders — "
               "Options and Futures Combined' long-format pages, dropped weekly "
               f"into {CFTC_DIR.relative_to(ROOT.parent)}. Re-parsed from scratch "
               "at each dashboard start — add a new file and restart to refresh.",
               style={"color": "#aaa", "fontSize": "11px", "marginTop": "16px"}),
    ])


if __name__ == "__main__":
    out = load_cftc()
    if out.empty:
        print(f"No CFTC files found under {CFTC_DIR}.")
    else:
        print(f"Parsed {out['cftc_code'].nunique()} commodities, "
              f"{out['report_date'].nunique()} report date(s), {len(out)} rows.")
        snap = _latest_snapshot(out)
        with pd.option_context("display.max_rows", 50, "display.width", 180):
            print(snap[["label", "report_date", "open_interest", "mm_long", "mm_short",
                        "mm_net", "mm_net_pct_oi", "wow_mm_net"]].to_string(index=False))
