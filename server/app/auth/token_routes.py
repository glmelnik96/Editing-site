"""Токены агента: выпуск (секрет один раз), список, отзыв. Только из браузера."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator

from server.app.auth.deps import CurrentUser, require_cookie
from server.app.auth.tokens import MAX_TOKEN_DAYS, TokenLimitError, create_token, list_tokens, revoke_token
from server.app.errors import ApiError
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int | None = Field(default=None, ge=1, le=MAX_TOKEN_DAYS)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("имя токена пустое")
        return value


class TokenView(BaseModel):
    id: str
    name: str
    created_at: str
    last_used_at: str | None
    expires_at: str | None


class TokenCreated(TokenView):
    secret: str


class TokenList(BaseModel):
    tokens: list[TokenView]


@router.get("", response_model=TokenList)
def list_(
    user: CurrentUser = Depends(require_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> TokenList:
    return TokenList(tokens=[TokenView(**t) for t in list_tokens(conn, user.id)])


@router.post("", status_code=201, response_model=TokenCreated)
def create(
    body: TokenCreate,
    user: CurrentUser = Depends(require_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> TokenCreated:
    try:
        view, secret = create_token(
            conn, user_id=user.id, name=body.name, expires_in_days=body.expires_in_days
        )
    except TokenLimitError as exc:
        raise ApiError(409, "too_many_tokens", str(exc)) from exc
    return TokenCreated(**view, secret=secret)


@router.delete("/{token_id}", status_code=204)
def revoke(
    token_id: str,
    user: CurrentUser = Depends(require_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    if not revoke_token(conn, user_id=user.id, token_id=token_id):
        raise ApiError(404, "not_found", "Токен не найден")
    return Response(status_code=204)
