"""Защита cookie-сессий от запросов с чужих сайтов и IP клиента.

IP клиента за Caddy восстанавливает сам uvicorn (флаги --proxy-headers --forwarded-allow-ips=127.0.0.1
в systemd-юните): он читает X-Forwarded-For справа налево и отбрасывает адреса, подставленные клиентом.
Поэтому здесь X-Forwarded-For не разбирается вовсе. Без флагов все клиенты выглядят как 127.0.0.1,
и лимитер входа становится общим на всех, что безопаснее подделки адресов.
"""
from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint

from server.app.errors import error_body

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SESSION_COOKIE = "vsid"  # имя cookie сессии; auth.sessions импортирует его отсюда
SAME_SITE_VALUES = ("same-origin", "none")


def is_cross_site(headers: Mapping[str, str], allowed_origin: str) -> bool:
    """True, если запрос пришёл не с нашего origin.

    Без обоих заголовков считаем чужим: браузеры всегда шлют Origin на изменяющих запросах,
    а не-браузерные клиенты ходят с токеном и до этой проверки не доходят.
    """
    origin = headers.get("origin")
    if origin is not None:
        return origin.rstrip("/").lower() != allowed_origin.rstrip("/").lower()
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site.lower() not in SAME_SITE_VALUES
    return True


def is_bearer(headers: Mapping[str, str]) -> bool:
    return headers.get("authorization", "").lower().startswith("bearer ")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def install_origin_check(app: FastAPI) -> None:
    @app.middleware("http")
    async def _origin_check(request: Request, call_next: RequestResponseEndpoint) -> Response:
        allowed = request.app.state.settings.allowed_origin
        if (
            request.method in UNSAFE_METHODS
            and SESSION_COOKIE in request.cookies
            and not is_bearer(request.headers)
            and is_cross_site(request.headers, allowed)
        ):
            return JSONResponse(
                status_code=403,
                content=error_body("cross_site", "Запрос с чужого сайта отклонён", {"allowed_origin": allowed}),
            )
        return await call_next(request)
