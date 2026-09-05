"""Проекты: /api/v1/projects. Документ приходит и уходит целиком, версия защищает от гонки правок."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from server.app.auth.deps import CurrentUser, current_user
from server.app.errors import ApiError
from server.app.projects.doc import ProjectInvalid
from server.app.projects.store import (
    ProjectConflict,
    ProjectLimit,
    create_project,
    delete_project,
    finish_project,
    get_project,
    list_projects,
    save_project,
)
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    doc: dict | None = None


class ProjectSave(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    doc: dict | None = None


class ProjectView(BaseModel):
    id: str
    name: str
    version: int
    status: str
    created_at: str
    updated_at: str
    finished_at: str | None
    doc: dict[str, Any]


class ProjectCard(BaseModel):
    id: str
    name: str
    version: int
    status: str
    created_at: str
    updated_at: str
    finished_at: str | None
    clips_count: int
    duration: float


class ProjectList(BaseModel):
    projects: list[ProjectCard]


def invalid(exc: ProjectInvalid) -> ApiError:
    return ApiError(422, "invalid_project", "Документ проекта не прошёл проверку", {"errors": exc.errors})


def conflict(exc: ProjectConflict) -> ApiError:
    return ApiError(
        409, "version_conflict", "Проект изменился, перечитайте его", {"project": exc.project}
    )


def _owned(conn: sqlite3.Connection, user: CurrentUser, project_id: str) -> dict:
    project = get_project(conn, user.id, project_id)
    if project is None:
        raise ApiError(404, "not_found", "Проект не найден")
    return project


@router.get("", response_model=ProjectList)
def list_(
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectList:
    return ProjectList(projects=[ProjectCard(**p) for p in list_projects(conn, user.id)])


@router.post("", status_code=201, response_model=ProjectView)
def create(
    body: ProjectCreate,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    try:
        project = create_project(
            conn, request.app.state.settings, user.id, name=body.name, raw_doc=body.doc
        )
    except ProjectInvalid as exc:
        raise invalid(exc) from exc
    except ProjectLimit as exc:
        raise ApiError(409, "too_many_projects", str(exc)) from exc
    return ProjectView(**project)


@router.get("/{project_id}", response_model=ProjectView)
def get_(
    project_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    return ProjectView(**_owned(conn, user, project_id))


@router.put("/{project_id}", response_model=ProjectView)
def save(
    project_id: str,
    body: ProjectSave,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    _owned(conn, user, project_id)
    try:
        project = save_project(
            conn, request.app.state.settings, user.id, project_id,
            name=body.name, raw_doc=body.doc, version=body.version,
        )
    except ProjectInvalid as exc:
        raise invalid(exc) from exc
    except ProjectConflict as exc:
        raise conflict(exc) from exc
    except KeyError as exc:
        # Проект удалили между проверкой владения и записью: для клиента это «не найден».
        raise ApiError(404, "not_found", "Проект не найден") from exc
    return ProjectView(**project)


@router.delete("/{project_id}", status_code=204)
def delete(
    project_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    if not delete_project(conn, user.id, project_id):
        raise ApiError(404, "not_found", "Проект не найден")
    return Response(status_code=204)


@router.post("/{project_id}/finish", response_model=ProjectView)
def finish(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    _owned(conn, user, project_id)
    try:
        project = finish_project(conn, request.app.state.settings, user.id, project_id)
    except KeyError as exc:
        raise ApiError(404, "not_found", "Проект не найден") from exc
    return ProjectView(**project)
