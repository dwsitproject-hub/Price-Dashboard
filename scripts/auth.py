import configparser
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
CREDENTIALS_PATH = ROOT / "credentials.ini"
TOKEN_PATH = ROOT / "token.json"

AUTH_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Authentication/RequestToken"
TOKEN_LIFETIME_HOURS = 23  # tokens last 24 hrs; refresh 1 hr early


# ── Credentials ────────────────────────────────────────────────────────────────

def _load_credentials() -> tuple[str, str]:
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(f"credentials.ini not found at {CREDENTIALS_PATH}")

    config = configparser.ConfigParser()
    config.read(CREDENTIALS_PATH)

    try:
        username = config["credentials"]["username"].strip()
        password = config["credentials"]["password"].strip()
    except KeyError as e:
        raise KeyError(f"Missing key in credentials.ini: {e}")

    if not username or not password:
        raise ValueError(
            "credentials.ini has empty username or password. Please fill them in."
        )

    return username, password


# ── Token cache (token.json) ───────────────────────────────────────────────────

def _load_cached_token() -> str | None:
    """Return the cached token string if it exists and has not expired, else None."""
    if not TOKEN_PATH.exists():
        return None

    try:
        data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        expires_at = datetime.fromisoformat(data["expires_at"])
        if datetime.now(timezone.utc) < expires_at:
            return data["token"]
    except (KeyError, ValueError, json.JSONDecodeError):
        pass  # treat any malformed cache as missing

    return None


def _save_token(token: str) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=TOKEN_LIFETIME_HOURS)
    payload = {
        "token": token,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    TOKEN_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("Token saved to token.json")


# ── Auth request ───────────────────────────────────────────────────────────────

def _fetch_new_token() -> str:
    username, password = _load_credentials()

    print("Fetching new token...")
    try:
        response = requests.post(
            AUTH_URL,
            headers={"Content-Type": "application/json"},
            json={"Credentials": {"Username": username, "Password": password}},
            timeout=30,
        )
    except requests.ConnectionError:
        raise ConnectionError("Network error: could not reach the LSEG auth endpoint.")
    except requests.Timeout:
        raise TimeoutError("Request timed out while contacting the LSEG auth endpoint.")

    if not response.ok:
        # Try to extract LSEG's nested error message
        lseg_message = ""
        try:
            lseg_message = response.json()["error"]["message"]
        except (KeyError, ValueError):
            lseg_message = response.text[:300]

        if response.status_code == 400:
            raise ValueError(
                f"LSEG rejected the request (400): {lseg_message}\n"
                "Check that your username in credentials.ini matches the LoginId "
                "shown in your LSEG account (numeric ID only, no spaces)."
            )
        if response.status_code == 401:
            raise PermissionError(
                f"Authentication failed (401): {lseg_message}\n"
                "Check your password in credentials.ini."
            )
        if response.status_code == 403:
            raise PermissionError(
                f"Access denied (403): {lseg_message}\n"
                "Your account may not have API access."
            )
        raise RuntimeError(
            f"Unexpected response from auth endpoint: HTTP {response.status_code} — {lseg_message}"
        )

    try:
        token = response.json()["value"]
    except (KeyError, ValueError):
        raise RuntimeError(
            f"Could not parse token from response. Raw response: {response.text[:300]}"
        )

    return token


# ── Public API ─────────────────────────────────────────────────────────────────

def get_token() -> str:
    """
    Return a valid LSEG Bearer token string.
    Loads from token.json if cached and not expired; otherwise fetches a new one.
    """
    cached = _load_cached_token()
    if cached:
        print("Token loaded from cache.")
        return cached

    token = _fetch_new_token()
    _save_token(token)
    return token


if __name__ == "__main__":
    token = get_token()
    print(f"\nToken (first 40 chars): {token[:40]}...")