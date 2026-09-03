"""Администратор: whitelist почт и общая статистика. Чужие проекты администратор не видит.

Удаление адреса из whitelist отключает учётную запись (сессии и токены перестают работать),
повторное добавление включает её обратно.
"""
from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from server.app.auth.deps import CurrentUser, require_admin, require_admin_cookie
from server.app.auth.users import normalize_email
from server.app.errors import ApiError
from server.app.health import disk_free_pct_safe
from server.app.util import now_iso
from server.db.core import get_db, transaction

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class WhitelistAdd(BaseModel):
    email: str


class WhitelistEntry(BaseModel):
    email: str
    added_by: str | None
    added_at: str


class WhitelistList(BaseModel):
    emails: list[WhitelistEntry]


class Stats(BaseModel):
    users: int
    sessions: int
    tokens: int
    disk_free_pct: float


@router.get("/whitelist", response_model=WhitelistList)
def whitelist_list(
    _: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> WhitelistList:
    rows = conn.execute("SELECT email, added_by, added_at FROM whitelist ORDER BY added_at, email")
    return WhitelistList(emails=[WhitelistEntry(**dict(r)) for r in rows])


@router.post("/whitelist", status_code=201, response_model=WhitelistEntry)
def whitelist_add(
    body: WhitelistAdd,
    admin: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> WhitelistEntry:
    email = normalize_email(body.email)
    if not EMAIL_RE.match(email):
        raise ApiError(422, "invalid_email", "Это не похоже на адрес почты")
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO whitelist (email, added_by, added_at) VALUES (?, ?, ?)",
            (email, admin.email, now_iso()),
        )
        conn.execute("UPDATE users SET disabled = 0 WHERE email = ?", (email,))
    row = conn.execute("SELECT email, added_by, added_at FROM whitelist WHERE email = ?", (email,)).fetchone()
    return WhitelistEntry(**dict(row))


@router.delete("/whitelist/{email}", status_code=204)
def whitelist_remove(
    request: Request,
    email: str,
    _: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    normalized = normalize_email(email)
    if normalized == normalize_email(request.app.state.settings.admin_email):
        raise ApiError(409, "cannot_remove_admin", "Администратор из конфигурации всегда в списке")
    with transaction(conn):
        cur = conn.execute("DELETE FROM whitelist WHERE email = ?", (normalized,))
        if cur.rowcount == 0:
            raise ApiError(404, "not_found", "Адреса нет в списке")
        conn.execute("UPDATE users SET disabled = 1 WHERE email = ?", (normalized,))
        conn.execute(
            "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email = ?)", (normalized,)
        )
    return Response(status_code=204)


@router.get("/stats", response_model=Stats)
def stats(
    request: Request,
    _: CurrentUser = Depends(require_admin),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Stats:
    def count(table: str, where: str = "") -> int:
        return conn.execute(f"SELECT count(*) FROM {table} {where}").fetchone()[0]

    return Stats(
        users=count("users"),
        sessions=count("sessions"),
        tokens=count("api_tokens", "WHERE revoked_at IS NULL"),
        disk_free_pct=disk_free_pct_safe(request.app.state.settings.data_dir),
    )
