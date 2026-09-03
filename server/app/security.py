"""Защита cookie-сессий от запросов с чужих сайтов и определение IP клиента."""
from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.app.errors import error_body

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SESSION_COOKIE = "vsid"


def is_cross_site(headers: Mapping[str, str], allowed_origin: str) -> bool:
    origin = headers.get("origin")
    if origin is not None:
        return origin.rstrip("/").lower() != allowed_origin.rstrip("/").lower()
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site.lower() == "cross-site"
    return False


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def install_origin_check(app: FastAPI) -> None:
    @app.middleware("http")
    async def _origin_check(request: Request, call_next):
        if (
            request.method in UNSAFE_METHODS
            and SESSION_COOKIE in request.cookies
            and not request.headers.get("authorization")
            and is_cross_site(request.headers, request.app.state.settings.allowed_origin)
        ):
            return JSONResponse(status_code=403, content=error_body("cross_site", "Запрос с чужого сайта отклонён"))
        return await call_next(request)
