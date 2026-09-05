"""Файлы ассетов наружу.

GET /files/... отдаёт само приложение (локально и в тестах). На VM тот же путь перехватывает Caddy:
forward_auth спрашивает GET /internal/authz, а файл отдаёт file_server с диска (Range и большие файлы
идут мимо Python). Правила доступа в обоих случаях одни: authorize_file.

Разница, о которой должен знать клиент: отсутствие самого файла на диске (производный файл ещё не готов)
здесь даёт наш JSON с кодом not_found, а на VM — обычный 404 от file_server без тела в нашем формате.
Поэтому по 404 на /files/... клиент ориентируется на статус, а не на разбор тела.
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from server.app.auth.deps import CurrentUser, current_user
from server.app.config import Settings
from server.app.errors import ApiError
from server.app.storage import PUBLIC_FILES, asset_dir, parse_file_url, render_dir
from server.app.util import iso, utcnow
from server.db.core import get_db

router = APIRouter(tags=["files"])

TOUCH_MIN_INTERVAL = timedelta(minutes=1)
FILE_CACHE = "private, max-age=3600"


def authorize_file(
    conn: sqlite3.Connection,
    settings: Settings,
    user: CurrentUser,
    user_id: str,
    owner_id: str,
    name: str,
    kind: str = "asset",
) -> Path:
    """Путь к файлу или ApiError: 403 для непубличных имён (source.*), 404 для чужого и несуществующего.

    owner_id — это ассет для kind="asset" и проект для kind="render".
    """
    if user_id != user.id:
        raise ApiError(404, "not_found", "Файл не найден")
    if kind == "render":
        render_id = name[: -len(".mp4")]
        row = conn.execute(
            "SELECT id FROM renders WHERE id = ? AND project_id = ? AND user_id = ?",
            (render_id, owner_id, user_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "not_found", "Файл не найден")
        return render_dir(settings, user_id, owner_id) / name
    if name not in PUBLIC_FILES:
        raise ApiError(403, "forbidden", "Этот файл наружу не отдаётся")
    row = conn.execute("SELECT id FROM assets WHERE id = ? AND user_id = ?", (owner_id, user_id)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Файл не найден")
    return asset_dir(settings, user_id, owner_id) / name


def download_disposition(project_name: str, fallback: str) -> str:
    """Заголовок вложения с именем проекта. Имена у нас кириллические, а в заголовок HTTP такое
    напрямую не положить (latin-1), поэтому имя идёт вторым параметром в кодировке из RFC 5987,
    а ASCII-запасное (rnd_….mp4) остаётся для старых клиентов. Кавычки, слэши и переводы строк
    убираем: иначе заголовок можно разорвать самим названием проекта."""
    bad = ('"', "\\", "/", "\r", "\n", "\t")
    cleaned = "".join(ch for ch in project_name if ch not in bad).strip()
    name = f"{cleaned}.mp4" if cleaned else fallback
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"


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


@router.get("/files/{user_id}/projects/{project_id}/renders/{name}", include_in_schema=False)
def serve_render(
    request: Request,
    user_id: str,
    project_id: str,
    name: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> FileResponse:
    """Готовый ролик отдаётся вложением: браузер сохраняет его, а не открывает вкладкой."""
    parsed = parse_file_url(f"/files/{user_id}/projects/{project_id}/renders/{name}")
    if parsed is None:
        raise ApiError(404, "not_found", "Файл не найден")
    settings = request.app.state.settings
    path = authorize_file(conn, settings, user, user_id, project_id, name, "render")
    if not path.is_file():
        raise ApiError(404, "not_found", "Файл ещё не готов")
    row = conn.execute(
        "SELECT name FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id)
    ).fetchone()
    return FileResponse(
        path,
        headers={
            "Cache-Control": FILE_CACHE,
            "Content-Disposition": download_disposition(row["name"] if row else "", name),
        },
    )


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
    user_id, owner_id, name, kind = parsed
    authorize_file(conn, request.app.state.settings, user, user_id, owner_id, name, kind)
    if kind == "asset":
        touch_last_access(conn, owner_id)
    return Response(status_code=204)
