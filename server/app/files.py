"""Файлы ассетов наружу.

GET /files/... отдаёт само приложение (локально и в тестах). На VM тот же путь перехватывает Caddy:
forward_auth спрашивает GET /internal/authz, а файл отдаёт file_server с диска (Range и большие файлы
идут мимо Python). Правила доступа в обоих случаях одни: authorize_file.
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from server.app.auth.deps import CurrentUser, current_user
from server.app.config import Settings
from server.app.errors import ApiError
from server.app.storage import PUBLIC_FILES, asset_dir, parse_file_url
from server.app.util import iso, utcnow
from server.db.core import get_db

router = APIRouter(tags=["files"])

TOUCH_MIN_INTERVAL = timedelta(minutes=1)
FILE_CACHE = "private, max-age=3600"


def authorize_file(
    conn: sqlite3.Connection, settings: Settings, user: CurrentUser, user_id: str, asset_id: str, name: str
) -> Path:
    """Путь к файлу или ApiError: 403 для непубличных имён (source.*), 404 для чужого и несуществующего."""
    if name not in PUBLIC_FILES:
        raise ApiError(403, "forbidden", "Этот файл наружу не отдаётся")
    if user_id != user.id:
        raise ApiError(404, "not_found", "Файл не найден")
    row = conn.execute("SELECT id FROM assets WHERE id = ? AND user_id = ?", (asset_id, user_id)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Файл не найден")
    return asset_dir(settings, user_id, asset_id) / name


def touch_last_access(conn: sqlite3.Connection, asset_id: str) -> None:
    """Не чаще раза в минуту (раздел 3 спеки); сравнение в SQL, чтобы не писать в WAL на каждый запрос."""
    now = utcnow()
    conn.execute(
        "UPDATE assets SET last_access_at = ? WHERE id = ? AND last_access_at < ?",
        (iso(now), asset_id, iso(now - TOUCH_MIN_INTERVAL)),
    )


@router.get("/files/{user_id}/assets/{asset_id}/{name}", include_in_schema=False)
def serve_file(
    request: Request,
    user_id: str,
    asset_id: str,
    name: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> FileResponse:
    path = authorize_file(conn, request.app.state.settings, user, user_id, asset_id, name)
    if not path.is_file():
        raise ApiError(404, "not_found", "Файл ещё не готов")
    touch_last_access(conn, asset_id)
    return FileResponse(path, headers={"Cache-Control": FILE_CACHE})


@router.get("/internal/authz", include_in_schema=False)
def authz(
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    """Для Caddy forward_auth: любой 2xx разрешает отдать файл с диска. Путь приходит в X-Forwarded-Uri."""
    uri = request.headers.get("x-forwarded-uri", "").split("?", 1)[0]
    parsed = parse_file_url(uri)
    if parsed is None:
        raise ApiError(404, "not_found", "Файл не найден")
    user_id, asset_id, name = parsed
    authorize_file(conn, request.app.state.settings, user, user_id, asset_id, name)
    touch_last_access(conn, asset_id)
    return Response(status_code=204)
