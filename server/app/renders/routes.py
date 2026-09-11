"""Готовые ролики и ход заданий: /api/v1/renders, /api/v1/jobs.

Всё фильтруется по владельцу: чужой идентификатор даёт 404, а не 403 — существование чужих
объектов наружу не подтверждаем. Исключение — админ: он видит задания и ролики всей команды,
как видит её проекты и записи.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from server.app.auth.deps import CurrentUser, current_user
from server.app.errors import ApiError
from server.app.jobs import list_jobs_for_user
from server.app.projects.routes import RenderView
from server.app.projects.store import delete_render, get_render, get_render_any, render_owner
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
    # Чьё задание. Обычному человеку отдаём только его собственные, админу — всей команды.
    owner_email: str
    owner_name: str


class JobList(BaseModel):
    jobs: list[JobListItem]


def _owned_job(conn: sqlite3.Connection, user: CurrentUser, job_id: str) -> sqlite3.Row:
    # Админ смотрит и чужие задания: он мог сам поставить сборку в чужом проекте — она числится
    # за владельцем, потому что готовый ролик ложится в его каталог.
    row = (
        conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if user.role == "admin"
        else conn.execute(
            "SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user.id)
        ).fetchone()
    )
    if row is None:
        raise ApiError(404, "not_found", "Задание не найдено")
    return row


@router.get("/renders/{render_id}", response_model=RenderView)
def get_(
    render_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> RenderView:
    # Ролик чужого проекта админ тоже видит: он мог сам его собрать.
    render = get_render(conn, user.id, render_id) if user.role != "admin" else get_render_any(conn, render_id)
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
    if owner is None or (owner != user.id and user.role != "admin"):
        raise ApiError(404, "not_found", "Ролик не найден")
    if not delete_render(conn, owner, render_id):
        raise ApiError(404, "not_found", "Ролик не найден")
    return Response(status_code=204)


@router.get("/jobs", response_model=JobList)
def list_jobs(
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> JobList:
    rows = list_jobs_for_user(conn, user.id, now=utcnow(), everyone=user.role == "admin")
    return JobList(jobs=[JobListItem(**row) for row in rows])


@router.get("/jobs/{job_id}", response_model=JobView)
def job(
    job_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> JobView:
    row = _owned_job(conn, user, job_id)
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
    row = _owned_job(conn, user, job_id)
    # Анализ отменять нечего: без него у записи нет ни длительности, ни карт пауз, ни полоски
    # кадров, и в проект она не встанет. Кому нужно прервать — удаляет запись, а это отменяет
    # её задания правильно и не оставляет её висеть в analyzing.
    if row["type"] == "analyze":
        raise ApiError(
            422, "cannot_cancel", "Анализ записи отменить нельзя: удалите саму запись"
        )
    conn.execute(
        "UPDATE jobs SET status = 'canceled', finished_at = ? "
        "WHERE id = ? AND (user_id = ? OR ?) AND status IN ('queued', 'running')",
        (now_iso(), job_id, user.id, user.role == "admin"),
    )
    return Response(status_code=204)
