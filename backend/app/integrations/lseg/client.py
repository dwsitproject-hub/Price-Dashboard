"""LSEG DataScope Select client — port of legacy scripts/auth.py + scripts/fetch.py.

Changes vs legacy: credentials from env/KMS (no credentials.ini), bearer token cached *encrypted* in
ops.integration_tokens (no token.json), retry with exponential backoff, RIC chunking (≤100 per request)
and year chunking for full backfills (legacy hit ReadTimeout on a 345-RIC full pull).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.models import IntegrationToken

logger = logging.getLogger("app.lseg")

# Elektron Timeseries field names. NB: "Open Price"/"Close" are rejected by the API — use plain names.
FIELDS = ["RIC", "Trade Date", "Last", "Settlement Price", "Bid", "Ask", "Volume", "Open Interest"]
POLL_INTERVAL = 10
TOKEN_LIFETIME = timedelta(hours=23)      # tokens last 24 h; refresh 1 h early
RETRIES = 5


class LsegError(RuntimeError):
    pass


class LsegClient:
    def __init__(self, db: DbSession):
        self.db = db
        self.s = get_settings()
        self.base = self.s.lseg_base_url.rstrip("/")
        self.http = httpx.Client(timeout=httpx.Timeout(300, connect=30))

    def close(self) -> None:
        self.http.close()

    # ── token ────────────────────────────────────────────────────────────────
    def token(self, force: bool = False) -> str:
        row = self.db.get(IntegrationToken, "lseg")
        if row and not force and row.expires_at > datetime.now(UTC):
            tok = decrypt(row.token_enc)
            if tok:
                return tok
        if not self.s.lseg_configured:
            raise LsegError("LSEG credentials are not configured (LSEG_USERNAME / LSEG_PASSWORD).")
        r = self._with_retry(lambda: self.http.post(
            f"{self.base}/Authentication/RequestToken",
            json={"Credentials": {"Username": self.s.lseg_username, "Password": self.s.lseg_password}}))
        if r.status_code in (400, 401, 403):
            raise LsegError(f"LSEG authentication failed ({r.status_code}). Check the LoginId (numeric) "
                            "and password.")
        if not r.is_success:
            raise LsegError(f"LSEG auth endpoint returned HTTP {r.status_code}.")
        tok = r.json()["value"]
        expires = datetime.now(UTC) + TOKEN_LIFETIME
        if row is None:
            self.db.add(IntegrationToken(provider="lseg", token_enc=encrypt(tok), expires_at=expires))
        else:
            row.token_enc, row.obtained_at, row.expires_at = encrypt(tok), datetime.now(UTC), expires
        self.db.commit()
        logger.info("LSEG token refreshed")
        return tok

    # ── extraction ───────────────────────────────────────────────────────────
    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Token {token}", "Content-Type": "application/json",
                "Prefer": "respond-async"}

    @staticmethod
    def _with_retry(fn):
        delay = 1.0
        for attempt in range(1, RETRIES + 1):
            try:
                r = fn()
                if r.status_code < 500:
                    return r
                logger.warning("LSEG HTTP %s (attempt %s)", r.status_code, attempt)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.warning("LSEG transport error %s (attempt %s)", type(exc).__name__, attempt)
                if attempt == RETRIES:
                    raise LsegError(f"LSEG unreachable after {RETRIES} attempts: {exc}") from exc
            time.sleep(delay)
            delay *= 2
        raise LsegError(f"LSEG kept failing after {RETRIES} attempts.")

    def _body(self, rics: list[str], start: date, end: date) -> dict:
        return {"ExtractionRequest": {
            "@odata.type": "#DataScope.Select.Api.Extractions.ExtractionRequests.ElektronTimeseriesExtractionRequest",
            "ContentFieldNames": FIELDS,
            "IdentifierList": {
                "@odata.type": "#DataScope.Select.Api.Extractions.ExtractionRequests.InstrumentIdentifierList",
                "InstrumentIdentifiers": [{"Identifier": r, "IdentifierType": "Ric"} for r in rics],
                "ValidationOptions": None, "UseUserPreferencesForValidationOptions": False},
            "Condition": {"ReportDateRangeType": "Range", "StartDate": f"{start.isoformat()}T00:00:00.000Z",
                          "EndDate": f"{end.isoformat()}T00:00:00.000Z"},
        }}

    def extract(self, rics: list[str], start: date, end: date) -> list[dict]:
        token = self.token()
        url = f"{self.base}/Extractions/ExtractWithNotes"
        r = self._with_retry(lambda: self.http.post(url, headers=self._headers(token),
                                                    json=self._body(rics, start, end)))
        if r.status_code == 401:
            token = self.token(force=True)
            r = self._with_retry(lambda: self.http.post(url, headers=self._headers(token),
                                                        json=self._body(rics, start, end)))
        if r.status_code == 202:
            location = r.headers.get("Location")
            if not location:
                raise LsegError("LSEG returned 202 without a Location header.")
            data = self._poll(location, token)
        elif r.status_code == 200:
            data = r.json()
        else:
            raise LsegError(f"Extraction request failed (HTTP {r.status_code}).")
        return data.get("Contents", [])

    def _poll(self, location: str, token: str, max_wait_s: int = 1800) -> dict:
        waited = 0
        while waited < max_wait_s:
            time.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL
            r = self._with_retry(lambda: self.http.get(location, headers=self._headers(token)))
            if r.status_code == 200:
                return r.json()
            if r.status_code != 202:
                raise LsegError(f"Unexpected status while polling ({r.status_code}).")
        raise LsegError("Timed out waiting for the LSEG extraction.")

    def extract_chunked(self, rics: list[str], start: date, end: date) -> Iterator[list[dict]]:
        """Yield rows per (year window × RIC chunk) so a long backfill never sends one giant request."""
        chunk = max(1, self.s.lseg_ric_chunk)
        windows: list[tuple[date, date]] = []
        cur = start
        while cur <= end:
            w_end = min(date(cur.year, 12, 31), end)
            windows.append((cur, w_end))
            cur = date(cur.year + 1, 1, 1)
        for w_start, w_end in windows:
            for i in range(0, len(rics), chunk):
                part = rics[i:i + chunk]
                logger.info("LSEG extract %s RICs %s→%s", len(part), w_start, w_end)
                yield self.extract(part, w_start, w_end)
