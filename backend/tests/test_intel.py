"""Intelligence sections: SND parsing, CFTC, AI analysis (vs legacy), News extraction, Calendar, Atlas, jobs.

Claude is never called: tests replace app.integrations.claude.run with a stand-in.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import pytest
from sqlalchemy import text

from app.core.db import get_engine
from app.integrations import claude

LEGACY = Path(__file__).resolve().parents[2] / "legacy"


@pytest.fixture()
def claude_on(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    calls = []

    def fake_run(**kw):
        calls.append(kw)
        return fake_run.reply(**kw)
    fake_run.calls = calls
    monkeypatch.setattr(claude, "run", fake_run)
    return fake_run


def _drain():
    from app.intel import jobs
    return jobs.drain()


# ── SND ─────────────────────────────────────────────────────────────────────

def _csv(rows):
    buf = io.StringIO()
    import csv
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode()


def test_snd_uploads_parse_like_legacy(admin_client):
    # Malaysia year sheet: title rows 0–2 (row 2 = headers), item rows per MY_YEAR_ITEMS, values in '000 MT.
    my = [["Malaysia SND"], ["'000 MT"], [""] + ["Jan", "Feb", "Mar"] + [""] * 17]
    my += [[f"row{i}"] + [""] * 20 for i in range(3, 25)]
    my[4][1:4] = ["1,500", "1,600", ""]     # Opening Stock (row 4): Mar empty → dropped
    my[18][1:3] = ["1,700", "1,650"]         # End Stock (row 18)
    r = admin_client.post("/api/intel/snd/sources", data={"role": "malaysia", "year": "2026"},
                          files={"file": ("my2026.csv", _csv(my), "text/csv")})
    assert r.status_code == 201, r.text
    # UOB history workbook: dates in row 1 (cols 1+), values already MT.
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["UOB"])
    ws.append(["", date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1)])
    for i in range(2, 16):
        ws.append([f"item{i}", 1000 * i, 1000 * i + 1, 999])
    buf = io.BytesIO()
    wb.save(buf)
    r = admin_client.post("/api/intel/snd/sources", data={"role": "malaysia_history"},
                          files={"file": ("UOB.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 201, r.text
    g = admin_client.get("/api/intel/snd/graphs?years=2025,2026").json()
    mal = next(c for c in g["countries"] if c["name"] == "Malaysia")
    opening = next(i for i in mal["items"] if i["name"] == "Opening Stock")
    t26 = next(t for t in opening["traces"] if t["year"] == 2026)
    # Jan 2026: history (999, row 2) wins over the year sheet (legacy drop_duplicates keep='first'); Feb from the sheet × 1000.
    assert t26["y"][0] == 999 and t26["y"][1] == 1_600_000 and t26["y"][2] is None
    t25 = next(t for t in opening["traces"] if t["year"] == 2025)
    assert t25["y"][10] == 2000 and t25["y"][11] == 2001
    tables = admin_client.get("/api/intel/snd/tables").json()["tables"]
    myt = next(t for t in tables if t["code"] == "malaysia")
    assert myt["year"] == 2026 and myt["headers"][:4] == ["", "Jan", "Feb", "Mar"] and len(myt["headers"]) == 21
    assert len(myt["rows"]) == 21 and myt["bmd"] is not None
    assert admin_client.delete("/api/intel/snd/sources/malaysia/2026").status_code == 200
    assert admin_client.post("/api/intel/snd/sources", data={"role": "nope", "year": "2026"},
                             files={"file": ("x.csv", _csv(my), "text/csv")}).status_code == 422


# ── CFTC ────────────────────────────────────────────────────────────────────

CFTC_HEADER = ("Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,CFTC_Contract_Market_Code,Open_Interest_All,"
               "Prod_Merc_Positions_Long_All,Prod_Merc_Positions_Short_All,Swap_Positions_Long_All,"
               "Swap__Positions_Short_All,Swap__Positions_Spread_All,M_Money_Positions_Long_All,"
               "M_Money_Positions_Short_All,M_Money_Positions_Spread_All,Other_Rept_Positions_Long_All,"
               "Other_Rept_Positions_Short_All,Other_Rept_Positions_Spread_All,NonRept_Positions_Long_All,"
               "NonRept_Positions_Short_All\n")


def test_cftc_parse_and_metrics(admin_client):
    from app.intel import cftc
    raw = (CFTC_HEADER
           + '"PALM OIL - CME",2030-01-07,037021,1000,100,300,50,20,5,400,100,10,30,20,1,40,60\n'
           + '"PALM OIL - CME",2030-01-14,037021,1200,100,350,60,20,5,500,100,10,30,20,1,40,60\n'
           + '"NOT TRACKED",2030-01-14,999999,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n').encode("latin-1")
    rows = cftc.parse_history_file(raw)
    assert [r["cftc_code"] for r in rows] == ["037021", "037021"] and rows[1]["mm_long"] == 500
    cftc._upsert(rows)
    body = admin_client.get("/api/intel/cftc").json()
    palm = next(p for p in body["products"] if p["code"] == "037021")
    assert palm["report_date"] == "2030-01-14"
    assert palm["mm_net"] == 400 and palm["wow_mm_net"] == 100          # 500-100 vs 400-100
    assert palm["pm_net"] == -250 and palm["mm_net_pct_oi"] == pytest.approx(400 / 1200 * 100, abs=1e-3)
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM intel.cftc_positions WHERE report_date >= '2030-01-01'"))


# ── AI analysis ─────────────────────────────────────────────────────────────

def test_ai_sections_match_legacy(admin_client, monkeypatch):
    for name in ("plotly", "plotly.graph_objects"):
        monkeypatch.setitem(sys.modules, name, mock.MagicMock())
    monkeypatch.syspath_prepend(str(LEGACY / "scripts"))
    monkeypatch.syspath_prepend(str(LEGACY))
    import ai_analysis as legacy
    from app.intel import ai
    key = "outright::FCPOc3"
    full = ai.load_series(key)
    window = full.tail(252).reset_index(drop=True)
    assert ai.compute_seasonal(full) == legacy.compute_seasonal(full)
    assert ai.compute_extremes(window) == legacy.compute_extremes(window)
    assert ai.compute_highs_lows(window) == legacy.compute_highs_lows(window)
    assert ai.compute_momentum(window) == legacy.compute_momentum(window)
    assert ai.compute_support_resistance(window) == legacy.compute_support_resistance(window)
    ours, _ = ai.compute_year_resemblance(full)
    theirs = legacy.compute_year_resemblance(full)
    assert ours["top_matches"] == theirs["top_matches"] and ours["ytd_days"] == theirs["ytd_days"]


def test_ai_report_without_claude_is_skipped(admin_client):
    groups = admin_client.get("/api/intel/ai/instruments").json()["groups"]
    assert groups[0]["group"] == "Outright Prices" and any(o["key"] == "outright::FCPOc3" for o in groups[0]["options"])
    r = admin_client.post("/api/intel/ai/reports", json={"instrument": "outright::FCPOc3", "timeframe": "1Y"})
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["status"] == "skipped" and "## 7. MOMENTUM & TREND" in rep["raw_text"]
    assert set(rep["sections"]) >= {"seasonal", "extremes", "highs_lows", "oi", "support_resistance",
                                    "year_resemblance", "momentum", "chart"}
    assert rep["sections"]["oi"]["latest_signal"] in ("Long Buildup", "Short Buildup", "Short Covering",
                                                      "Long Unwinding", "Neutral")
    assert admin_client.post("/api/intel/ai/reports", json={"instrument": "outright::NOPE", "timeframe": "1Y"}
                             ).status_code == 404


def test_ai_report_narrative_job(admin_client, claude_on):
    claude_on.reply = lambda **kw: claude.ClaudeResult(text="## 1. Seasonal\nFine.", model="claude-opus-5-5",
                                                       usage={"input_tokens": 10, "output_tokens": 5})
    rep = admin_client.post("/api/intel/ai/reports", json={"instrument": "spread::BOPO_c3", "timeframe": "3M"}).json()
    assert rep["status"] == "pending"
    _drain()
    done = admin_client.get(f"/api/intel/ai/reports/{rep['id']}").json()
    assert done["status"] == "ready" and done["narrative"].startswith("## 1.")
    assert "--- COMPUTED STATISTICS ---" in claude_on.calls[-1]["content"]


# ── News ────────────────────────────────────────────────────────────────────

def test_news_filename_rules():
    from app.intel import news
    assert news.classify_by_filename("CGS_broker_comment.pdf") == "broker_commentary"
    assert news.classify_by_filename("argus_biofuels.pdf") == "energy"
    assert news.classify_by_filename("random.pdf") is None
    assert news.date_from_filename("report_2026-04-07.pdf") == date(2026, 4, 7)
    assert news.date_from_filename("ow_07_04_2026.pdf") == date(2026, 4, 7)
    assert news.date_from_filename("no-date.pdf") is None


def test_news_upload_extract_and_panel(admin_client, claude_on):
    def reply(**kw):
        assert kw["schema"] is not None and kw["content"][0]["type"] == "document"
        return claude.ClaudeResult(text="", model="claude-opus-5-5", usage={"input_tokens": 100, "output_tokens": 50},
                                   data={"prices": [{"product": "Palm", "geography": "Asia", "sub_geography": "Malaysia",
                                                     "price_type": "target", "value": 4300, "unit": "MYR/MT",
                                                     "change": None, "tenor": "M3", "broker": "CGS",
                                                     "raw_text": "FCPO testing MYR4,300."}],
                                         "sentiment": [{"product": "Palm", "geography": "Asia", "sentiment": "bullish",
                                                        "confidence": 0.7, "theme": "export_strong",
                                                        "timeframe": "short_term", "summary": "Exports strong.",
                                                        "raw_text": "Exports up 18% m/m."}]})
    claude_on.reply = reply
    pdf = b"%PDF-1.4\n% test broker note\n"
    r = admin_client.post("/api/intel/news/documents", data={"doc_type": "auto"},
                          files={"file": ("broker_comment_2030-04-07.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["doc_type"] == "broker_commentary" and doc["type_source"] == "filename" and doc["doc_date"] == "2030-04-07"
    assert admin_client.post("/api/intel/news/documents", files={"file": ("copy.pdf", pdf, "application/pdf")}
                             ).status_code == 409                                   # same content
    _drain()
    docs = admin_client.get("/api/intel/news/documents").json()["documents"]
    mine = next(d for d in docs if d["id"] == doc["id"])
    assert mine["status"] == "succeeded" and mine["num_prices"] == 1 and mine["num_sentiment"] == 1
    p = admin_client.get("/api/intel/news/panel?geography=Asia&product=Palm").json()
    assert p["feed"][0]["date"] == "2030-04-07"
    assert any(e["type"] == "target" and e["value"] == 4300 for e in p["expectations"])
    assert p["sentiment"][-1] == {"date": "2030-04-07", "score": 0.7}
    assert p["lseg"] and p["lseg"][0]["ric"] == "FCPOc3"
    assert admin_client.delete(f"/api/intel/news/documents/{doc['id']}").status_code == 200
    assert admin_client.post("/api/intel/news/documents", files={"file": ("x.txt", b"hi", "text/plain")}
                             ).status_code == 422


# ── Calendar ────────────────────────────────────────────────────────────────

def test_calendar_refresh(admin_client, claude_on):
    today = date.today()
    events = [{"date": (today + timedelta(days=2)).isoformat(), "event": "WASDE", "agency": "USDA", "region": "US",
               "category": "agri", "source_url": "https://www.usda.gov/oce/commodity/wasde", "notes": "12:00 ET"},
              {"date": (today + timedelta(days=90)).isoformat(), "event": "Too far", "agency": "X", "region": "US",
               "category": "Macro", "source_url": "", "notes": ""},
              {"date": "not-a-date", "event": "Bad", "category": "Macro"},
              {"date": (today + timedelta(days=5)).isoformat(), "event": "MPOB S&D", "agency": "MPOB", "region": "Mars",
               "category": "Weird", "source_url": "ftp://x", "notes": ""}]
    claude_on.reply = lambda **kw: claude.ClaudeResult(
        text="```json\n" + json.dumps({"events": events}) + ",\n```", model="claude-opus-5-5", usage={})
    r = admin_client.post("/api/intel/calendar/refresh")
    assert r.status_code == 202, r.text
    _drain()
    assert claude_on.calls[-1]["tools"][0]["type"] == "web_search_20260209"
    body = admin_client.get("/api/intel/calendar").json()
    assert [e["event"] for e in body["events"]] == ["WASDE", "MPOB S&D"]
    mpob = body["events"][1]
    assert mpob["category"] == "Agri" and mpob["region"] == "Global" and mpob["source_url"] == ""
    assert body["events"][0]["category"] == "Agri" and body["job"]["status"] == "succeeded"


# ── Atlas, permissions, jobs ────────────────────────────────────────────────

def test_atlas_and_permissions(admin_client):
    body = admin_client.get("/api/intel/atlas").json()
    assert len(body["plants"]) == 94 and body["snapshot"] == "Jul 2026"
    assert admin_client.put("/api/intel/atlas", json={"plants": [{"name": "X", "lat": 1, "lon": 2, "status": "zz",
                                                                    "type": "standalone", "kt": 1, "country": "Y"}]}
                            ).status_code == 422


def test_viewer_cannot_use_intel_without_modules(admin_client):
    import uuid

    from fastapi.testclient import TestClient

    from app.main import app
    from conftest import login
    email, pw = f"viewer.intel.{uuid.uuid4().hex[:6]}@aastar-test.com", "Viewer-Intel-Pass-9"
    r = admin_client.post("/api/admin/users", json={"email": email, "display_name": "Viewer Intel", "role": "viewer",
                                                     "auth_provider": "local", "password": pw})
    assert r.status_code == 201, r.text
    with TestClient(app, base_url="http://testserver", headers={"Origin": "http://testserver"}) as v:
        assert login(v, email, pw).status_code == 200
        assert v.get("/api/intel/cftc").status_code == 403            # viewer defaults exclude the new modules
        assert v.post("/api/intel/cftc/refresh").status_code == 403
        assert v.post("/api/intel/news/documents", files={"file": ("a.pdf", b"%PDF", "application/pdf")}
                      ).status_code == 403


def test_job_enqueue_deduplicates():
    from app.intel import jobs
    a = jobs.enqueue("cftc_refresh", params={"years": [1999]})
    b = jobs.enqueue("cftc_refresh", params={"years": [1999]})
    assert a == b
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM ops.jobs WHERE id = :id"), {"id": a})
