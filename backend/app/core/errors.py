"""Uniform error shape: {"error": {"code", "message", "request_id"}}."""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import request_id_var

logger = logging.getLogger("app.errors")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details


def _body(code: str, message: str, details: dict | None = None) -> dict:
    err = {"code": code, "message": message, "request_id": request_id_var.get()}
    if details:
        err["details"] = details
    return {"error": err}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=_body(exc.code, exc.message, exc.details))

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException):
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error")
        return JSONResponse(status_code=exc.status_code, content=_body(code, str(exc.detail)),
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = [{"loc": ".".join(str(p) for p in e["loc"][1:]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content=_body("validation_error", "Invalid request.",
                                                           {"fields": fields}))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("unhandled error")
        return JSONResponse(status_code=500, content=_body("internal_error", "Something went wrong."))
