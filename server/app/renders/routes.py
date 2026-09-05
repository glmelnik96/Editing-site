"""Готовые ролики и ход заданий: /api/v1/renders, /api/v1/jobs.

Всё фильтруется по владельцу: чужой идентификатор даёт 404, а не 403 — существование чужих
объектов наружу не подтверждаем.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from server.app.auth.deps import CurrentUser, current_user
from server.app.errors import ApiError
from server.app.projects.routes import RenderView
from server.app.projects.store import delete_render, get_render
from server.app.util import now_iso
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


def _owned_job(conn: sqlite3.Connection, user: CurrentUser, job_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user.id)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Задание не найдено")
    return row


@router.get("/renders/{render_id}", response_model=RenderView)
def get_(
    render_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> RenderView:
    render = get_render(conn, user.id, render_id)
    if render is None:
        raise ApiError(404, "not_found", "Ролик не найден")
    return RenderView(**render)


@router.delete("/renders/{render_id}", status_code=204)
def delete(
    render_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    if not delete_render(conn, user.id, render_id):
        raise ApiError(404, "not_found", "Ролик не найден")
    return Response(status_code=204)


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
    _owned_job(conn, user, job_id)
    conn.execute(
        "UPDATE jobs SET status = 'canceled', finished_at = ? "
        "WHERE id = ? AND user_id = ? AND status IN ('queued', 'running')",
        (now_iso(), job_id, user.id),
    )
    return Response(status_code=204)
