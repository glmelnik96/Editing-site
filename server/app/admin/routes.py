"""Администратор: whitelist почт и общая статистика. Чужие проекты администратор не видит.

Удаление адреса из whitelist отключает учётную запись (сессии и токены перестают работать),
повторное добавление включает её обратно.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from server.app.admin import cabinet as cabinet_mod
from server.app.admin import store
from server.app.admin.services import RemoteClient, build_client, remote_services
from server.app.auth.deps import CurrentUser, require_admin, require_admin_cookie
from server.app.errors import ApiError
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


class ServiceItem(BaseModel):
    key: str
    title: str
    state: str
    message: str


class PersonItem(BaseModel):
    email: str
    admin: bool
    access: dict[str, bool | None]


class CabinetView(BaseModel):
    services: list[ServiceItem]
    people: list[PersonItem]


class AccessChange(BaseModel):
    email: str
    grant: list[str] = []
    revoke: list[str] = []


class ChangeItem(BaseModel):
    service: str
    action: str
    ok: bool
    error: str | None


class ChangeList(BaseModel):
    results: list[ChangeItem]


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


@router.get("/cabinet", response_model=CabinetView)
def cabinet_view(
    request: Request,
    _: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CabinetView:
    settings = request.app.state.settings
    with build_client(settings) as http:
        clients = [RemoteClient(svc, http) for svc in remote_services(settings)]
        view = cabinet_mod.collect(conn, settings, clients)
    return CabinetView(
        services=[
            ServiceItem(key=s.key, title=s.title, state=s.state, message=s.message) for s in view.services
        ],
        people=[PersonItem(email=p.email, admin=p.admin, access=p.access) for p in view.people],
    )


@router.post("/cabinet/access", response_model=ChangeList)
def cabinet_access(
    request: Request,
    body: AccessChange,
    admin: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ChangeList:
    """Ответ всегда 200: частичный успех — обычный исход, и правда лежит в теле (спека §7)."""
    settings = request.app.state.settings
    with build_client(settings) as http:
        clients = [RemoteClient(svc, http) for svc in remote_services(settings)]
        try:
            results = cabinet_mod.apply(
                conn, settings, clients,
                email=body.email, grant=body.grant, revoke=body.revoke, added_by=admin.email,
            )
        except cabinet_mod.CabinetError as exc:
            raise ApiError(422, exc.code, str(exc)) from exc
    return ChangeList(results=[ChangeItem(**vars(r)) for r in results])


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
