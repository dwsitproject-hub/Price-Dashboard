"""
News tab rendering module for the LSEGDashboard.
Provides a geography × product matrix selector and blended output panel
combining LSEG market data with document-extracted intelligence.
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html
from dash.exceptions import PreventUpdate

ROOT = Path(__file__).parent.parent
PROC_DIR = ROOT / "data" / "processed"
RAW_FILE = ROOT / "data" / "raw" / "all_instruments_raw.csv"
INTEL_DIR = ROOT / "data" / "doc_intel"

# ── Geography × Product options ──────────────────────────────────────────────

GEOGRAPHIES = ["Asia", "America", "EU", "Africa"]
PRODUCTS = ["Palm", "Soybean", "Other Edible Oils", "Energy", "Biodiesel"]

# ── LSEG instrument mapping ─────────────────────────────────────────────────
# Maps (geography, product) → list of RICs to load from all_instruments_raw.csv
# These show outright prices for the selected combination.

INSTRUMENT_MAP: dict[tuple[str, str], list[str]] = {
    # Asia
    ("Asia", "Palm"):              ["FCPOc3", "DCPc3", "CPO-MYSTH-M2", "POL-MYRBD-M2",
                                    "CPO-ID-M2", "POL-IDRBD-M2"],
    ("Asia", "Soybean"):           ["DBYc3", "JCI-SOA-DALN"],
    ("Asia", "Other Edible Oils"): [],
    ("Asia", "Energy"):            [],
    ("Asia", "Biodiesel"):         [],
    # America
    ("America", "Palm"):              [],
    ("America", "Soybean"):           ["BOc3", "Sc3", "SMc3", "BO-ARGUPR-C1",
                                       "S-ARGUPR-C1", "SM-ARGUPR-P1"],
    ("America", "Other Edible Oils"): [],
    ("America", "Energy"):            ["CLc3", "HOc3"],
    ("America", "Biodiesel"):         [],
    # EU
    ("EU", "Palm"):              [],
    ("EU", "Soybean"):           [],
    ("EU", "Other Edible Oils"): ["KTS-SOUABS-FOB"],
    ("EU", "Energy"):            ["LGOc3", "LCOc3"],
    ("EU", "Biodiesel"):         [],
    # Africa
    ("Africa", "Palm"):              [],
    ("Africa", "Soybean"):           [],
    ("Africa", "Other Edible Oils"): [],
    ("Africa", "Energy"):            [],
    ("Africa", "Biodiesel"):         [],
}

# Human-readable RIC names
RIC_NAMES = {
    "FCPOc3": "FCPO c3 (MYR/MT)", "DCPc3": "Dalian Palm c3 (CNY/MT)",
    "CPO-MYSTH-M2": "MY CPO Cash M2", "POL-MYRBD-M2": "MY Olein M2",
    "CPO-ID-M2": "ID CPO Cash M2", "POL-IDRBD-M2": "ID Olein M2",
    "DBYc3": "Dalian Soy Oil c3", "JCI-SOA-DALN": "CN Cash Soy Oil",
    "BOc3": "CBOT Soy Oil c3", "Sc3": "CBOT Soybeans c3", "SMc3": "CBOT Soy Meal c3",
    "BO-ARGUPR-C1": "SA Soy Oil c1", "S-ARGUPR-C1": "SA Soybeans c1",
    "SM-ARGUPR-P1": "SA Soy Meal", "SOY-CTILL-US": "US Soy Oil Basis",
    "CLc3": "WTI Crude c3", "HOc3": "Heating Oil c3",
    "LGOc3": "Gasoil c3", "LCOc3": "Brent Crude c3",
    "KTS-SOUABS-FOB": "Sunflower FOB Black Sea",
}

# ── Styles ───────────────────────────────────────────────────────────────────

_SECTION_HEADER = {
    "fontWeight": "700", "fontSize": "15px", "color": "#2c3e50",
    "borderBottom": "2px solid #3498db", "paddingBottom": "6px",
    "marginTop": "24px", "marginBottom": "12px",
}

_CARD_STYLE = {
    "backgroundColor": "#fff", "borderRadius": "8px",
    "boxShadow": "0 1px 4px rgba(0,0,0,0.08)", "padding": "16px",
    "marginBottom": "16px",
}

_TAG_STYLE = {
    "display": "inline-block", "padding": "2px 8px", "borderRadius": "4px",
    "fontSize": "11px", "marginRight": "6px", "marginBottom": "4px",
}

_SENTIMENT_COLORS = {
    "bullish": "#27ae60", "bearish": "#e74c3c", "neutral": "#7f8c8d",
}

_THEME_COLOR = "#3498db"


# ── Data loading ─────────────────────────────────────────────────────────────

def load_doc_intel() -> dict:
    """Load document intelligence Parquet files. Returns dict with keys: prices, sentiment, documents."""
    result = {"prices": pd.DataFrame(), "sentiment": pd.DataFrame(), "documents": pd.DataFrame()}

    prices_path = INTEL_DIR / "prices.parquet"
    sentiment_path = INTEL_DIR / "sentiment.parquet"
    documents_path = INTEL_DIR / "documents.parquet"

    if prices_path.exists():
        result["prices"] = pd.read_parquet(prices_path)
    if sentiment_path.exists():
        result["sentiment"] = pd.read_parquet(sentiment_path)
    if documents_path.exists():
        result["documents"] = pd.read_parquet(documents_path)

    return result


def load_lseg_prices(rics: list[str]) -> dict[str, pd.DataFrame]:
    """Load outright price time-series for given RICs from raw data."""
    if not rics or not RAW_FILE.exists():
        return {}

    df = pd.read_csv(RAW_FILE, parse_dates=["Trade Date"])
    df = df.rename(columns={"Trade Date": "date"})
    df = df[df["RIC"].isin(rics)]

    data = {}
    for ric in rics:
        ric_df = df[df["RIC"] == ric].copy()
        if ric_df.empty:
            continue
        # Use Last, fallback to Settlement Price
        ric_df["price"] = pd.to_numeric(ric_df.get("Last"), errors="coerce")
        sett = pd.to_numeric(ric_df.get("Settlement Price"), errors="coerce")
        ric_df["price"] = ric_df["price"].fillna(sett)
        ric_df = ric_df[["date", "price"]].dropna().sort_values("date")
        if not ric_df.empty:
            data[ric] = ric_df.reset_index(drop=True)

    return data


# ── Layout builder ───────────────────────────────────────────────────────────

def build_news_layout() -> html.Div:
    """Build the News tab layout with geography and product dropdowns."""
    return html.Div([
        # Selector row
        html.Div([
            html.Div([
                html.Label("Geography", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                dcc.Dropdown(
                    id="news-geography",
                    options=[{"label": g, "value": g} for g in GEOGRAPHIES],
                    value="Asia",
                    clearable=False,
                    style={"minWidth": "200px"},
                ),
            ]),
            html.Div([
                html.Label("Product", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                dcc.Dropdown(
                    id="news-product",
                    options=[{"label": p, "value": p} for p in PRODUCTS],
                    value="Palm",
                    clearable=False,
                    style={"minWidth": "200px"},
                ),
            ]),
        ], style={
            "display": "flex", "gap": "24px", "padding": "12px 0",
            "borderBottom": "1px solid #e0e0e0", "marginBottom": "16px",
        }),

        # Output panel (populated by callback)
        html.Div(id="news-panel"),
    ])


# ── Panel builder ────────────────────────────────────────────────────────────

def build_news_panel(geography: str, product: str) -> html.Div:
    """Build the blended output panel for a geography × product combination."""
    children = []

    # Load data
    doc_intel = load_doc_intel()
    rics = INSTRUMENT_MAP.get((geography, product), [])
    lseg_data = load_lseg_prices(rics)

    # ── 1. Price Trend ────────────────────────────────────────────────────────
    children.append(html.H4("Price Trend", style=_SECTION_HEADER))
    if lseg_data:
        children.append(_build_price_chart(lseg_data, geography, product))
    else:
        # Try document-extracted prices
        doc_prices = _filter_doc_prices(doc_intel["prices"], geography, product)
        if not doc_prices.empty:
            children.append(_build_doc_price_chart(doc_prices))
        else:
            children.append(html.P(
                f"No LSEG data or document-extracted prices for {geography} + {product}.",
                style={"color": "#888", "fontStyle": "italic"},
            ))

    # ── 2. Recent News ────────────────────────────────────────────────────────
    children.append(html.H4("Recent News", style=_SECTION_HEADER))
    doc_prices = _filter_doc_prices(doc_intel["prices"], geography, product)
    doc_sent = _filter_doc_sentiment(doc_intel["sentiment"], geography, product)

    if doc_prices.empty and doc_sent.empty:
        children.append(html.P(
            "No document extractions yet. Drop documents into data/documents/inbox/ and run doc_extract.py.",
            style={"color": "#888", "fontStyle": "italic"},
        ))
    else:
        children.append(_build_news_feed(doc_prices, doc_sent))

    # ── 3. Sentiment ──────────────────────────────────────────────────────────
    children.append(html.H4("Sentiment", style=_SECTION_HEADER))
    if not doc_sent.empty:
        children.append(_build_sentiment_section(doc_sent))
    else:
        children.append(html.P(
            "No sentiment data available yet.",
            style={"color": "#888", "fontStyle": "italic"},
        ))

    # ── 4. Market Expectation ─────────────────────────────────────────────────
    children.append(html.H4("Market Expectation", style=_SECTION_HEADER))
    forecasts = doc_prices[doc_prices["price_type"].isin(
        ["forecast", "target", "support", "resistance"]
    )] if not doc_prices.empty else pd.DataFrame()

    if not forecasts.empty:
        children.append(_build_expectations(forecasts))
    else:
        children.append(html.P(
            "No forecasts or targets extracted yet.",
            style={"color": "#888", "fontStyle": "italic"},
        ))

    # ── 5. SND Snapshot ───────────────────────────────────────────────────────
    children.append(html.H4("SND Snapshot", style=_SECTION_HEADER))
    snd_content = _build_snd_snapshot(geography, product)
    children.append(snd_content)

    return html.Div(children)


# ── Price chart from LSEG data ───────────────────────────────────────────────

def _build_price_chart(lseg_data: dict[str, pd.DataFrame], geography: str, product: str) -> dcc.Graph:
    """Build a Plotly chart of LSEG outright prices."""
    fig = go.Figure()
    for ric, df in lseg_data.items():
        name = RIC_NAMES.get(ric, ric)
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["price"],
            mode="lines", name=name,
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f}<extra>" + name + "</extra>",
        ))

    fig.update_layout(
        title=f"{geography} — {product} (LSEG Data)",
        template="plotly_white",
        height=400,
        margin=dict(l=50, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis=dict(
            rangeselector=dict(buttons=[
                dict(count=1, label="1M", step="month", stepmode="backward"),
                dict(count=3, label="3M", step="month", stepmode="backward"),
                dict(count=6, label="6M", step="month", stepmode="backward"),
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(step="all", label="All"),
            ]),
            rangeslider=dict(visible=False),
        ),
    )
    return dcc.Graph(figure=fig, config={"displayModeBar": False, "scrollZoom": True})


def _build_doc_price_chart(doc_prices: pd.DataFrame) -> dcc.Graph:
    """Build chart from document-extracted prices (when no LSEG data)."""
    fig = go.Figure()

    doc_prices = doc_prices.copy()
    doc_prices["document_date"] = pd.to_datetime(doc_prices["document_date"])

    for price_type in doc_prices["price_type"].unique():
        subset = doc_prices[doc_prices["price_type"] == price_type]
        fig.add_trace(go.Scatter(
            x=subset["document_date"], y=subset["value"],
            mode="markers+lines", name=price_type.title(),
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f}<extra>" + price_type + "</extra>",
        ))

    fig.update_layout(
        title="Document-Extracted Prices",
        template="plotly_white",
        height=350,
        margin=dict(l=50, r=20, t=50, b=40),
    )
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


# ── News feed ────────────────────────────────────────────────────────────────

def _build_news_feed(prices_df: pd.DataFrame, sentiment_df: pd.DataFrame) -> html.Div:
    """Build chronological news feed with source tags."""
    items = []

    # Combine prices and sentiment into a feed sorted by date
    feed_rows = []

    if not prices_df.empty:
        for _, row in prices_df.iterrows():
            feed_rows.append({
                "date": str(row.get("document_date", "")),
                "source": str(row.get("source_document", "")),
                "type": "price",
                "text": str(row.get("raw_text", "")),
                "detail": f"{row.get('price_type', '')} — {row.get('value', '')} {row.get('unit', '')}",
            })

    if not sentiment_df.empty:
        for _, row in sentiment_df.iterrows():
            feed_rows.append({
                "date": str(row.get("document_date", "")),
                "source": str(row.get("source_document", "")),
                "type": "sentiment",
                "text": str(row.get("raw_text", "")),
                "detail": str(row.get("summary", "")),
            })

    # Sort by date descending, limit to 20
    feed_rows.sort(key=lambda r: r["date"], reverse=True)
    feed_rows = feed_rows[:20]

    for row in feed_rows:
        # Source tag
        source_name = Path(row["source"]).stem[:20] if row["source"] else "Unknown"
        date_short = row["date"][5:10] if len(row["date"]) >= 10 else row["date"]
        tag_color = "#3498db" if row["type"] == "price" else "#8e44ad"

        tag = html.Span(
            f"[{source_name}, {date_short}]",
            style={**_TAG_STYLE, "backgroundColor": tag_color, "color": "#fff"},
        )

        # Detail line
        detail = html.Span(row["detail"], style={"fontWeight": "600", "fontSize": "13px"})

        # Expandable raw text
        raw_text = html.Details([
            html.Summary("Show excerpt", style={"cursor": "pointer", "fontSize": "11px", "color": "#7f8c8d"}),
            html.P(row["text"], style={"fontSize": "12px", "color": "#555", "marginTop": "4px",
                                        "padding": "8px", "backgroundColor": "#f8f9fa",
                                        "borderRadius": "4px"}),
        ]) if row["text"] else None

        card_children = [tag, html.Span(" "), detail]
        if raw_text:
            card_children.append(raw_text)

        items.append(html.Div(card_children, style={
            **_CARD_STYLE, "padding": "10px 14px",
        }))

    return html.Div(items) if items else html.P("No news items.", style={"color": "#888"})


# ── Sentiment section ────────────────────────────────────────────────────────

def _build_sentiment_section(sent_df: pd.DataFrame) -> html.Div:
    """Build sentiment trend chart + theme tags."""
    children = []

    sent_df = sent_df.copy()
    sent_df["document_date"] = pd.to_datetime(sent_df["document_date"])

    # Sentiment score: bullish=+1, neutral=0, bearish=-1, weighted by confidence
    score_map = {"bullish": 1, "neutral": 0, "bearish": -1}
    sent_df["score"] = sent_df["sentiment"].map(score_map).fillna(0) * sent_df["confidence"].fillna(0.5)

    # Daily average score
    daily = sent_df.groupby("document_date")["score"].mean().reset_index()
    daily = daily.sort_values("document_date")

    if len(daily) >= 2:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=daily["document_date"], y=daily["score"],
            mode="lines+markers", name="Sentiment Score",
            line=dict(color="#3498db", width=2),
            fill="tozeroy",
            fillcolor="rgba(52, 152, 219, 0.1)",
        ))
        fig.add_hline(y=0, line_dash="dash", line_color="#bdc3c7")
        fig.update_layout(
            template="plotly_white", height=250,
            margin=dict(l=50, r=20, t=30, b=30),
            yaxis=dict(title="Score", range=[-1.1, 1.1]),
            xaxis=dict(title=""),
        )
        children.append(dcc.Graph(figure=fig, config={"displayModeBar": False}))
    elif len(daily) == 1:
        score = daily.iloc[0]["score"]
        label = "Bullish" if score > 0.2 else ("Bearish" if score < -0.2 else "Neutral")
        color = _SENTIMENT_COLORS.get(label.lower(), "#7f8c8d")
        children.append(html.Div([
            html.Span(f"Current: {label} ", style={"fontWeight": "700", "color": color}),
            html.Span(f"(score: {score:+.2f})", style={"color": "#888"}),
        ], style=_CARD_STYLE))

    # Theme tags
    if "theme" in sent_df.columns:
        theme_counts = sent_df["theme"].value_counts().head(8)
        if not theme_counts.empty:
            tags = []
            for theme, count in theme_counts.items():
                tags.append(html.Span(
                    f"{theme} ({count})",
                    style={**_TAG_STYLE, "backgroundColor": _THEME_COLOR, "color": "#fff"},
                ))
            children.append(html.Div([
                html.Span("Themes: ", style={"fontWeight": "600", "fontSize": "12px"}),
                *tags,
            ], style={"marginTop": "8px"}))

    return html.Div(children) if children else html.P("Insufficient data for sentiment chart.", style={"color": "#888"})


# ── Market expectations ──────────────────────────────────────────────────────

def _build_expectations(forecasts: pd.DataFrame) -> html.Div:
    """Build table of forecasts, targets, support, resistance."""
    rows = []
    for _, row in forecasts.iterrows():
        source_name = Path(str(row.get("source_document", ""))).stem[:20]
        date_short = str(row.get("document_date", ""))[:10]
        rows.append(html.Tr([
            html.Td(html.Span(
                f"[{source_name}, {date_short}]",
                style={**_TAG_STYLE, "backgroundColor": "#e67e22", "color": "#fff"},
            )),
            html.Td(str(row.get("price_type", "")).title(), style={"fontWeight": "600"}),
            html.Td(f"{row.get('value', '')}", style={"textAlign": "right"}),
            html.Td(str(row.get("unit", "")), style={"color": "#888"}),
            html.Td(str(row.get("tenor", "") or ""), style={"color": "#888"}),
        ]))

    if not rows:
        return html.P("No forecasts available.", style={"color": "#888"})

    table = html.Table([
        html.Thead(html.Tr([
            html.Th("Source"), html.Th("Type"), html.Th("Value"),
            html.Th("Unit"), html.Th("Tenor"),
        ], style={"borderBottom": "2px solid #3498db", "textAlign": "left"})),
        html.Tbody(rows),
    ], style={"width": "100%", "fontSize": "13px", "borderCollapse": "collapse"})

    return html.Div(table, style=_CARD_STYLE)


# ── SND snapshot ─────────────────────────────────────────────────────────────

def _build_snd_snapshot(geography: str, product: str) -> html.Div:
    """Build SND snapshot — pulls from existing SND data where available."""
    if geography == "Asia" and product == "Palm":
        try:
            from snd_data import load_snd
            snd = load_snd()
            if snd:
                items = []
                for item_name, df in snd.items():
                    latest = df.iloc[-1]
                    items.append(html.Tr([
                        html.Td(item_name, style={"fontWeight": "600"}),
                        html.Td(f"{latest['value']:,.0f} MT", style={"textAlign": "right"}),
                        html.Td(f"{latest['date'].strftime('%Y-%m')}", style={"color": "#888"}),
                    ]))

                table = html.Table([
                    html.Thead(html.Tr([
                        html.Th("Item"), html.Th("Value"), html.Th("Date"),
                    ], style={"borderBottom": "2px solid #3498db", "textAlign": "left"})),
                    html.Tbody(items),
                ], style={"width": "100%", "fontSize": "13px", "borderCollapse": "collapse"})

                return html.Div(table, style=_CARD_STYLE)
        except Exception:
            pass

    return html.P(
        f"SND data not yet available for {geography} + {product}. "
        "Document extractions may populate this section over time.",
        style={"color": "#888", "fontStyle": "italic"},
    )


# ── Filters ──────────────────────────────────────────────────────────────────

def _filter_doc_prices(df: pd.DataFrame, geography: str, product: str) -> pd.DataFrame:
    """Filter price extractions by geography and product."""
    if df.empty:
        return df
    mask = (df["geography"] == geography) & (df["product"] == product)
    return df[mask].copy()


def _filter_doc_sentiment(df: pd.DataFrame, geography: str, product: str) -> pd.DataFrame:
    """Filter sentiment extractions by geography and product."""
    if df.empty:
        return df
    mask = (df["geography"] == geography) & (df["product"] == product)
    return df[mask].copy()
