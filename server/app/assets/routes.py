"""Ассеты: список, карточка, удаление, одноразовая загрузка мелких файлов (SRT, музыка до 64 МБ)."""
from __future__ import annotations

import shutil
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from server.app.assets.views import AssetView, asset_view
from server.app.auth.deps import CurrentUser, current_user
from server.app.errors import ApiError
from server.app.jobs import cancel_jobs_for_target
from server.app.storage import asset_dir, upload_path
from server.app.uploads.routes import api_error
from server.app.uploads.store import UploadError, check_capacity, clean_filename, finalize_file, resolve_kind
from server.app.util import new_id
from server.db.core import get_db, transaction

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])

READ_PIECE = 1024 * 1024


class AssetList(BaseModel):
    assets: list[AssetView]


def get_asset(conn: sqlite3.Connection, user_id: str, asset_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM assets WHERE id = ? AND user_id = ?", (asset_id, user_id)).fetchone()


def _owned(conn: sqlite3.Connection, user: CurrentUser, asset_id: str) -> sqlite3.Row:
    row = get_asset(conn, user.id, asset_id)
    if row is None:
        raise ApiError(404, "not_found", "Ассет не найден")
    return row


@router.get("", response_model=AssetList)
def list_(
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AssetList:
    rows = conn.execute("SELECT * FROM assets WHERE user_id = ? ORDER BY created_at DESC, id", (user.id,))
    return AssetList(assets=[asset_view(r) for r in rows])


@router.post("/upload", status_code=201, response_model=AssetView)
# Тело разбирает Starlette до входа сюда, поэтому наш лимит отсекает файл уже после приёма:
# настоящий предел стоит на Caddy (request_body max_size 68MB на этом маршруте).
async def upload_small(
    request: Request,
    file: UploadFile,
    kind: Annotated[str | None, Form()] = None,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AssetView:
    """Файл целиком одним запросом. Пишется во временный файл рядом с загрузками,
    дальше тот же finalize_file."""
    settings = request.app.state.settings
    if not request.app.state.upload_limiter.allow(user.id):
        raise ApiError(429, "rate_limited", "Слишком много новых загрузок, подождите час")
    tmp = upload_path(settings, new_id("tmp"))
    tmp.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        filename = clean_filename(file.filename or "")
        resolved = resolve_kind(filename, kind)
        # open/write в потоке: блокирующий файловый ввод-вывод не должен стопорить event loop.
        out = await run_in_threadpool(open, tmp, "wb")
        try:
            while piece := await file.read(READ_PIECE):
                size += len(piece)
                if size > settings.small_upload_max_bytes:
                    raise UploadError(
                        413,
                        "too_large",
                        "Файл больше допустимого для одноразовой загрузки",
                        {"limit_bytes": settings.small_upload_max_bytes},
                    )
                await run_in_threadpool(out.write, piece)
        finally:
            await run_in_threadpool(out.close)
        check_capacity(conn, settings, user.id, size)
        row = finalize_file(
            conn, settings, user_id=user.id, src=tmp, filename=filename, size=size, kind=resolved,
            check_quota=True,
        )
    except UploadError as exc:
        tmp.unlink(missing_ok=True)
        raise api_error(exc) from exc
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return asset_view(_owned(conn, user, row["id"]))


@router.get("/{asset_id}", response_model=AssetView)
def get_(
    asset_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AssetView:
    return asset_view(_owned(conn, user, asset_id))


@router.delete("/{asset_id}", status_code=204)
def delete(
    asset_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    """Сначала запись, потом файлы: упавший процесс не оставит запись без файлов, папку подберёт janitor.
    Проверка «ассет стоит в незавершённом проекте» появится в M2 вместе с таблицей projects."""
    with transaction(conn):
        cur = conn.execute("DELETE FROM assets WHERE id = ? AND user_id = ?", (asset_id, user.id))
        if cur.rowcount == 0:
            raise ApiError(404, "not_found", "Ассет не найден")
        cancel_jobs_for_target(conn, asset_id)
    shutil.rmtree(asset_dir(request.app.state.settings, user.id, asset_id), ignore_errors=True)
    return Response(status_code=204)
