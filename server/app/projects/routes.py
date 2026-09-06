"""Проекты: /api/v1/projects. Документ приходит и уходит целиком, версия защищает от гонки правок."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from server.app.auth.deps import CurrentUser, current_user
from server.app.errors import ApiError
from server.app.jobs import enqueue_job
from server.app.projects.doc import ProjectInvalid
from server.app.projects.store import (
    ProjectConflict,
    ProjectLimit,
    SubtitlesUnavailable,
    active_renders,
    build_project_subtitles,
    create_checkpoint,
    create_project,
    delete_project,
    finish_project,
    get_project,
    list_projects,
    list_renders,
    list_versions,
    restore_version,
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
    # Документ обязателен: сохранение приходит целиком, и пропуск поля стёр бы весь монтаж.
    # Пустой проект создаётся через POST без doc, а не сохранением без него.
    doc: dict


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


class CheckpointCreate(BaseModel):
    label: str = Field(default="", max_length=200)


class RestoreRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=64)


class VersionView(BaseModel):
    id: str
    version: int
    label: str
    name: str
    created_at: str
    clips_count: int
    duration: float


class VersionList(BaseModel):
    versions: list[VersionView]


@router.post("/{project_id}/checkpoint", status_code=201, response_model=VersionView)
def checkpoint(
    project_id: str,
    body: CheckpointCreate,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> VersionView:
    _owned(conn, user, project_id)
    try:
        made = create_checkpoint(
            conn, request.app.state.settings, user.id, project_id, label=body.label
        )
    except ProjectInvalid as exc:
        raise invalid(exc) from exc
    except KeyError as exc:
        raise ApiError(404, "not_found", "Проект не найден") from exc
    return VersionView(**made)


@router.get("/{project_id}/versions", response_model=VersionList)
def versions(
    project_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> VersionList:
    _owned(conn, user, project_id)
    return VersionList(versions=[VersionView(**v) for v in list_versions(conn, user.id, project_id)])


@router.post("/{project_id}/restore", response_model=ProjectView)
def restore(
    project_id: str,
    body: RestoreRequest,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    _owned(conn, user, project_id)
    try:
        project = restore_version(
            conn, request.app.state.settings, user.id, project_id, body.version_id
        )
    except ProjectInvalid as exc:
        raise invalid(exc) from exc
    except ProjectConflict as exc:
        raise conflict(exc) from exc
    except KeyError as exc:
        raise ApiError(404, "not_found", "Точка сохранения не найдена") from exc
    return ProjectView(**project)


class RenderRequest(BaseModel):
    quality: Literal["draft", "final"] = "draft"


class RenderQueued(BaseModel):
    job_id: str
    quality: str


class RenderView(BaseModel):
    id: str
    project_id: str
    quality: str
    size: int
    duration: float
    created_at: str
    expires_at: str
    download: str


class RenderList(BaseModel):
    renders: list[RenderView]


@router.post("/{project_id}/render", status_code=202, response_model=RenderQueued)
def render(
    project_id: str,
    body: RenderRequest,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> RenderQueued:
    """Ставит сборку в очередь. Ход виден в задании, готовый ролик появится в списке рендеров."""
    project = _owned(conn, user, project_id)
    if not project["doc"].get("clips"):
        raise ApiError(422, "empty_project", "В проекте нет клипов")
    settings = request.app.state.settings
    # Предел считает и очередь, и выполняющееся: на слабой машине третий всё равно ждёт.
    if active_renders(conn, user.id) > settings.max_renders_queued:
        raise ApiError(409, "too_many_renders", "Уже собирается слишком много роликов, подождите")
    job_id = enqueue_job(
        conn, user_id=user.id, type_="render", target_id=project_id, params={"quality": body.quality}
    )
    return RenderQueued(job_id=job_id, quality=body.quality)


@router.get("/{project_id}/renders", response_model=RenderList)
def renders(
    project_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> RenderList:
    _owned(conn, user, project_id)
    return RenderList(renders=[RenderView(**r) for r in list_renders(conn, user.id, project_id)])


@router.get("/{project_id}/subtitles")
def subtitles(
    project_id: str,
    request: Request,
    fmt: Annotated[Literal["srt", "vtt"], Query(alias="format")] = "srt",
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    """Субтитры проекта — ровно тот файл, который уйдёт в сборку ролика.

    Отдаём до рендера, а не после: агент смонтировал проект по транскрипту и хочет вычитать текст
    в шкале ролика, пока ещё есть что править. Сборка ленивая, дальше работает кэш версии.
    """
    project = _owned(conn, user, project_id)
    try:
        srt = build_project_subtitles(conn, request.app.state.settings, project)
    except SubtitlesUnavailable as exc:
        # Текст берём у сборки: она формулирует отказ для человека, и вторая формулировка здесь
        # разошлась бы с той, что видно в карточке упавшего задания.
        raise ApiError(422, "no_transcript", str(exc)) from exc
    if srt is None:
        raise ApiError(
            422,
            "no_transcript_subtitles",
            "В проекте нет субтитров из расшифровки: загруженный файл лежит у своего ассета",
        )
    path = srt if fmt == "srt" else srt.with_suffix(".vtt")
    return Response(content=path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")
