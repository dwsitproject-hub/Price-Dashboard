"""Google Sheets access: public CSV export, and the Sheets API v4 with a service account (read-only).

The service-account flow is the standard OAuth 2.0 JWT bearer grant (RS256 assertion → access token), done with
PyJWT + httpx so no Google client library is needed. Values are read FORMATTED (as the legacy readers did), so
dates arrive as the sheet displays them and numbers keep their thousands separators.
"""
from __future__ import annotations

import csv
import io
import json
import time
from urllib.parse import quote

import httpx
import jwt

TOKEN_URL = "https://oauth2.googleapis.com/token"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sid}/export"
SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
TIMEOUT = httpx.Timeout(90.0, connect=15.0)


class SheetError(Exception):
    pass


def _with_retry(call, host: str, attempts: int = 3) -> httpx.Response:
    """Retry transient network failures (connect/read timeouts, resets) with a short backoff."""
    for i in range(attempts):
        try:
            return call()
        except httpx.TransportError as exc:
            if i == attempts - 1:
                raise SheetError(f"Could not reach {host} ({type(exc).__name__}).") from exc
            time.sleep(2 * (i + 1))
    raise AssertionError("unreachable")


def parse_service_account(raw: str) -> dict:
    """Validate a service-account key file's JSON; returns the parsed dict."""
    try:
        info = json.loads(raw)
    except ValueError as exc:
        raise SheetError("The key file is not valid JSON.") from exc
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise SheetError("This is not a Google service-account key (type must be 'service_account').")
    for k in ("client_email", "private_key", "token_uri"):
        if not info.get(k):
            raise SheetError(f"The key file has no '{k}'.")
    if "BEGIN PRIVATE KEY" not in info["private_key"]:
        raise SheetError("The key file's private_key is not a PEM private key.")
    return info


def csv_to_grid(text: str) -> list[list[str]]:
    return [row for row in csv.reader(io.StringIO(text))]


def fetch_public_csv(sheet_id: str, gid: int, client: httpx.Client | None = None) -> str:
    """CSV export of one tab of a link-shared sheet. A private sheet answers 401/403 (or a sign-in page)."""
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    try:
        r = _with_retry(lambda: client.get(EXPORT_URL.format(sid=sheet_id), params={"format": "csv", "gid": str(gid)}),
                        "docs.google.com")
    finally:
        if own:
            client.close()
    ctype = r.headers.get("content-type", "")
    if r.status_code in (401, 403) or "text/html" in ctype:
        raise SheetError("The sheet is not shared publicly (CSV export refused).")
    if r.status_code != 200:
        raise SheetError(f"CSV export failed with HTTP {r.status_code}.")
    r.encoding = "utf-8"
    return r.text


class ServiceAccountClient:
    def __init__(self, info: dict, http: httpx.Client | None = None):
        self.info = info
        self.http = http or httpx.Client(timeout=TIMEOUT)
        self._token: str | None = None
        self._exp = 0.0

    @property
    def email(self) -> str:
        return self.info["client_email"]

    def close(self) -> None:
        self.http.close()

    def token(self) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        now = int(time.time())
        assertion = jwt.encode({"iss": self.info["client_email"], "scope": SCOPE, "aud": TOKEN_URL,
                                "iat": now, "exp": now + 3600}, self.info["private_key"], algorithm="RS256",
                               headers={"kid": self.info.get("private_key_id")} if self.info.get("private_key_id")
                               else None)
        try:
            r = self.http.post(TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                "assertion": assertion})
        except httpx.HTTPError as exc:
            raise SheetError(f"Could not reach oauth2.googleapis.com ({type(exc).__name__}).") from exc
        if r.status_code != 200:
            detail = ""
            try:
                detail = r.json().get("error_description") or r.json().get("error") or ""
            except ValueError:
                pass
            raise SheetError(f"Google rejected the service-account key (HTTP {r.status_code}{': ' + detail if detail else ''}).")
        body = r.json()
        self._token, self._exp = body["access_token"], time.time() + int(body.get("expires_in", 3600))
        return self._token

    def _get(self, url: str, params: dict | None = None) -> dict:
        token = self.token()
        r = _with_retry(lambda: self.http.get(url, params=params, headers={"Authorization": f"Bearer {token}"}),
                        "sheets.googleapis.com")
        if r.status_code == 403:
            raise SheetError(f"The sheet is not shared with {self.email} (HTTP 403) — share it as Viewer.")
        if r.status_code == 404:
            raise SheetError("Spreadsheet not found (HTTP 404).")
        if r.status_code != 200:
            raise SheetError(f"Sheets API error HTTP {r.status_code}.")
        return r.json()

    def tab_titles(self, sheet_id: str) -> dict[int, str]:
        meta = self._get(f"{SHEETS_API}/{sheet_id}", {"fields": "sheets.properties(sheetId,title)"})
        return {s["properties"]["sheetId"]: s["properties"]["title"] for s in meta.get("sheets", [])}

    def grid(self, sheet_id: str, title: str) -> list[list[str]]:
        quoted = "'" + title.replace("'", "''") + "'"
        body = self._get(f"{SHEETS_API}/{sheet_id}/values/{quote(quoted, safe='')}",
                         {"valueRenderOption": "FORMATTED_VALUE", "dateTimeRenderOption": "FORMATTED_STRING"})
        return [[("" if c is None else str(c)) for c in row] for row in body.get("values", [])]
