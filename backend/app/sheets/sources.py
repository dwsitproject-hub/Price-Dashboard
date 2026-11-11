"""The four Google Sheet workbooks the app reads (docs/07-SHEET-SOURCES.md), and how each is fetched.

Public (link-shared) workbooks are read through the CSV export by tab gid; private ones through the Sheets API
with the service account (ops.integration_secrets 'google_sa', or GOOGLE_SERVICE_ACCOUNT_FILE). Any workbook can
also be fed by an admin upload of its .xlsx export (intel.sheet_uploads), used when the live read is not possible.
"""
from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.core.db import get_engine
from app.integrations import gsheets

logger = logging.getLogger("app.sheets")


@dataclass(frozen=True)
class Tab:
    key: str
    title: str
    gid: int


@dataclass(frozen=True)
class Source:
    code: str
    title: str
    sheet_id: str
    access: str                     # public | private
    feeds: str                      # what it feeds, for the admin page
    tabs: tuple[Tab, ...] = field(default_factory=tuple)

    @property
    def url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/edit"


SOURCES: dict[str, Source] = {s.code: s for s in [
    Source("argus", "Argus Historical Prices", "1VfaXyEbok2X4rxn4W4tGekWIg_IiRpOjTtT4zyzgksc", "private",
           "Waste/Bio (all sub-sections)", (Tab("prices", "Prices", 78291884),)),
    Source("market_price", "KPN Dws – Market Price Report", "1josHJfUqq7b_PSMoIWF3gwFkFRRLUFNlYT9aSPdoMAY", "private",
           "Basis / Hedge Ratios · Tradable Prem", (Tab("premiums", "Premiums", 221189267),)),
    Source("china_parity", "China-Parity Sheet", "16r7vkke3Kc-TVB4vKsJI01ndTRxIeiaO4i6FoHYfhE4", "public",
           "Parity · Ind/China (China) and Product Spreads · China",
           (Tab("dashboard", "Dashboard Data", 466961326), Tab("cross_oil", "Cross-oil Spread", 1329015366),
            Tab("palm_import", "Palm Import Parity", 674600137),
            Tab("soyoil_import", "Soyoil Import Parity", 809669896))),
    Source("india_parity", "India-Parity Sheet", "1tY63YWHUm8LHEKUJAtObl4zsIiAyeVmZ", "public",
           "Product Spreads / Outright Prices · India, Parity · Ind/China (India)",
           (Tab("local", "data2", 998039497), Tab("dashboard", "Dashboard Data", 1151879540),
            Tab("calculation", "Calculation", 741962089))),
]}

SA_PROVIDER = "google_sa"


# ── Service-account key ─────────────────────────────────────────────────────────────────────────────────────

def service_account() -> tuple[dict | None, str]:
    """(key info or None, where it came from: 'admin' | 'file' | '')."""
    with get_engine().connect() as c:
        row = c.execute(text("SELECT secret_enc FROM ops.integration_secrets WHERE provider = :p"),
                        {"p": SA_PROVIDER}).first()
    if row:
        raw = decrypt(row.secret_enc)
        if raw:
            try:
                return gsheets.parse_service_account(raw), "admin"
            except gsheets.SheetError:
                logger.warning("stored service-account key is invalid")
    path = get_settings().google_service_account_file
    if path:
        p = Path(path)
        if p.is_file():
            try:
                return gsheets.parse_service_account(p.read_text(encoding="utf-8")), "file"
            except (OSError, gsheets.SheetError) as exc:
                logger.warning("GOOGLE_SERVICE_ACCOUNT_FILE unusable: %s", exc)
    return None, ""


def save_service_account(raw: str, user_id) -> str:
    info = gsheets.parse_service_account(raw)
    with get_engine().begin() as c:
        c.execute(text("INSERT INTO ops.integration_secrets (provider, secret_enc, label, updated_by) "
                       "VALUES (:p, :s, :l, :u) ON CONFLICT (provider) DO UPDATE SET secret_enc = EXCLUDED.secret_enc, "
                       "label = EXCLUDED.label, updated_by = EXCLUDED.updated_by, updated_at = now()"),
                  {"p": SA_PROVIDER, "s": encrypt(json.dumps(info)), "l": info["client_email"], "u": user_id})
    return info["client_email"]


def delete_service_account() -> bool:
    with get_engine().begin() as c:
        return bool(c.execute(text("DELETE FROM ops.integration_secrets WHERE provider = :p"),
                              {"p": SA_PROVIDER}).rowcount)


# ── Uploads (fallback) ──────────────────────────────────────────────────────────────────────────────────────

def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def grids_from_xlsx(source: Source, data: bytes) -> dict[str, list[list[str]]]:
    """The tabs this source needs, read from an .xlsx export of the workbook (values, not formulas)."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - corrupt or non-Excel file
        raise ValueError("The file could not be read as an Excel workbook.") from exc
    try:
        missing = [t.title for t in source.tabs if t.title not in wb.sheetnames]
        if missing:
            raise ValueError(f"Tab(s) {', '.join(missing)} not found. The workbook has: "
                             f"{', '.join(wb.sheetnames[:30])}")
        out = {}
        for t in source.tabs:
            rows = [[_cell(v) for v in r] for r in wb[t.title].iter_rows(values_only=True)]
            while rows and not any(rows[-1]):
                rows.pop()
            out[t.key] = rows
        return out
    finally:
        wb.close()


def save_upload(code: str, filename: str, grids: dict, user_id) -> None:
    with get_engine().begin() as c:
        c.execute(text("INSERT INTO intel.sheet_uploads (code, filename, grids, uploaded_by) "
                       "VALUES (:c, :f, CAST(:g AS jsonb), :u) ON CONFLICT (code) DO UPDATE SET "
                       "filename = EXCLUDED.filename, grids = EXCLUDED.grids, uploaded_by = EXCLUDED.uploaded_by, "
                       "uploaded_at = now()"), {"c": code, "f": filename[:255], "g": json.dumps(grids), "u": user_id})


def delete_upload(code: str) -> bool:
    with get_engine().begin() as c:
        return bool(c.execute(text("DELETE FROM intel.sheet_uploads WHERE code = :c"), {"c": code}).rowcount)


def _uploaded(code: str) -> dict | None:
    with get_engine().connect() as c:
        row = c.execute(text("SELECT grids FROM intel.sheet_uploads WHERE code = :c"), {"c": code}).first()
    return row.grids if row else None


# ── Fetch ───────────────────────────────────────────────────────────────────────────────────────────────────

class NotAvailable(Exception):
    """No way to read this source (private, no service account, no upload)."""


def fetch(source: Source, sa_client: gsheets.ServiceAccountClient | None) -> tuple[dict[str, list[list[str]]], str]:
    """({tab key: grid}, method). Live read first; the admin upload is the fallback."""
    errors = []
    if source.access == "public":
        try:
            return {t.key: gsheets.csv_to_grid(gsheets.fetch_public_csv(source.sheet_id, t.gid))
                    for t in source.tabs}, "csv"
        except gsheets.SheetError as exc:
            errors.append(str(exc))
    if sa_client is not None:
        try:
            titles = sa_client.tab_titles(source.sheet_id)
            grids = {}
            for t in source.tabs:
                title = titles.get(t.gid) or t.title
                grids[t.key] = sa_client.grid(source.sheet_id, title)
            return grids, "api"
        except gsheets.SheetError as exc:
            errors.append(str(exc))
    uploaded = _uploaded(source.code)
    if uploaded:
        return uploaded, "upload"
    if source.access == "private" and sa_client is None and not errors:
        raise NotAvailable("Private sheet: add the Google service-account key (Admin → Sheet sources) or upload "
                           "an .xlsx export.")
    raise gsheets.SheetError("; ".join(errors) or "No data source available.")
