"""DWS Downstream Hub SSO — strict OIDC contract (Authorization Code + PKCE S256, JWKS-verified ID token).

The backend is the OIDC client (BFF): the PKCE verifier and the ID token never reach the browser.
See docs/03-TECHNICAL-DESIGN.md §4.3 and the Hub's SSO-TARGET-APP-INTEGRATION.md.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt, random_token, sha256
from app.models import OidcLoginState, Role, User
from app.services import audit, sessions
from app.services import settings as runtime_settings
from app.services.audit import RequestMeta

logger = logging.getLogger("app.sso")

ALLOWED_ALGS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "EdDSA"]
STATE_TTL = timedelta(minutes=10)
LEEWAY_SECONDS = 60


class SsoError(Exception):
    """Carries a stable error code for /login?error=<code>."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


class HubOidcClient:
    """Discovery + JWKS cache. One instance per process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._discovery: Discovery | None = None
        self._discovery_at = 0.0
        self._jwks: dict[str, dict] = {}
        self._jwks_at = 0.0

    def _http(self) -> httpx.Client:
        return httpx.Client(timeout=get_settings().sso_http_timeout_seconds, follow_redirects=False)

    def discovery(self, force: bool = False) -> Discovery:
        with self._lock:
            if self._discovery and not force and time.monotonic() - self._discovery_at < 3600:
                return self._discovery
        issuer = get_settings().hub_issuer.rstrip("/")
        url = f"{issuer}/api/sso/.well-known/openid-configuration"
        try:
            with self._http() as c:
                r = c.get(url)
            r.raise_for_status()
            doc = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SsoError("sso_unavailable", f"Downstream Hub discovery failed: {exc}") from exc
        d = Discovery(doc["issuer"], doc["authorization_endpoint"], doc["token_endpoint"], doc["jwks_uri"])
        with self._lock:
            self._discovery, self._discovery_at = d, time.monotonic()
        return d

    def _signing_key(self, kid: str | None):
        now = time.monotonic()
        if kid not in self._jwks or now - self._jwks_at > 600:
            self._refresh_jwks()
        if kid not in self._jwks:           # key rotated since our cache — one forced refresh
            self._refresh_jwks()
        jwk = self._jwks.get(kid) or (next(iter(self._jwks.values())) if kid is None and len(self._jwks) == 1
                                      else None)
        if jwk is None:
            raise SsoError("sso_token_invalid", "ID token signed with an unknown key.")
        return PyJWK(jwk)

    def _refresh_jwks(self) -> None:
        try:
            with self._http() as c:
                r = c.get(self.discovery().jwks_uri)
            r.raise_for_status()
            keys = r.json().get("keys", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise SsoError("sso_unavailable", f"Downstream Hub JWKS fetch failed: {exc}") from exc
        with self._lock:
            self._jwks = {k.get("kid"): k for k in keys}
            self._jwks_at = time.monotonic()

    def exchange_code(self, code: str, verifier: str) -> str:
        s = get_settings()
        body = {"grant_type": "authorization_code", "code": code, "redirect_uri": s.hub_redirect_uri,
                "client_id": s.hub_client_id, "code_verifier": verifier}
        try:
            with self._http() as c:
                r = c.post(self.discovery().token_endpoint, json=body)
        except httpx.HTTPError as exc:
            raise SsoError("sso_unavailable", f"Token endpoint unreachable: {exc}") from exc
        if r.status_code != 200:
            err = ""
            try:
                err = r.json().get("error", "")
            except ValueError:
                pass
            raise SsoError("sso_token_failed", f"Token exchange failed ({r.status_code} {err}).")
        id_token = r.json().get("id_token")
        if not id_token:
            raise SsoError("sso_token_failed", "Token response had no id_token.")
        return id_token

    def verify_id_token(self, id_token: str, nonce: str) -> dict:
        s = get_settings()
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise SsoError("sso_token_invalid", "Malformed ID token.") from exc
        alg = header.get("alg")
        if alg not in ALLOWED_ALGS:
            raise SsoError("sso_token_invalid", f"ID token algorithm {alg!r} is not allowed.")
        key = self._signing_key(header.get("kid"))
        try:
            claims = jwt.decode(id_token, key=key.key, algorithms=[alg], audience=s.hub_client_id,
                                issuer=self.discovery().issuer, leeway=LEEWAY_SECONDS,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except jwt.PyJWTError as exc:
            raise SsoError("sso_token_invalid", f"ID token rejected: {exc}") from exc
        if not claims.get("sub"):
            raise SsoError("sso_token_invalid", "ID token has no sub.")
        if claims.get("nonce") is not None and claims["nonce"] != nonce:
            raise SsoError("sso_token_invalid", "ID token nonce mismatch.")
        return claims


hub_client = HubOidcClient()


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def safe_next(path: str | None) -> str:
    """Only same-site relative paths (prevents open redirects, PRD SSO-09)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path or len(path) > 512:
        return "/"
    if path.startswith("/api/"):
        return "/"
    return path


def begin(db: DbSession, next_path: str | None, meta: RequestMeta) -> str:
    s = get_settings()
    if not s.sso_configured:
        raise SsoError("sso_disabled", "Downstream Hub SSO is not configured for this environment.")
    d = hub_client.discovery()
    state, nonce, verifier = random_token(), random_token(), random_token(48)
    db.add(OidcLoginState(state_hash=sha256(state), code_verifier_enc=encrypt(verifier), nonce=nonce,
                          next_path=safe_next(next_path), ip=meta.ip,
                          expires_at=datetime.now(UTC) + STATE_TTL))
    db.commit()
    params = {"response_type": "code", "client_id": s.hub_client_id, "redirect_uri": s.hub_redirect_uri,
              "code_challenge": _pkce_challenge(verifier), "code_challenge_method": "S256",
              "state": state, "nonce": nonce, "scope": "openid profile email"}
    return f"{d.authorization_endpoint}?{urlencode(params)}"


def _consume_state(db: DbSession, state: str) -> OidcLoginState:
    row = db.execute(
        update(OidcLoginState)
        .where(OidcLoginState.state_hash == sha256(state), OidcLoginState.consumed_at.is_(None),
               OidcLoginState.expires_at > func.now())
        .values(consumed_at=func.now())
        .returning(OidcLoginState.code_verifier_enc, OidcLoginState.nonce, OidcLoginState.next_path)
    ).first()
    db.commit()
    if row is None:
        raise SsoError("sso_state_invalid", "Sign-in link expired or already used. Please try again.")
    return OidcLoginState(code_verifier_enc=row[0], nonce=row[1], next_path=row[2])


def _resolve_user(db: DbSession, claims: dict, meta: RequestMeta) -> User:
    sub, email = str(claims["sub"]), (claims.get("email") or "").strip().lower()
    name = (claims.get("name") or email.split("@")[0] or "Hub user")[:120]

    user = db.scalar(select(User).where(User.hub_sub == sub))
    if user is None and claims.get("email_verified") is not True:
        # Hub SSO v2 (§4): bind by email or JIT-create only once the Hub has proven inbox ownership.
        audit.record(db, "SSO_REJECTED", actor_email=email or None, outcome="failure", meta=meta,
                     details={"reason": "email_unverified", "sub": sub})
        db.commit()
        raise SsoError("sso_email_unverified",
                       "Verify your email in Downstream Hub first (use the link the Hub emailed you), then retry.")
    if user is None and email:
        candidate = db.scalar(select(User).where(func.lower(User.email) == email))
        if candidate is not None:
            if candidate.hub_sub not in (None, sub):
                raise SsoError("sso_not_registered", "This email is linked to a different Hub account.")
            if candidate.auth_provider == "local":
                raise SsoError("sso_not_registered",
                               "This account is set to password sign-in only. Ask an administrator to "
                               "enable Downstream Hub SSO for it.")
            candidate.hub_sub = sub
            audit.record(db, "SSO_LINK", actor_id=candidate.id, actor_email=candidate.email, target_type="user",
                         target_id=str(candidate.id), meta=meta)
            user = candidate
    if user is None:
        vals = runtime_settings.all_values(db)
        if not vals.get("sso.auto_provision") or not email:
            audit.record(db, "SSO_REJECTED", actor_email=email or None, outcome="failure", meta=meta,
                         details={"reason": "not_registered", "sub": sub})
            db.commit()
            raise SsoError("sso_not_registered",
                           "Your Downstream Hub account is not registered in AASTAR. Contact an administrator.")
        role = db.scalar(select(Role).where(Role.code == vals.get("sso.default_role", "viewer")))
        if role is None or "admin" in (role.permissions or []):
            role = db.scalar(select(Role).where(Role.code == "viewer"))
        user = User(email=email, display_name=name, role_id=role.id, auth_provider="sso", hub_sub=sub)
        user.role = role
        db.add(user)
        db.flush()
        audit.record(db, "USER_CREATE", actor_id=user.id, actor_email=email, target_type="user",
                     target_id=str(user.id), meta=meta, details={"source": "sso_auto_provision",
                                                                 "role": role.code})
    if user.status != "active":
        audit.record(db, "SSO_REJECTED", actor_id=user.id, actor_email=user.email, outcome="failure",
                     meta=meta, details={"reason": "disabled"})
        db.commit()
        raise SsoError("sso_disabled_user", "Your AASTAR account is disabled. Contact an administrator.")
    return user


def complete(db: DbSession, code: str, state: str, meta: RequestMeta) -> tuple[User, sessions.IssuedSession, str]:
    if not code or not state:
        raise SsoError("sso_state_invalid", "Missing code or state.")
    st = _consume_state(db, state)
    verifier = decrypt(st.code_verifier_enc)
    if verifier is None:
        raise SsoError("sso_state_invalid", "Sign-in state could not be read.")
    id_token = hub_client.exchange_code(code, verifier)
    claims = hub_client.verify_id_token(id_token, st.nonce)
    user = _resolve_user(db, claims, meta)
    user.last_login_at, user.last_login_method = datetime.now(UTC), "sso"
    user.failed_login_count, user.locked_until = 0, None
    issued = sessions.create(db, user, "sso", meta)
    audit.record(db, "SSO_LOGIN", actor_id=user.id, actor_email=user.email, meta=meta,
                 details={"iss": claims.get("iss")})
    db.commit()
    return user, issued, safe_next(st.next_path)


def purge_expired_states(db: DbSession) -> int:
    from sqlalchemy import delete
    n = db.execute(delete(OidcLoginState).where(OidcLoginState.expires_at < func.now() - timedelta(days=1))
                   ).rowcount or 0
    db.commit()
    return n
