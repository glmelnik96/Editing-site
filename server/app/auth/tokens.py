"""Токены агента: секрет показывается один раз, в базе только sha256."""
from __future__ import annotations

import secrets
import sqlite3
from datetime import timedelta

from server.app.util import iso, new_id, now_iso, parse_iso, sha256_hex, utcnow
from server.db.core import transaction

TOKEN_PREFIX = "vt_"
TOUCH_INTERVAL = timedelta(minutes=1)
MAX_TOKEN_DAYS = 3650
MAX_ACTIVE_TOKENS = 20


class TokenLimitError(ValueError):
    """У пользователя уже MAX_ACTIVE_TOKENS живых токенов."""


def hash_token(secret: str) -> str:
    return sha256_hex(secret)


def token_view(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "expires_at": row["expires_at"],
    }


def create_token(
    conn: sqlite3.Connection, *, user_id: str, name: str, expires_in_days: int | None
) -> tuple[dict, str]:
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    tid = new_id("tok")
    now = utcnow()
    if expires_in_days is not None and not (1 <= expires_in_days <= MAX_TOKEN_DAYS):
        raise ValueError(f"expires_in_days должен быть от 1 до {MAX_TOKEN_DAYS}")
    expires_at = iso(now + timedelta(days=expires_in_days)) if expires_in_days is not None else None
    with transaction(conn):
        active = conn.execute(
            "SELECT count(*) FROM api_tokens WHERE user_id = ? AND revoked_at IS NULL", (user_id,)
        ).fetchone()[0]
        if active >= MAX_ACTIVE_TOKENS:
            raise TokenLimitError(f"не больше {MAX_ACTIVE_TOKENS} активных токенов; отзовите ненужные")
        conn.execute(
            "INSERT INTO api_tokens "
            "(id, user_id, name, token_hash, created_at, last_used_at, expires_at, revoked_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, ?, NULL)",
            (tid, user_id, name[:100], hash_token(secret), iso(now), expires_at),
        )
    row = conn.execute("SELECT * FROM api_tokens WHERE id = ?", (tid,)).fetchone()
    return token_view(row), secret


def list_tokens(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM api_tokens WHERE user_id = ? AND revoked_at IS NULL "
        "ORDER BY created_at DESC, rowid DESC",
        (user_id,),
    )
    return [token_view(r) for r in rows]


def revoke_token(conn: sqlite3.Connection, *, user_id: str, token_id: str) -> bool:
    cur = conn.execute(
        "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
        (now_iso(), token_id, user_id),
    )
    return cur.rowcount == 1


def resolve_token(conn: sqlite3.Connection, secret: str | None) -> sqlite3.Row | None:
    """Строка с полями пользователя + token_id, либо None."""
    if not secret or not secret.startswith(TOKEN_PREFIX):
        return None
    row = conn.execute(
        "SELECT t.id AS token_id, t.last_used_at, t.expires_at, t.revoked_at, "
        "u.id, u.email, u.name, u.role, u.disabled "
        "FROM api_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?",
        (hash_token(secret),),
    ).fetchone()
    if row is None or row["revoked_at"] or row["disabled"]:
        return None
    now = utcnow()
    if row["expires_at"] and row["expires_at"] < iso(now):
        return None
    recent = False
    if row["last_used_at"]:
        try:
            recent = now - parse_iso(row["last_used_at"]) <= TOUCH_INTERVAL
        except (TypeError, ValueError):
            recent = False
    if not recent:
        conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (iso(now), row["token_id"]))
    return row
