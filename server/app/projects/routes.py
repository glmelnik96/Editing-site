"""Проекты: /api/v1/projects. Документ приходит и уходит целиком, версия защищает от гонки правок."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from server.app.assets.views import AssetView, asset_view
from server.app.auth.deps import CurrentUser, current_user
from server.app.errors import ApiError
from server.app.jobs import enqueue_job
from server.app.projects.doc import ProjectInvalid
from server.app.projects.store import (
    NoCues,
    ProjectConflict,
    ProjectLimit,
    SubtitlesUnavailable,
    active_renders,
    build_project_subtitles,
    create_checkpoint,
    create_project,
    delete_project,
    generate_project_cues,
    get_project,
    list_projects,
    list_renders,
    list_versions,
    project_owner,
    restore_version,
    save_project,
)
from server.db.core import get_db
from server.media.convert import missing_encoder

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
    created_at: str
    updated_at: str
    doc: dict[str, Any]


class ProjectCard(BaseModel):
    id: str
    name: str
    version: int
    created_at: str
    updated_at: str
    clips_count: int
    duration: float


class AssetList(BaseModel):
    assets: list[AssetView]


class ProjectList(BaseModel):
    projects: list[ProjectCard]


def invalid(exc: ProjectInvalid) -> ApiError:
    return ApiError(422, "invalid_project", "Документ проекта не прошёл проверку", {"errors": exc.errors})


def conflict(exc: ProjectConflict) -> ApiError:
    return ApiError(
        409, "version_conflict", "Проект изменился, перечитайте его", {"project": exc.project}
    )


def _owned(conn: sqlite3.Connection, user: CurrentUser, project_id: str) -> tuple[dict, str]:
    """Проект и его владелец. Админ работает и с чужими: он отвечает за общий диск и за то, чтобы
    работа команды не встала, пока автор в отпуске.

    Владельца возвращаем отдельно, потому что дальше маршрут действует от его имени, а не от имени
    вызывающего: файлы проекта лежат в каталоге владельца, а сохранение ищет строку по его id.
    Чужому не-админу отвечаем 404, а не 403 — сообщать, что чужой проект существует, незачем.
    """
    owner = project_owner(conn, project_id)
    if owner is None or (owner != user.id and user.role != "admin"):
        raise ApiError(404, "not_found", "Проект не найден")
    project = get_project(conn, owner, project_id)
    if project is None:
        raise ApiError(404, "not_found", "Проект не найден")
    return project, owner


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
    project, _ = _owned(conn, user, project_id)
    return ProjectView(**project)


@router.put("/{project_id}", response_model=ProjectView)
def save(
    project_id: str,
    body: ProjectSave,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    _, owner = _owned(conn, user, project_id)
    try:
        project = save_project(
            conn, request.app.state.settings, owner, project_id,
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
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    _, owner = _owned(conn, user, project_id)
    if not delete_project(conn, request.app.state.settings, owner, project_id):
        raise ApiError(404, "not_found", "Проект не найден")
    return Response(status_code=204)


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
    _, owner = _owned(conn, user, project_id)
    try:
        made = create_checkpoint(
            conn, request.app.state.settings, owner, project_id, label=body.label
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
    _, owner = _owned(conn, user, project_id)
    return VersionList(versions=[VersionView(**v) for v in list_versions(conn, owner, project_id)])


@router.post("/{project_id}/restore", response_model=ProjectView)
def restore(
    project_id: str,
    body: RestoreRequest,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    _, owner = _owned(conn, user, project_id)
    try:
        project = restore_version(
            conn, request.app.state.settings, owner, project_id, body.version_id
        )
    except ProjectInvalid as exc:
        raise invalid(exc) from exc
    except ProjectConflict as exc:
        raise conflict(exc) from exc
    except KeyError as exc:
        raise ApiError(404, "not_found", "Точка сохранения не найдена") from exc
    return ProjectView(**project)


class RenderRequest(BaseModel):
    quality: Literal["draft", "final", "preview", "medium", "high", "target"] = "draft"
    format: Literal["mp4", "webm", "m4a"] = "mp4"
    # Короткая сторона кадра; длинную даёт пропорция проекта. None — по качеству.
    short_side: Literal[360, 480, 720, 1080, 1440, 2160] | None = None
    # Только для целевого качества: средний битрейт видео, кбит/с.
    bitrate_kbps: int | None = Field(default=None, ge=300, le=50_000)


class RenderQueued(BaseModel):
    job_id: str
    quality: str
    format: str = "mp4"


class RenderView(BaseModel):
    id: str
    project_id: str
    quality: str
    format: str = "mp4"
    width: int | None = None
    height: int | None = None
    video_bitrate: int | None = None
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
    project, owner = _owned(conn, user, project_id)
    if not project["doc"].get("clips"):
        raise ApiError(422, "empty_project", "В проекте нет клипов")
    settings = request.app.state.settings
    # Предел считает и очередь, и выполняющееся: на слабой машине третий всё равно ждёт.
    if active_renders(conn, owner) > settings.max_renders_queued:
        raise ApiError(409, "too_many_renders", "Уже собирается слишком много роликов, подождите")
    if body.quality == "target" and body.bitrate_kbps is None:
        raise ApiError(422, "bitrate_required", "Для целевого качества назовите битрейт")
    if body.format == "webm":
        # Без VP9 или Opus сборка упала бы через минуты кодирования сырым stderr — отказ сразу.
        missing = missing_encoder(settings, "webm")
        if missing:
            raise ApiError(503, "encoder_unavailable", missing)
    params: dict = {"quality": body.quality, "format": body.format}
    if body.short_side is not None:
        params["short_side"] = body.short_side
    if body.bitrate_kbps is not None:
        params["bitrate_kbps"] = body.bitrate_kbps
    job_id = enqueue_job(
        # Задание и готовый ролик принадлежат владельцу: файл ложится в его каталог, и путь
        # /files/{владелец}/projects/… иначе не сошёлся бы.
        conn, user_id=owner, type_="render", target_id=project_id, params=params
    )
    return RenderQueued(job_id=job_id, quality=body.quality, format=body.format)


@router.get("/{project_id}/assets", response_model=AssetList)
def project_assets(
    project_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AssetList:
    """Записи, из которых можно собрать этот проект, — то есть записи его владельца.

    Редактор берёт список отсюда, а не из общего /assets: документ обязан ссылаться на записи
    владельца (проверка проекта их и сверяет), а у админа, открывшего чужой проект, свой список
    совсем другой — все клипы выглядели бы необработанными.
    """
    _, owner = _owned(conn, user, project_id)
    rows = conn.execute(
        "SELECT * FROM assets WHERE user_id = ? ORDER BY created_at DESC, id", (owner,)
    ).fetchall()
    transcribed = {
        r["asset_id"]
        for r in conn.execute("SELECT asset_id FROM transcripts WHERE user_id = ?", (owner,))
    }
    return AssetList(assets=[asset_view(r, has_transcript=r["id"] in transcribed) for r in rows])


@router.get("/{project_id}/renders", response_model=RenderList)
def renders(
    project_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> RenderList:
    _, owner = _owned(conn, user, project_id)
    return RenderList(renders=[RenderView(**r) for r in list_renders(conn, owner, project_id)])


class SubtitlesGenerate(BaseModel):
    # asset_id больше не выбирает запись: реплики всегда из клипов шкалы. Поле оставлено, чтобы
    # старый клиент не получил 422 на знакомом теле запроса.
    asset_id: str | None = Field(default=None, max_length=64)
    mode: Literal["burn", "soft"] = "burn"
    # Версия необязательна: реплики собираются из документа, который лежит на сервере, и свежую
    # копию для этого держать не нужно. Но если клиент её прислал — правило то же, что у PUT:
    # поверх чужой правки не сохраняем.
    version: int | None = Field(default=None, ge=1)


@router.post("/{project_id}/subtitles/generate", response_model=ProjectView)
def generate_subtitles(
    project_id: str,
    body: SubtitlesGenerate,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    """Собирает реплики из расшифровки и кладёт их в документ проекта обычным сохранением.

    Дальше ролик собирается из этих реплик, а не из расшифровки заново: человек их вычитывает и
    правит карточками, и его правка обязана дожить до вжигания.
    """
    project, owner = _owned(conn, user, project_id)
    if not project["doc"].get("clips"):
        raise ApiError(422, "empty_project", "В проекте нет клипов")
    try:
        saved = generate_project_cues(
            conn, request.app.state.settings, owner, project,
            mode=body.mode,
            version=project["version"] if body.version is None else body.version,
        )
    except SubtitlesUnavailable as exc:
        # Текст берём у сборки: вторая формулировка здесь разошлась бы с той, что видно в ответе
        # ручки субтитров и в карточке упавшего задания.
        raise ApiError(422, "no_transcript", str(exc)) from exc
    except NoCues as exc:
        raise ApiError(422, "no_cues", str(exc)) from exc
    except ProjectInvalid as exc:
        raise invalid(exc) from exc
    except ProjectConflict as exc:
        raise conflict(exc) from exc
    except KeyError as exc:
        # Проект удалили между проверкой владения и записью: для клиента это «не найден».
        raise ApiError(404, "not_found", "Проект не найден") from exc
    return ProjectView(**saved)


@router.get("/{project_id}/subtitles")
def subtitles(
    project_id: str,
    request: Request,
    fmt: Annotated[Literal["srt", "vtt"], Query(alias="format")] = "srt",
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    """Файл субтитров из реплик или расшифровки — не обязательно то, что уйдёт в кадр.

    Отдаём до рендера: агент хочет вычитать текст в шкале ролика. Сборка ролика смотрит на
    `enabled` и может пропустить этот файл; эта ручка на признак не смотрит. Сборка ленивая,
    дальше работает кэш версии.
    """
    project, _ = _owned(conn, user, project_id)
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
