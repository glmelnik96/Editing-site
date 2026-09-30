"""Готовые ролики и ход заданий: /api/v1/renders, /api/v1/jobs.

Всё общее: ролики и задания видит, отменяет и удаляет любой вошедший — как проекты и записи.
Несуществующий идентификатор даёт 404.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from server.app.audit import audit
from server.app.auth.deps import CurrentUser, current_user
from server.app.errors import ApiError
from server.app.jobs import list_jobs as list_team_jobs
from server.app.projects.routes import RenderView
from server.app.projects.store import delete_render, get_render_any, render_owner
from server.app.util import now_iso, utcnow
from server.db.core import get_db

router = APIRouter(prefix="/api/v1", tags=["renders"])


class JobView(BaseModel):
    id: str
    type: str
    status: str
    progress: float
    error: str | None
    created_at: str
    finished_at: str | None


class JobListItem(BaseModel):
    id: str
    type: str
    status: str
    progress: float
    error: str | None
    created_at: str
    finished_at: str | None
    label: str
    cancelable: bool
    quality: str | None
    target_id: str
    # Чьё задание: у сборки — автор проекта, у анализа, прокси и расшифровки — автор записи.
    owner_email: str
    owner_name: str


class JobList(BaseModel):
    jobs: list[JobListItem]


def _job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Задание не найдено")
    return row


@router.get("/renders/{render_id}", response_model=RenderView)
def get_(
    render_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> RenderView:
    render = get_render_any(conn, render_id)
    if render is None:
        raise ApiError(404, "not_found", "Ролик не найден")
    return RenderView(**render)


@router.delete("/renders/{render_id}", status_code=204)
def delete(
    render_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    owner = render_owner(conn, render_id)
    if owner is None or not delete_render(conn, owner, render_id):
        raise ApiError(404, "not_found", "Ролик не найден")
    audit(conn, user, "удалил ролик", render_id, owner)
    return Response(status_code=204)


@router.get("/jobs", response_model=JobList)
def list_jobs(
    mine: bool = False,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> JobList:
    """Задания всей команды; ?mine=1 — только свои (конвертер, скрипты агента)."""
    rows = list_team_jobs(conn, now=utcnow(), owner=user.id if mine else None)
    return JobList(jobs=[JobListItem(**row) for row in rows])


@router.get("/jobs/{job_id}", response_model=JobView)
def job(
    job_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> JobView:
    row = _job(conn, job_id)
    return JobView(
        id=row["id"], type=row["type"], status=row["status"], progress=row["progress"],
        error=row["error"], created_at=row["created_at"], finished_at=row["finished_at"],
    )


@router.post("/jobs/{job_id}/cancel", status_code=204)
def cancel(
    job_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    """Отменяет задание в очереди или выполняющееся: воркер увидит это при следующем пульсе."""
    row = _job(conn, job_id)
    # Анализ отменять нечего: без него у записи нет ни длительности, ни карт пауз, ни полоски
    # кадров, и в проект она не встанет. Кому нужно прервать — удаляет запись, а это отменяет
    # её задания правильно и не оставляет её висеть в analyzing.
    if row["type"] == "analyze":
        raise ApiError(
            422, "cannot_cancel", "Анализ записи отменить нельзя: удалите саму запись"
        )
    cur = conn.execute(
        "UPDATE jobs SET status = 'canceled', finished_at = ? "
        "WHERE id = ? AND status IN ('queued', 'running')",
        (now_iso(), job_id),
    )
    if cur.rowcount:
        audit(conn, user, "отменил задание", f"{job_id} ({row['type']})", row["user_id"])
    return Response(status_code=204)
