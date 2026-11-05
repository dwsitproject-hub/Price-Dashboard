"""Password policy, hashing, open-redirect protection, OIDC ID-token validation."""
from __future__ import annotations

import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.security import (generate_temporary_password, hash_password, password_policy_errors,
                               verify_password)
from app.services import sso


def test_password_policy():
    assert password_policy_errors("short1A!")
    assert password_policy_errors("alllowercaseletters")             # 1 class
    assert not password_policy_errors("Correct-Horse-9-Battery")
    assert password_policy_errors("jerry.smith-Pass1!", "jerry.smith@x.com")
    assert len(password_policy_errors("A" * 129 + "a1")) >= 1


def test_hash_and_verify():
    h = hash_password("Correct-Horse-9-Battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "Correct-Horse-9-Battery")
    assert not verify_password(h, "wrong")
    assert not verify_password(None, "anything")


def test_temporary_password_meets_policy():
    for _ in range(20):
        assert not password_policy_errors(generate_temporary_password())


@pytest.mark.parametrize("path,expected", [
    ("/calendar", "/calendar"), ("/m/crush?x=1", "/m/crush?x=1"), ("//evil.com", "/"),
    ("https://evil.com", "/"), ("/\\evil.com", "/"), (None, "/"), ("/api/admin/users", "/"),
])
def test_safe_next(path, expected):
    assert sso.safe_next(path) == expected


# ── OIDC ID token validation with a locally generated key pair ───────────────
ISSUER, CLIENT = "http://hub.test", "aastar-test"


@pytest.fixture()
def oidc(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_numbers()

    def b64(n: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()
    jwk = {"kty": "RSA", "kid": "k1", "alg": "RS256", "use": "sig", "n": b64(pub.n), "e": b64(pub.e)}
    client = sso.HubOidcClient()
    client._discovery = sso.Discovery(ISSUER, f"{ISSUER}/authorize", f"{ISSUER}/token", f"{ISSUER}/jwks")
    client._discovery_at = time.monotonic()
    client._jwks, client._jwks_at = {"k1": jwk}, time.monotonic()
    monkeypatch.setattr(client, "_refresh_jwks", lambda: None)
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "hub_client_id", CLIENT)

    def make(**over):
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT, "sub": "u-123", "email": "a@b.c", "iat": now,
                  "exp": now + 60, "nonce": "n1", **over}
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})
    return client, make


def test_valid_token(oidc):
    client, make = oidc
    assert client.verify_id_token(make(), "n1")["sub"] == "u-123"


@pytest.mark.parametrize("over", [{"aud": "other-app"}, {"iss": "http://evil"}, {"exp": int(time.time()) - 3600},
                                  {"sub": None}, {"nonce": "wrong"}])
def test_rejected_tokens(oidc, over):
    client, make = oidc
    with pytest.raises(sso.SsoError):
        client.verify_id_token(make(**over), "n1")


def test_hs256_rejected(oidc):
    client, _ = oidc
    tok = jwt.encode({"iss": ISSUER, "aud": CLIENT, "sub": "x", "exp": int(time.time()) + 60,
                      "iat": int(time.time())}, "secret", algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(sso.SsoError, match="not allowed"):
        client.verify_id_token(tok, "n1")


def test_pkce_challenge_is_s256():
    # RFC 7636 appendix B test vector
    assert sso._pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == \
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


@pytest.mark.parametrize("base_url,override,expected", [
    ("http://172.28.92.56:3170", None, False),     # HTTP staging: Secure cookies would be dropped
    ("https://aastar.kpndomain.com", None, True),
    ("https://aastar.kpndomain.com", False, False),
    ("http://localhost:5181", True, True),
])
def test_secure_cookies_follow_scheme(base_url, override, expected):
    from app.core.config import get_settings
    s = get_settings().model_copy(update={"app_base_url": base_url, "cookie_secure": override})
    assert s.secure_cookies is expected
    assert s.is_https is base_url.startswith("https://")
