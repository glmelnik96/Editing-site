"""Вход через Yandex OAuth, выход, текущий пользователь."""
from __future__ import annotations

import secrets
import sqlite3

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from server.app.auth.deps import CurrentUser, current_user
from server.app.auth.oauth import build_authorize_url, exchange_code, fetch_userinfo
from server.app.auth.sessions import create_session, delete_session
from server.app.auth.users import is_whitelisted, upsert_user
from server.app.errors import ApiError
from server.app.security import SESSION_COOKIE, client_ip, is_bearer
from server.app.uploads.store import used_bytes
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
me_router = APIRouter(prefix="/api/v1", tags=["me"])

STATE_COOKIE = "oauth_state"
STATE_COOKIE_PATH = "/api/v1/auth"


def _state_cookie_kwargs(settings) -> dict:
    return {"path": STATE_COOKIE_PATH, "httponly": True, "secure": settings.cookie_secure, "samesite": "lax"}


def _callback_failure(settings, code: str) -> RedirectResponse:
    """Callback открывает браузер как обычную навигацию: ошибку показывает страница входа по ?error=<code>."""
    resp = RedirectResponse(f"/?error={code}", status_code=302)
    resp.delete_cookie(STATE_COOKIE, **_state_cookie_kwargs(settings))
    return resp


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    settings = request.app.state.settings
    if not settings.yandex_client_id or not settings.yandex_client_secret:
        raise ApiError(503, "oauth_not_configured", "Yandex OAuth не настроен")
    if not request.app.state.login_limiter.allow(client_ip(request)):
        raise ApiError(429, "rate_limited", "Слишком много попыток входа, подождите минуту")
    state = secrets.token_urlsafe(24)
    url = build_authorize_url(
        client_id=settings.yandex_client_id, redirect_uri=settings.yandex_redirect_uri, state=state
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(STATE_COOKIE, state, max_age=600, **_state_cookie_kwargs(settings))
    return resp


@router.get("/callback")
async def callback(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    settings = request.app.state.settings
    if not request.app.state.login_limiter.allow(client_ip(request)):
        raise ApiError(429, "rate_limited", "Слишком много попыток входа, подождите минуту")
    if error:
        return _callback_failure(settings, "oauth_error")
    if not code or not state or state != request.cookies.get(STATE_COOKIE):
        return _callback_failure(settings, "bad_state")
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
    except (httpx.HTTPError, ValueError, KeyError):
        return _callback_failure(settings, "oauth_upstream")
    email = str(info.get("default_email") or "").strip().lower()
    if not is_whitelisted(conn, email, settings.admin_email):
        return _callback_failure(settings, "not_allowed")
    name = str(info.get("real_name") or info.get("display_name") or email)
    yandex_id = str(info.get("id") or "") or None
    user = upsert_user(conn, email=email, name=name, admin_email=settings.admin_email, yandex_id=yandex_id)
    if user["disabled"]:
        return _callback_failure(settings, "account_disabled")
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
    resp.delete_cookie(STATE_COOKIE, **_state_cookie_kwargs(settings))
    return resp


@router.post("/logout")
def logout(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> JSONResponse:  # noqa: B008
    if is_bearer(request.headers):
        raise ApiError(
            400,
            "cookie_required",
            "Выход касается только cookie-сессии, токен отзывается через /api/v1/tokens",
        )
    delete_session(conn, request.cookies.get(SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="lax",
    )
    return resp


class Quota(BaseModel):
    used_bytes: int
    limit_bytes: int


class MeView(CurrentUser):
    quota: Quota


@me_router.get("/me", response_model=MeView)
def me(
    request: Request,
    response: Response,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> MeView:
    response.headers["Cache-Control"] = "no-store"
    limit = request.app.state.settings.user_quota_bytes
    return MeView(**user.model_dump(), quota=Quota(used_bytes=used_bytes(conn, user.id), limit_bytes=limit))
