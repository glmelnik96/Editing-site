"""Администратор: whitelist почт и общая статистика. Чужие проекты администратор не видит.

Удаление адреса из whitelist отключает учётную запись (сессии и токены перестают работать),
повторное добавление включает её обратно.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from server.app.admin import store
from server.app.auth.deps import CurrentUser, require_admin, require_admin_cookie
from server.app.health import disk_free_pct_safe
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


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
    return WhitelistList(emails=[WhitelistEntry(**row) for row in store.listing(conn)])


@router.post("/whitelist", status_code=201, response_model=WhitelistEntry)
def whitelist_add(
    body: WhitelistAdd,
    admin: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> WhitelistEntry:
    return WhitelistEntry(**store.add(conn, body.email, added_by=admin.email))


@router.delete("/whitelist/{email}", status_code=204)
def whitelist_remove(
    request: Request,
    email: str,
    _: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    store.remove(conn, request.app.state.settings, email)
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
