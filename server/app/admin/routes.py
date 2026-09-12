"""Администратор: whitelist почт, общая статистика и обзор чужой работы.

Удаление адреса из whitelist отключает учётную запись (сессии и токены перестают работать),
повторное добавление включает её обратно.
"""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from server.app.admin import cabinet as cabinet_mod
from server.app.admin import store
from server.app.admin.services import RemoteClient, build_client, remote_services
from server.app.auth.deps import CurrentUser, require_admin, require_admin_cookie
from server.app.errors import ApiError
from server.app.health import disk_free_pct_safe
from server.app.uploads.store import used_bytes
from server.db.core import get_db
from server.media.timeline import clips_duration

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


class PersonUse(BaseModel):
    email: str
    name: str
    bytes: int
    records: int


class UsageList(BaseModel):
    people: list[PersonUse]


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


class OwnedProject(BaseModel):
    id: str
    name: str
    owner_email: str
    owner_name: str
    clips_count: int
    duration: float
    updated_at: str


class OwnedProjectList(BaseModel):
    projects: list[OwnedProject]


class OwnedAsset(BaseModel):
    id: str
    original_name: str
    owner_email: str
    owner_name: str
    kind: str
    status: str
    size: int
    duration: float | None
    created_at: str


class OwnedAssetList(BaseModel):
    assets: list[OwnedAsset]


@router.get("/projects", response_model=OwnedProjectList)
def all_projects(
    _: CurrentUser = Depends(require_admin),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> OwnedProjectList:
    """Проекты всех: диск общий, и следить за тем, чем он занят, кроме админа некому."""
    rows = conn.execute(
        "SELECT p.id, p.name, p.doc, p.updated_at, u.email, u.name AS owner_name "
        "FROM projects AS p JOIN users AS u ON u.id = p.user_id "
        "ORDER BY p.updated_at DESC, p.id"
    )
    out = []
    for row in rows:
        clips = (json.loads(row["doc"]).get("clips") or [])
        out.append(OwnedProject(
            id=row["id"], name=row["name"], owner_email=row["email"], owner_name=row["owner_name"],
            clips_count=len(clips), duration=clips_duration(clips), updated_at=row["updated_at"],
        ))
    return OwnedProjectList(projects=out)


@router.get("/assets", response_model=OwnedAssetList)
def all_assets(
    _: CurrentUser = Depends(require_admin),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> OwnedAssetList:
    """Записи всех. Самые тяжёлые сверху: место кончается из-за них, а не из-за числа файлов."""
    rows = conn.execute(
        "SELECT a.id, a.original_name, a.kind, a.status, a.size, a.duration, a.created_at, "
        "u.email, u.name AS owner_name "
        "FROM assets AS a JOIN users AS u ON u.id = a.user_id "
        "ORDER BY a.size DESC, a.id"
    )
    return OwnedAssetList(assets=[
        OwnedAsset(
            id=r["id"], original_name=r["original_name"], owner_email=r["email"],
            owner_name=r["owner_name"], kind=r["kind"], status=r["status"], size=r["size"],
            duration=r["duration"], created_at=r["created_at"],
        )
        for r in rows
    ])


@router.get("/usage", response_model=UsageList)
def usage(
    request: Request,
    _: CurrentUser = Depends(require_admin),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> UsageList:
    """Кто сколько занимает на диске — по файлам в папке человека, так же, как считает его лимит.
    Тяжёлые сверху: место кончается из-за них; у кого на диске ничего нет, в списке не стоит."""
    settings = request.app.state.settings
    records = {r[0]: r[1] for r in conn.execute("SELECT user_id, count(*) FROM assets GROUP BY user_id")}
    people = []
    for row in conn.execute("SELECT id, email, name FROM users").fetchall():
        taken = used_bytes(conn, settings, row["id"])
        if taken:
            people.append(PersonUse(
                email=row["email"], name=row["name"], bytes=taken, records=records.get(row["id"], 0),
            ))
    people.sort(key=lambda p: (-p.bytes, p.email))
    return UsageList(people=people)


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
