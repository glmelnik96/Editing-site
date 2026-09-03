"""Вход через Yandex OAuth, выход, текущий пользователь."""
from __future__ import annotations

import secrets
import sqlite3

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from server.app.auth.deps import CurrentUser, current_user
from server.app.auth.oauth import build_authorize_url, exchange_code, fetch_userinfo
from server.app.auth.sessions import create_session, delete_session
from server.app.auth.users import is_whitelisted, upsert_user
from server.app.errors import ApiError
from server.app.security import SESSION_COOKIE, client_ip
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
me_router = APIRouter(prefix="/api/v1", tags=["me"])

STATE_COOKIE = "oauth_state"
STATE_COOKIE_PATH = "/api/v1/auth"


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    settings = request.app.state.settings
    if not request.app.state.login_limiter.allow(client_ip(request)):
        raise ApiError(429, "rate_limited", "Слишком много попыток входа, подождите минуту")
    if not settings.yandex_client_id:
        raise ApiError(503, "oauth_not_configured", "Yandex OAuth не настроен")
    state = secrets.token_urlsafe(24)
    url = build_authorize_url(
        client_id=settings.yandex_client_id, redirect_uri=settings.yandex_redirect_uri, state=state
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=STATE_COOKIE_PATH,
    )
    return resp


@router.get("/callback")
async def callback(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    settings = request.app.state.settings
    if error:
        raise ApiError(400, "oauth_error", f"Яндекс вернул ошибку: {error}")
    if not code or not state or state != request.cookies.get(STATE_COOKIE):
        raise ApiError(400, "bad_state", "Сессия входа не совпадает, начните вход заново")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            access_token = await exchange_code(
                client,
                code=code,
                client_id=settings.yandex_client_id,
                client_secret=settings.yandex_client_secret,
                redirect_uri=settings.yandex_redirect_uri,
            )
            info = await fetch_userinfo(client, access_token)
    except httpx.HTTPError as exc:
        raise ApiError(502, "oauth_upstream", f"Яндекс недоступен: {exc.__class__.__name__}") from exc
    email = str(info.get("default_email") or "").strip().lower()
    if not is_whitelisted(conn, email, settings.admin_email):
        raise ApiError(403, "not_allowed", "Этот адрес не в списке разрешённых")
    name = str(info.get("real_name") or info.get("display_name") or email)
    user = upsert_user(conn, email=email, name=name, admin_email=settings.admin_email)
    if user["disabled"]:
        raise ApiError(403, "account_disabled", "Учётная запись отключена")
    delete_session(conn, request.cookies.get(SESSION_COOKIE))
    sid = create_session(
        conn, user_id=user["id"], user_agent=request.headers.get("user-agent", ""), settings=settings
    )
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE,
        sid,
        max_age=settings.session_absolute_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    resp.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    return resp


@router.post("/logout")
def logout(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> JSONResponse:  # noqa: B008
    delete_session(conn, request.cookies.get(SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@me_router.get("/me")
def me(user: CurrentUser = Depends(current_user)) -> dict:  # noqa: B008
    return user.model_dump()
