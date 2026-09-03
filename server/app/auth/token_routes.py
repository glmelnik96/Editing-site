"""Токены агента: выпуск (секрет один раз), список, отзыв. Только из браузера."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from server.app.auth.deps import CurrentUser, require_cookie
from server.app.auth.tokens import MAX_TOKEN_DAYS, create_token, list_tokens, revoke_token
from server.app.errors import ApiError
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int | None = Field(default=None, ge=1, le=MAX_TOKEN_DAYS)


@router.get("")
def list_(
    user: CurrentUser = Depends(require_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> dict:
    return {"tokens": list_tokens(conn, user.id)}


@router.post("", status_code=201)
def create(
    body: TokenCreate,
    user: CurrentUser = Depends(require_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> dict:
    view, secret = create_token(conn, user_id=user.id, name=body.name, expires_in_days=body.expires_in_days)
    return {**view, "secret": secret}


@router.delete("/{token_id}", status_code=204)
def revoke(
    token_id: str,
    user: CurrentUser = Depends(require_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    if not revoke_token(conn, user_id=user.id, token_id=token_id):
        raise ApiError(404, "not_found", "Токен не найден")
    return Response(status_code=204)
