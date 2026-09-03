"""Серверные сессии. В cookie только случайный идентификатор, в базе его sha256:
утечка базы не отдаёт живые сессии.

Имя cookie (SESSION_COOKIE) живёт в server/app/security.py; здесь только работа с идентификатором.
"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import timedelta

from server.app.config import Settings
from server.app.util import iso, parse_iso, sha256_hex, utcnow

TOUCH_INTERVAL = timedelta(minutes=1)

_USER_COLUMNS = "u.id, u.email, u.name, u.role, u.disabled"


def create_session(conn: sqlite3.Connection, *, user_id: str, user_agent: str, settings: Settings) -> str:
    """Создаёт сессию и возвращает её секрет для cookie; в базе хранится только хеш."""
    now = utcnow()
    sid = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, last_seen_at, absolute_expires_at, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            sha256_hex(sid),
            user_id,
            iso(now),
            iso(now),
            iso(now + timedelta(days=settings.session_absolute_days)),
            user_agent[:200],
        ),
    )
    # Оставляем max_sessions_per_user самых новых (rowid растёт с каждой вставкой, время может совпасть).
    conn.execute(
        "DELETE FROM sessions WHERE user_id = ? AND id NOT IN "
        "(SELECT id FROM sessions WHERE user_id = ? ORDER BY rowid DESC LIMIT ?)",
        (user_id, user_id, settings.max_sessions_per_user),
    )
    return sid


def resolve_session(conn: sqlite3.Connection, sid: str | None, settings: Settings) -> sqlite3.Row | None:
    """Строка с полями пользователя + session_id (хеш), либо None. Просроченные сессии удаляются."""
    if not sid:
        return None
    session_id = sha256_hex(sid)
    row = conn.execute(
        f"SELECT s.id AS session_id, s.last_seen_at, s.absolute_expires_at, {_USER_COLUMNS} "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    now = utcnow()
    idle_for = now - parse_iso(row["last_seen_at"])
    expired = iso(now) > row["absolute_expires_at"] or idle_for > timedelta(days=settings.session_idle_days)
    if expired or row["disabled"]:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return None
    if idle_for > TOUCH_INTERVAL:
        conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (iso(now), session_id))
    return row


def delete_session(conn: sqlite3.Connection, sid: str | None) -> None:
    if sid:
        conn.execute("DELETE FROM sessions WHERE id = ?", (sha256_hex(sid),))
