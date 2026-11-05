"""FastAPI application factory."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import admin, auth, health, market
from app.core.config import APP_VERSION, get_settings
from app.core.crypto import random_token
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, log_event, request_id_var, user_id_var

logger = logging.getLogger("app.http")
MAX_BODY = 1_000_000


def _hub_origin() -> str:
    u = urlparse(get_settings().hub_issuer)
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else ""


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id")
        if not rid or len(rid) > 64 or not rid.replace("-", "").isalnum():
            rid = random_token(12)
        request_id_var.set(rid)
        user_id_var.set(None)
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_BODY:
            return JSONResponse(status_code=413, content={"error": {"code": "payload_too_large",
                                                                     "message": "Request body too large.",
                                                                     "request_id": rid}})
        start = time.perf_counter()
        response = await call_next(request)
        ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = rid
        self._security_headers(response, request)
        log_event(logger, logging.INFO, "request", method=request.method, path=request.url.path,
                  status=response.status_code, ms=ms)
        return response

    @staticmethod
    def _security_headers(response, request: Request) -> None:
        s = get_settings()
        hub = _hub_origin()
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        if request.url.path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
            h.setdefault("Content-Security-Policy",
                         f"default-src 'none'; frame-ancestors 'none'; form-action 'self' {hub}".strip())
        if s.is_https:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)
    docs = "/api/docs" if s.app_env in ("local", "dev", "sit") else None
    app = FastAPI(title="AASTAR Price Dashboard API", version=APP_VERSION, docs_url=docs, redoc_url=None,
                  openapi_url="/api/openapi.json" if docs else None)
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    for r in (health.router, auth.router, market.router, admin.router):
        app.include_router(r, prefix="/api")
    logger.info("AASTAR API %s starting (env=%s, sso=%s, lseg=%s)", APP_VERSION, s.app_env, s.sso_configured,
                s.lseg_configured)
    return app


app = create_app()
