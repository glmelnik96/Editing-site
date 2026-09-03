"""Текущий пользователь: Bearer-токен агента или cookie-сессия браузера."""
from __future__ import annotations

import sqlite3

from fastapi import Depends, Request
from pydantic import BaseModel

from server.app.auth.sessions import resolve_session
from server.app.auth.tokens import resolve_token
from server.app.auth.users import normalize_email
from server.app.config import Settings
from server.app.errors import ApiError
from server.app.security import SESSION_COOKIE, is_bearer
from server.db.core import get_db

CHALLENGE = {"WWW-Authenticate": "Bearer"}


class CurrentUser(BaseModel):
    id: str
    email: str
    name: str
    role: str
    auth: str  # "cookie" | "token"


def _user(row: sqlite3.Row, auth: str, settings: Settings) -> CurrentUser:
    admin_email = normalize_email(settings.admin_email) if settings.admin_email else ""
    role = "admin" if admin_email and row["email"] == admin_email else "user"
    return CurrentUser(id=row["id"], email=row["email"], name=row["name"], role=role, auth=auth)


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> CurrentUser:  # noqa: B008
    if is_bearer(request.headers):
        row = resolve_token(conn, request.headers["authorization"][7:].strip())
        if row is None:
            raise ApiError(401, "invalid_token", "Токен недействителен", headers=CHALLENGE)
        return _user(row, "token", request.app.state.settings)
    row = resolve_session(conn, request.cookies.get(SESSION_COOKIE), request.app.state.settings)
    if row is None:
        raise ApiError(401, "unauthorized", "Требуется вход", headers=CHALLENGE)
    return _user(row, "cookie", request.app.state.settings)


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:  # noqa: B008
    if user.role != "admin":
        raise ApiError(403, "admin_only", "Только для администратора")
    return user


def require_cookie(user: CurrentUser = Depends(current_user)) -> CurrentUser:  # noqa: B008
    """Управление токенами и настройками только из браузера: токен не должен плодить токены."""
    if user.auth != "cookie":
        raise ApiError(403, "cookie_required", "Доступно только из браузера после входа")
    return user


def require_admin_cookie(user: CurrentUser = Depends(require_cookie)) -> CurrentUser:  # noqa: B008
    """Правки whitelist только из браузера: токен агента не должен заводить новых людей."""
    if user.role != "admin":
        raise ApiError(403, "admin_only", "Только для администратора")
    return user
