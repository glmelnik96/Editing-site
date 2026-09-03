"""Текущий пользователь: Bearer-токен агента или cookie-сессия браузера."""
from __future__ import annotations

import sqlite3

from fastapi import Depends, Request
from pydantic import BaseModel

from server.app.auth.sessions import resolve_session
from server.app.auth.tokens import resolve_token
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


def _user(row: sqlite3.Row, auth: str) -> CurrentUser:
    return CurrentUser(id=row["id"], email=row["email"], name=row["name"], role=row["role"], auth=auth)


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> CurrentUser:  # noqa: B008
    if is_bearer(request.headers):
        row = resolve_token(conn, request.headers["authorization"][7:].strip())
        if row is None:
            raise ApiError(401, "invalid_token", "Токен недействителен", headers=CHALLENGE)
        return _user(row, "token")
    row = resolve_session(conn, request.cookies.get(SESSION_COOKIE), request.app.state.settings)
    if row is None:
        raise ApiError(401, "unauthorized", "Требуется вход", headers=CHALLENGE)
    return _user(row, "cookie")


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:  # noqa: B008
    if user.role != "admin":
        raise ApiError(403, "admin_only", "Только для администратора")
    return user


def require_cookie(user: CurrentUser = Depends(current_user)) -> CurrentUser:  # noqa: B008
    """Управление токенами и настройками только из браузера: токен не должен плодить токены."""
    if user.auth != "cookie":
        raise ApiError(403, "cookie_required", "Доступно только из браузера после входа")
    return user
