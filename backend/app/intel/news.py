"""News / document intelligence — port of legacy scripts/doc_extract.py, doc_prompts.py and news_dashboard.py.

Documents (broker commentary, Oil World, energy/biofuels reports) are uploaded in the app instead of dropped
into a folder. Claude reads PDFs natively (document blocks) and returns JSON constrained by a schema, so product
and geography values always match the panel's filters. Prompts are the legacy ones; the JSON layout the legacy
spelled out in prose is now the schema. Failures are recorded on the document, not retried silently.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from datetime import date, datetime

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine
from app.core.errors import ApiError
from app.integrations import claude
from app.intel.jobs import register

GEOGRAPHIES = ["Asia", "America", "EU", "Africa"]
PRODUCTS = ["Palm", "Soybean", "Other Edible Oils", "Energy", "Biodiesel"]
DOC_TYPES = ["broker_commentary", "oil_world", "energy"]
PRICE_TYPES = ["spot", "forward", "forecast", "support", "resistance", "target"]
EXPECTATION_TYPES = ("forecast", "target", "support", "resistance")
MIME = {".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}

# (geography, product) → LSEG RICs (legacy news_dashboard.INSTRUMENT_MAP; empty cells omitted)
INSTRUMENT_MAP: dict[tuple[str, str], list[str]] = {
    ("Asia", "Palm"): ["FCPOc3", "DCPc3", "CPO-MYSTH-M2", "POL-MYRBD-M2", "CPO-ID-M2", "POL-IDRBD-M2"],
    ("Asia", "Soybean"): ["DBYc3", "JCI-SOA-DALN"],
    ("America", "Soybean"): ["BOc3", "Sc3", "SMc3", "BO-ARGUPR-C1", "S-ARGUPR-C1", "SM-ARGUPR-P1"],
    ("America", "Energy"): ["CLc3", "HOc3"],
    ("EU", "Other Edible Oils"): ["KTS-SOUABS-FOB"],
    ("EU", "Energy"): ["LGOc3", "LCOc3"],
}
RIC_NAMES = {
    "FCPOc3": "FCPO c3", "DCPc3": "Dalian Palm c3", "CPO-MYSTH-M2": "MY CPO Cash M2", "POL-MYRBD-M2": "MY Olein M2",
    "CPO-ID-M2": "ID CPO Cash M2", "POL-IDRBD-M2": "ID Olein M2", "DBYc3": "Dalian Soy Oil c3",
    "JCI-SOA-DALN": "CN Cash Soy Oil", "BOc3": "CBOT Soy Oil c3", "Sc3": "CBOT Soybeans c3", "SMc3": "CBOT Soy Meal c3",
    "BO-ARGUPR-C1": "SA Soy Oil c1", "S-ARGUPR-C1": "SA Soybeans c1", "SM-ARGUPR-P1": "SA Soy Meal",
    "CLc3": "WTI Crude c3", "HOc3": "Heating Oil c3", "LGOc3": "Gasoil c3", "LCOc3": "Brent Crude c3",
    "KTS-SOUABS-FOB": "Sunflower FOB Black Sea",
}

# ── Legacy prompts (doc_prompts.py), verbatim ───────────────────────────────
_BROKER = """You are extracting structured commodity market intelligence from a broker commentary document.
These documents are narrative-style reports from commodity brokers containing market views, price forecasts,
trade recommendations, and supply/demand commentary. They often embed specific price levels within prose.

Focus on:
- Price levels, targets, support/resistance levels mentioned in the text
- Broker's directional view (bullish/bearish) and rationale
- Supply/demand drivers mentioned (weather, policy, export pace, crush margins, etc.)
- Forward-looking statements and forecasts
- Cross-commodity comparisons and spread views"""
_OIL_WORLD = """You are extracting structured commodity market intelligence from an Oil World report.
Oil World is a benchmark reference for global oils and fats markets. These reports contain:
- Detailed supply/demand balance tables
- Production, export, import, and stock estimates
- Price assessments for multiple commodities across geographies
- Forecasts and revisions to prior estimates

Focus on:
- All price data in tables and text (spot prices, FOB/CIF values, forward prices)
- Supply/demand balance numbers (production, exports, imports, crushing, stocks)
- Revisions to estimates (up/down vs prior forecast)
- Key narratives about market direction
- Cross-geography comparisons"""
_ENERGY = """You are extracting structured commodity market intelligence from an energy/biofuels report.
These reports cover biodiesel, renewable fuels, energy markets, and their intersection with vegetable oils.

Focus on:
- Biodiesel prices (FAME, UCOME, RME, HVO, SAF, POME)
- Feedstock prices (UCO, tallow, palm oil, rapeseed, soybean oil)
- Energy prices (crude, gasoil, heating oil)
- Mandate/policy changes affecting biofuel demand
- Blending margins and arbitrage opportunities
- Regional demand patterns (EU mandates, US RFS, Indonesia B-series, etc.)"""
_RULES = """
Rules:
- Extract ALL price data points and sentiment signals. Be thorough.
- Never infer or hallucinate — only extract what is explicitly stated.
- If a date is unclear, use the document date.
- Map every product to the closest category: Palm (includes CPO, olein, stearin, PFAD, PKO, palm kernel),
  Soybean (includes soybean oil, soybean meal, soybeans, soy crush),
  Other Edible Oils (includes sunflower, rapeseed, canola, groundnut, cottonseed, rice bran, coconut, tallow),
  Energy (includes crude oil, gasoil, heating oil, naphtha, natural gas),
  Biodiesel (includes FAME, UCOME, RME, HVO, SAF, POME, UCO, biofuel mandates).
- Map geography: Asia (Malaysia, Indonesia, China, India, Philippines, etc.),
  America (USA, Brazil, Argentina, Canada),
  EU (European countries, UK, Turkey),
  Africa (Nigeria, Kenya, Egypt, etc.).
  Use 'Global' as sub_geography when region-specific is not clear.
- For prices: extract the numeric value, unit, and context. Include price changes if mentioned.
- For sentiment: assess the tone (bullish/bearish/neutral), assign a theme tag, and rate confidence 0.0-1.0.
"""
PROMPTS = {"broker_commentary": _BROKER + "\n" + _RULES, "oil_world": _OIL_WORLD + "\n" + _RULES,
           "energy": _ENERGY + "\n" + _RULES}
CLASSIFY = """Classify this document into one of these categories based on the first page text.

Categories:
- broker_commentary (narrative market commentary from a broker or trading house)
- oil_world (Oil World benchmark report with supply/demand tables)
- energy (energy, biofuels, biodiesel, or renewable fuels report)
- unknown (does not fit any category)

First page text:
{text}"""

_NULLABLE_STR = {"type": ["string", "null"]}
_NULLABLE_NUM = {"type": ["number", "null"]}
EXTRACTION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["prices", "sentiment"],
    "properties": {
        "prices": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["product", "geography", "sub_geography", "price_type", "value", "unit", "change", "tenor",
                         "broker", "raw_text"],
            "properties": {
                "product": {"type": "string", "enum": PRODUCTS},
                "geography": {"type": "string", "enum": GEOGRAPHIES},
                "sub_geography": {"type": "string", "description": "specific country or 'Global'"},
                "price_type": {"type": "string", "enum": PRICE_TYPES},
                "value": {"type": "number"},
                "unit": {"type": "string", "description": "e.g. USD/MT, MYR/MT, cents/lb, EUR/MT"},
                "change": {**_NULLABLE_NUM, "description": "price change if mentioned, else null"},
                "tenor": {**_NULLABLE_STR, "description": "e.g. prompt, M1, M2, Q1, 2026, or null"},
                "broker": {**_NULLABLE_STR, "description": "broker name or null"},
                "raw_text": {"type": "string", "description": "exact sentence/phrase from the document"},
            }}},
        "sentiment": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["product", "geography", "sentiment", "confidence", "theme", "timeframe", "summary", "raw_text"],
            "properties": {
                "product": {"type": "string", "enum": PRODUCTS},
                "geography": {"type": "string", "enum": GEOGRAPHIES},
                "sentiment": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                "confidence": {"type": "number", "description": "0.0 to 1.0"},
                "theme": {"type": "string", "description": "e.g. supply_tightness, demand_weakness, export_strong, "
                          "policy, weather, production_increase, crush_margin, inventory_draw, logistics, "
                          "biodiesel_mandate"},
                "timeframe": {"type": "string", "enum": ["short_term", "medium_term", "long_term"]},
                "summary": {"type": "string", "description": "one-sentence summary of the signal"},
                "raw_text": {"type": "string", "description": "exact sentence/phrase from the document"},
            }}},
    },
}
CLASSIFY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["category"],
                   "properties": {"category": {"type": "string", "enum": DOC_TYPES + ["unknown"]}}}

# ── Filename rules (legacy doc_extract.py) ──────────────────────────────────
_FILENAME_PATTERNS = {
    "broker_commentary": [r"comment", r"broker", r"market\s*view"],
    "oil_world": [r"oil\s*world", r"oilworld", r"ow_", r"weekly", r"monthly"],
    "energy": [r"argus", r"biofuel", r"biodiesel", r"energy", r"fame", r"ucome"],
}
_DATE_PATTERNS = [r"(\d{4}[-_]\d{2}[-_]\d{2})", r"(\d{2}[-_]\d{2}[-_]\d{4})", r"(\d{1,2}[-_]\d{1,2}[-_]\d{2,4})"]


def classify_by_filename(filename: str) -> str | None:
    low = filename.lower()
    for doc_type, patterns in _FILENAME_PATTERNS.items():
        if any(re.search(p, low) for p in patterns):
            return doc_type
    return None


def date_from_filename(filename: str) -> date | None:
    for pattern in _DATE_PATTERNS:
        m = re.search(pattern, filename)
        if not m:
            continue
        s = m.group(1).replace("_", "-")
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y"):
            try:
                d = datetime.strptime(s, fmt).date()
            except ValueError:
                continue
            if 2020 <= d.year <= 2030:
                return d
    return None


# ── Upload ──────────────────────────────────────────────────────────────────

def upload(filename: str, data: bytes, doc_type: str | None, doc_date: date | None, user_id) -> dict:
    from app.intel import jobs
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in MIME:
        raise ApiError(422, "bad_file_type", "Upload a PDF, Word (.docx) or Excel (.xlsx) document.")
    max_mb = get_settings().document_max_mb
    if len(data) > max_mb * 1024 * 1024:
        raise ApiError(413, "too_large", f"Documents are limited to {max_mb} MB.")
    if doc_type not in (None, "", "auto") and doc_type not in DOC_TYPES:
        raise ApiError(422, "bad_doc_type", f"doc_type must be auto or one of {', '.join(DOC_TYPES)}")
    sha = hashlib.sha256(data).hexdigest()
    if doc_type in (None, "", "auto"):
        guessed = classify_by_filename(filename)
        doc_type, type_source = (guessed, "filename") if guessed else (None, None)
    else:
        type_source = "user"
    d = doc_date or date_from_filename(filename) or date.today()
    with get_engine().begin() as c:
        existing = c.execute(text("SELECT id, filename FROM intel.documents WHERE sha256 = :s"), {"s": sha}).first()
        if existing:
            raise ApiError(409, "duplicate", f"This document was already uploaded as {existing.filename}.")
        did = c.execute(text(
            "INSERT INTO intel.documents (filename, sha256, mime, size_bytes, content, doc_type, type_source, "
            "doc_date, uploaded_by) VALUES (:f, :s, :m, :n, :c, :t, :ts, :d, :u) RETURNING id"),
            {"f": filename[:255], "s": sha, "m": MIME[ext], "n": len(data), "c": data, "t": doc_type,
             "ts": type_source, "d": d, "u": user_id}).scalar_one()
    jobs.enqueue("document_extract", trigger="upload", params={"document_id": str(did)}, user_id=user_id)
    return get_document(did)


def get_document(did) -> dict:
    with get_engine().connect() as c:
        r = c.execute(text("SELECT d.id, d.filename, d.mime, d.size_bytes, d.doc_type, d.type_source, d.doc_date, "
                           "d.status, d.error, d.num_prices, d.num_sentiment, d.uploaded_at, d.processed_at, "
                           "u.email AS uploaded_by FROM intel.documents d LEFT JOIN iam.users u ON u.id = d.uploaded_by "
                           "WHERE d.id = :id"), {"id": did}).mappings().first()
    if r is None:
        raise ApiError(404, "not_found", "Document not found.")
    return dict(r)


def list_documents(limit: int = 100) -> list[dict]:
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT d.id, d.filename, d.size_bytes, d.doc_type, d.type_source, d.doc_date, d.status, "
                              "d.error, d.num_prices, d.num_sentiment, d.uploaded_at, d.processed_at, "
                              "u.email AS uploaded_by FROM intel.documents d LEFT JOIN iam.users u ON u.id = d.uploaded_by "
                              "ORDER BY d.uploaded_at DESC LIMIT :n"), {"n": limit}).mappings().all()
    return [dict(r) for r in rows]


def delete_document(did) -> bool:
    with get_engine().begin() as c:
        return bool(c.execute(text("DELETE FROM intel.documents WHERE id = :id"), {"id": did}).rowcount)


def reprocess(did, user_id) -> dict:
    from app.intel import jobs
    with get_engine().begin() as c:
        n = c.execute(text("UPDATE intel.documents SET status = 'queued', error = NULL WHERE id = :id "
                           "AND status IN ('failed','succeeded')"), {"id": did}).rowcount
    if not n:
        raise ApiError(409, "busy", "The document is already queued or being processed.")
    jobs.enqueue("document_extract", params={"document_id": str(did)}, user_id=user_id)
    return get_document(did)


# ── Extraction job ──────────────────────────────────────────────────────────

def _text_of(mime: str, data: bytes) -> str:
    if mime.endswith("wordprocessingml.document"):
        from docx import Document
        return "\n\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs if p.text.strip())
    if mime.endswith("spreadsheetml.sheet"):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            rows = ["\t".join("" if v is None else str(v) for v in r) for r in ws.iter_rows(values_only=True)]
            rows = [r for r in rows if r.strip()]
            if rows:
                parts.append(f"[Sheet: {ws.title}]\n" + "\n".join(rows))
        wb.close()
        return "\n\n".join(parts)
    raise ValueError(mime)


def _first_page_text(mime: str, data: bytes) -> str:
    if mime == "application/pdf":
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(data))
            return (reader.pages[0].extract_text() or "") if reader.pages else ""
        except Exception:  # noqa: BLE001 - unreadable text layer; fall back to the default type
            return ""
    return _text_of(mime, data)[:3000]


def _set(did, **fields) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    with get_engine().begin() as c:
        c.execute(text(f"UPDATE intel.documents SET {sets} WHERE id = :id"), {**fields, "id": did})


@register("document_extract")
def extract(params: dict) -> dict:
    did = params["document_id"]
    with get_engine().connect() as c:
        d = c.execute(text("SELECT filename, mime, content, doc_type, doc_date FROM intel.documents WHERE id = :id"),
                      {"id": did}).first()
    if d is None:
        return {"skipped": "document deleted"}
    _set(did, status="processing", error=None)
    try:
        data = bytes(d.content)
        doc_type = d.doc_type
        if doc_type is None:                                     # legacy: filename rules, then Claude, then default
            first = _first_page_text(d.mime, data)[:3000]
            doc_type, src = "broker_commentary", "default"
            if first.strip():
                res = claude.run(system="Classify commodity market documents.", content=CLASSIFY.format(text=first),
                                 max_tokens=4000, effort="low", schema=CLASSIFY_SCHEMA)
                if res.data.get("category") in DOC_TYPES:
                    doc_type, src = res.data["category"], "model"
            _set(did, doc_type=doc_type, type_source=src)
        instruction = f"Extract all commodity market data from this document.\n\nFilename: {d.filename}"
        if d.mime == "application/pdf":
            content = [{"type": "document", "title": d.filename[:200],
                        "source": {"type": "base64", "media_type": "application/pdf",
                                   "data": base64.standard_b64encode(data).decode()}},
                       {"type": "text", "text": instruction}]
        else:
            body = _text_of(d.mime, data)
            if not body.strip():
                raise ValueError("The document contains no readable text.")
            content = f"{instruction}\n\nDocument text:\n{body}"
        res = claude.run(system=PROMPTS[doc_type] + f"\nDocument date: {d.doc_date.isoformat()}", content=content,
                         max_tokens=64000, effort="medium", schema=EXTRACTION_SCHEMA)
        prices, sentiment = res.data.get("prices", []), res.data.get("sentiment", [])
        with get_engine().begin() as c:
            c.execute(text("DELETE FROM intel.doc_prices WHERE document_id = :id"), {"id": did})
            c.execute(text("DELETE FROM intel.doc_sentiment WHERE document_id = :id"), {"id": did})
            if prices:
                c.execute(text(
                    "INSERT INTO intel.doc_prices (document_id, document_date, product, geography, sub_geography, "
                    "price_type, value, unit, change, tenor, broker, raw_text) VALUES (:document_id, :document_date, "
                    ":product, :geography, :sub_geography, :price_type, :value, :unit, :change, :tenor, :broker, :raw_text)"),
                    [{**p, "document_id": did, "document_date": d.doc_date, "unit": (p.get("unit") or "")[:40],
                      "tenor": (p.get("tenor") or None) and p["tenor"][:40],
                      "broker": (p.get("broker") or None) and p["broker"][:120],
                      "sub_geography": (p.get("sub_geography") or "")[:80]} for p in prices])
            if sentiment:
                c.execute(text(
                    "INSERT INTO intel.doc_sentiment (document_id, document_date, product, geography, sentiment, "
                    "confidence, theme, timeframe, summary, raw_text) VALUES (:document_id, :document_date, :product, "
                    ":geography, :sentiment, :confidence, :theme, :timeframe, :summary, :raw_text)"),
                    [{**s, "document_id": did, "document_date": d.doc_date, "theme": (s.get("theme") or "")[:80],
                      "confidence": min(max(float(s.get("confidence") or 0.5), 0.0), 1.0)} for s in sentiment])
            c.execute(text("UPDATE intel.documents SET status = 'succeeded', error = NULL, num_prices = :p, "
                           "num_sentiment = :s, model = :m, usage = CAST(:u AS jsonb), processed_at = now() WHERE id = :id"),
                      {"p": len(prices), "s": len(sentiment), "m": res.model, "u": json.dumps(res.usage), "id": did})
        return {"document_id": did, "doc_type": doc_type, "prices": len(prices), "sentiment": len(sentiment), **res.usage}
    except Exception as exc:
        _set(did, status="failed", error=str(exc)[:1000], processed_at=datetime.now())
        raise


# ── Panel (legacy build_news_panel) ─────────────────────────────────────────

def _f(v):
    return None if v is None else float(v)


def panel(geography: str, product: str) -> dict:
    from app.intel import snd
    if geography not in GEOGRAPHIES or product not in PRODUCTS:
        raise ApiError(422, "bad_filter", "Unknown geography or product.")
    rics = INSTRUMENT_MAP.get((geography, product), [])
    with get_engine().connect() as c:
        lseg = []
        if rics:
            rows = c.execute(text(
                "SELECT p.ric, p.trade_date, p.price::float8 AS price, i.native_unit FROM market.price_daily p "
                "JOIN market.instruments i ON i.ric = p.ric WHERE p.ric = ANY(:r) AND p.price IS NOT NULL "
                "ORDER BY p.ric, p.trade_date"), {"r": rics}).all()
            by: dict[str, dict] = {}
            for r in rows:
                s = by.setdefault(r.ric, {"ric": r.ric, "name": RIC_NAMES.get(r.ric, r.ric), "unit": r.native_unit,
                                          "x": [], "y": []})
                s["x"].append(r.trade_date.isoformat())
                s["y"].append(r.price)
            lseg = [by[r] for r in rics if r in by]
        prices = c.execute(text(
            "SELECT p.document_date, d.filename, p.price_type, p.value, p.unit, p.change, p.tenor, p.broker, "
            "p.sub_geography, p.raw_text FROM intel.doc_prices p JOIN intel.documents d ON d.id = p.document_id "
            "WHERE p.geography = :g AND p.product = :p ORDER BY p.document_date DESC, p.id"),
            {"g": geography, "p": product}).mappings().all()
        sent = c.execute(text(
            "SELECT s.document_date, d.filename, s.sentiment, s.confidence, s.theme, s.timeframe, s.summary, "
            "s.raw_text FROM intel.doc_sentiment s JOIN intel.documents d ON d.id = s.document_id "
            "WHERE s.geography = :g AND s.product = :p ORDER BY s.document_date DESC, s.id"),
            {"g": geography, "p": product}).mappings().all()
    feed = sorted([{"kind": "price", "date": r["document_date"].isoformat(), "source": r["filename"],
                    "detail": f"{r['price_type']} — {_f(r['value']):,.2f} {r['unit'] or ''}".strip(),
                    "raw_text": r["raw_text"]} for r in prices]
                  + [{"kind": "sentiment", "date": r["document_date"].isoformat(), "source": r["filename"],
                      "detail": r["summary"], "sentiment": r["sentiment"], "raw_text": r["raw_text"]} for r in sent],
                  key=lambda x: x["date"], reverse=True)[:20]
    daily: dict[str, list[float]] = {}
    themes: dict[str, int] = {}
    for r in sent:          # legacy: {bullish: 1, neutral: 0, bearish: -1} × confidence (missing → 0.5)
        score = {"bullish": 1, "neutral": 0, "bearish": -1}[r["sentiment"]] * (
            float(r["confidence"]) if r["confidence"] is not None else 0.5)
        daily.setdefault(r["document_date"].isoformat(), []).append(score)
        if r["theme"]:
            themes[r["theme"]] = themes.get(r["theme"], 0) + 1
    sentiment = [{"date": d, "score": round(sum(v) / len(v), 4)} for d, v in sorted(daily.items())]
    doc_prices: dict[str, dict] = {}
    for r in reversed(prices):
        s = doc_prices.setdefault(r["price_type"], {"price_type": r["price_type"], "x": [], "y": [], "unit": []})
        s["x"].append(r["document_date"].isoformat())
        s["y"].append(_f(r["value"]))
        s["unit"].append(r["unit"])
    expectations = [{"date": r["document_date"].isoformat(), "source": r["filename"], "type": r["price_type"],
                     "value": _f(r["value"]), "unit": r["unit"], "tenor": r["tenor"]}
                    for r in prices if r["price_type"] in EXPECTATION_TYPES][:50]
    snapshot = snd.latest_end_stock("Malaysia") if (geography, product) == ("Asia", "Palm") else []
    return {"geography": geography, "product": product, "lseg": lseg, "doc_prices": list(doc_prices.values()),
            "feed": feed, "sentiment": sentiment,
            "themes": sorted(themes.items(), key=lambda kv: (-kv[1], kv[0]))[:8],
            "expectations": expectations, "snd": snapshot}
