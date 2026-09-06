"""Наш белый список: одни и те же функции для браузерной админки и для кабинета.

HTTP-запрос к самому себе означал бы второй способ делать то же самое и второе место,
где можно ошибиться (спека §3).
"""
from __future__ import annotations

import re
import sqlite3

from server.app.auth.users import normalize_email
from server.app.config import Settings
from server.app.errors import ApiError
from server.app.util import now_iso
from server.db.core import transaction

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(email: str) -> str:
    """Нормализованный адрес или ApiError 422."""
    normalized = normalize_email(email)
    if not EMAIL_RE.match(normalized):
        raise ApiError(422, "invalid_email", "Это не похоже на адрес почты")
    return normalized


def listing(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT email, added_by, added_at FROM whitelist ORDER BY added_at, email")
    return [dict(r) for r in rows]


def add(conn: sqlite3.Connection, email: str, *, added_by: str) -> dict:
    """Добавляет адрес и включает отключённую учётную запись обратно."""
    normalized = valid_email(email)
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO whitelist (email, added_by, added_at) VALUES (?, ?, ?)",
            (normalized, added_by, now_iso()),
        )
        conn.execute("UPDATE users SET disabled = 0 WHERE email = ?", (normalized,))
    row = conn.execute(
        "SELECT email, added_by, added_at FROM whitelist WHERE email = ?", (normalized,)
    ).fetchone()
    return dict(row)


def remove(conn: sqlite3.Connection, settings: Settings, email: str) -> None:
    """Убирает адрес, отключает учётную запись и гасит её сессии: человека выкидывает сразу."""
    normalized = normalize_email(email)
    if normalized == normalize_email(settings.admin_email):
        raise ApiError(409, "cannot_remove_admin", "Администратор из конфигурации всегда в списке")
    with transaction(conn):
        cur = conn.execute("DELETE FROM whitelist WHERE email = ?", (normalized,))
        if cur.rowcount == 0:
            raise ApiError(404, "not_found", "Адреса нет в списке")
        conn.execute("UPDATE users SET disabled = 1 WHERE email = ?", (normalized,))
        conn.execute(
            "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email = ?)", (normalized,)
        )
