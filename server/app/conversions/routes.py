"""Конвертер форматов: /api/v1/assets/{id}/convert и /api/v1/conversions/{id}."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from server.app.assets.routes import get_asset
from server.app.auth.deps import CurrentUser, current_user
from server.app.conversions.store import (
    delete_conversion,
    evict_oldest,
    get_conversion,
    list_conversions,
)
from server.app.errors import ApiError
from server.app.jobs import enqueue_job
from server.app.projects.store import active_renders
from server.app.util import new_id
from server.db.core import get_db, transaction
from server.media.convert import FORMATS, has_mp3_encoder

router = APIRouter(prefix="/api/v1", tags=["conversions"])

READY = ("ready", "proxy_ready")
AUDIO_FORMATS = {"mp3", "m4a", "wav"}


class ConvertRequest(BaseModel):
    format: str


class ConvertQueued(BaseModel):
    job_id: str
    conversion_id: str


class ConversionView(BaseModel):
    id: str
    asset_id: str
    format: str
    size: int
    duration: float
    created_at: str
    expires_at: str
    download: str


class ConversionList(BaseModel):
    conversions: list[ConversionView]


def _owned_asset(conn: sqlite3.Connection, user: CurrentUser, asset_id: str) -> sqlite3.Row:
    row = get_asset(conn, user.id, asset_id)
    if row is None:
        raise ApiError(404, "not_found", "Ассет не найден")
    return row


@router.post("/assets/{asset_id}/convert", status_code=202, response_model=ConvertQueued)
def convert(
    asset_id: str,
    body: ConvertRequest,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ConvertQueued:
    asset = _owned_asset(conn, user, asset_id)
    fmt = body.format
    if fmt not in FORMATS:
        raise ApiError(422, "invalid_format", "Неизвестный формат конвертации")
    if asset["status"] not in READY:
        raise ApiError(422, "asset_not_ready", "Файл ещё обрабатывается")
    if fmt in AUDIO_FORMATS and not asset["has_audio"]:
        raise ApiError(422, "no_audio", "В файле нет звука")
    if fmt == "mp4" and asset["kind"] != "video":
        raise ApiError(422, "not_video", "В mp4 можно собрать только видео")
    duration = float(asset["duration"] or 0)
    settings = request.app.state.settings
    if duration > settings.max_total_duration_sec:
        raise ApiError(422, "too_long", "Файл длиннее допустимого")
    if fmt == "mp3" and not has_mp3_encoder(settings):
        raise ApiError(
            503, "encoder_unavailable", "В этой сборке ffmpeg нет кодека MP3, выберите m4a или wav"
        )

    with transaction(conn):
        pending = conn.execute(
            "SELECT 1 FROM jobs WHERE target_id = ? AND type = 'convert' "
            "AND status IN ('queued', 'running')",
            (asset_id,),
        ).fetchone()
        if pending is not None:
            raise ApiError(409, "already_queued", "Конвертация этого файла уже идёт")
        if active_renders(conn, user.id) > settings.max_renders_queued:
            raise ApiError(409, "too_many_renders", "Уже собирается слишком много роликов, подождите")
        evict_oldest(conn, asset_id)
        conversion_id = new_id("cnv")
        job_id = enqueue_job(
            conn, user_id=user.id, type_="convert", target_id=asset_id,
            params={"format": fmt, "conversion_id": conversion_id},
        )
    return ConvertQueued(job_id=job_id, conversion_id=conversion_id)


@router.get("/assets/{asset_id}/conversions", response_model=ConversionList)
def list_(
    asset_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ConversionList:
    _owned_asset(conn, user, asset_id)
    return ConversionList(
        conversions=[ConversionView(**c) for c in list_conversions(conn, user.id, asset_id)]
    )


@router.get("/conversions/{conversion_id}", response_model=ConversionView)
def get_(
    conversion_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ConversionView:
    row = get_conversion(conn, user.id, conversion_id)
    if row is None:
        raise ApiError(404, "not_found", "Конверсия не найдена")
    return ConversionView(**row)


@router.delete("/conversions/{conversion_id}", status_code=204)
def delete(
    conversion_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    if not delete_conversion(conn, user.id, conversion_id):
        raise ApiError(404, "not_found", "Конверсия не найдена")
    return Response(status_code=204)
