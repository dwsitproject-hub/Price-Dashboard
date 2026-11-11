"""/api/auth — local login, session, password change, DWS Hub SSO."""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app.api.deps import Principal, check_origin, client_ip, current_principal, request_meta
from app.core.config import APP_VERSION, get_settings
from app.core.db import get_db
from app.core.errors import ApiError
from app.core.ratelimit import limiter
from app.schemas import ChangePasswordIn, LoginIn
from app.services import audit, auth, sessions, sso
from app.services import settings as runtime_settings
from app.services.rbac import effective_modules

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("app.auth")


def _set_session_cookies(response: Response, issued: sessions.IssuedSession) -> None:
    s = get_settings()
    secure = s.secure_cookies
    max_age = int((issued.session.expires_at - issued.session.created_at).total_seconds())
    response.set_cookie(sessions.SESSION_COOKIE, issued.token, max_age=max_age, httponly=True, secure=secure,
                        samesite="lax", path="/")
    response.set_cookie(sessions.CSRF_COOKIE, issued.csrf, max_age=max_age, httponly=False, secure=secure,
                        samesite="strict", path="/")


def _clear_cookies(response: Response) -> None:
    response.delete_cookie(sessions.SESSION_COOKIE, path="/")
    response.delete_cookie(sessions.CSRF_COOKIE, path="/")


def me_payload(db: DbSession, p_user, auth_method: str) -> dict:
    return {
        "id": str(p_user.id), "email": p_user.email, "display_name": p_user.display_name,
        "role": p_user.role.code, "role_name": p_user.role.name,
        "permissions": list(p_user.role.permissions or []),
        "modules": effective_modules(db, p_user), "hub_sub": p_user.hub_sub,
        "auth_provider": p_user.auth_provider, "auth_method": auth_method,
    }


@router.get("/config")
def auth_config(db: DbSession = Depends(get_db)) -> dict:
    s = get_settings()
    return {"local_enabled": s.auth_local_enabled, "sso_enabled": s.sso_configured, "sso_label": s.sso_label,
            "app_version": APP_VERSION, "release": s.app_release, "environment": s.app_env,
            "data_mode": runtime_settings.get(db, "data.mode")}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: DbSession = Depends(get_db)) -> dict:
    check_origin(request)
    ip = client_ip(request) or "unknown"
    if not limiter.allow(f"login:{ip}", get_settings().login_rate_per_minute, 60):
        raise ApiError(429, "rate_limited", "Too many sign-in attempts. Wait a minute and try again.")
    user, issued = auth.login(db, body.email, body.password, request_meta(request))
    _set_session_cookies(response, issued)
    return me_payload(db, user, "local")


@router.post("/logout")
def logout(request: Request, response: Response, p: Principal = Depends(current_principal),
           db: DbSession = Depends(get_db)) -> dict:
    sess = db.merge(p.session)
    sessions.revoke(db, sess, "logout")
    audit.record(db, "LOGOUT", actor_id=p.user.id, actor_email=p.user.email, meta=request_meta(request))
    db.commit()
    _clear_cookies(response)
    return {"ok": True}


@router.get("/me")
def me(p: Principal = Depends(current_principal), db: DbSession = Depends(get_db)) -> dict:
    return me_payload(db, p.user, p.session.auth_method)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, response: Response,
                    p: Principal = Depends(current_principal), db: DbSession = Depends(get_db)) -> dict:
    user = db.merge(p.user)
    meta = request_meta(request)
    auth.change_password(db, user, body.current_password, body.new_password, None, meta)
    issued = sessions.create(db, user, p.session.auth_method, meta)   # rotate: all old sessions revoked
    db.commit()
    _set_session_cookies(response, issued)
    return me_payload(db, user, p.session.auth_method)


# ── DWS Hub SSO ─────────────────────────────────────────────────────────────

def _login_error(code: str) -> RedirectResponse:
    return RedirectResponse(f"/login?error={quote(code)}", status_code=302)


@router.get("/sso/login")
def sso_login(request: Request, next: str | None = None, db: DbSession = Depends(get_db)):
    """Starts OIDC. Also the Hub launch Target URL (PRD SSO-06)."""
    try:
        return RedirectResponse(sso.begin(db, next, request_meta(request)), status_code=302)
    except sso.SsoError as exc:
        logger.warning("sso begin failed: %s", exc)
        return _login_error(exc.code)


@router.get("/sso/callback")
def sso_callback(request: Request, code: str | None = None, state: str | None = None,
                 error: str | None = None, code_verifier: str | None = None, db: DbSession = Depends(get_db)):
    meta = request_meta(request)
    if code_verifier is not None:
        # Hub tile launch: the Hub minted its own state and PKCE pair and appends the verifier to this URL
        # (a "transitional helper" in the Hub). We don't redeem that code: a verifier in a URL is not PKCE and
        # an unsolicited code invites login CSRF. Start our own flow instead; the Hub recognises its session
        # cookie at /authorize, so the user goes straight through.
        logger.info("sso: Hub-initiated launch, restarting as app-initiated sign-in")
        return RedirectResponse("/api/auth/sso/login", status_code=302)
    if error:
        audit.record(db, "SSO_REJECTED", outcome="failure", meta=meta, details={"idp_error": error[:64]})
        db.commit()
        return _login_error("sso_denied")
    try:
        user, issued, next_path = sso.complete(db, code or "", state or "", meta)
    except sso.SsoError as exc:
        db.rollback()
        logger.warning("sso callback failed: %s (%s)", exc.code, exc)
        if exc.code in ("sso_token_failed", "sso_token_invalid", "sso_state_invalid"):
            audit.record(db, "SSO_REJECTED", outcome="failure", meta=meta, details={"reason": exc.code})
            db.commit()
        return _login_error(exc.code)
    response = RedirectResponse(next_path, status_code=302)
    _set_session_cookies(response, issued)
    return response
