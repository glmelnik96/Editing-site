"""Загрузка по частям: /api/v1/uploads. Часть приходит сырыми байтами и пишется потоком по смещению."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from server.app.auth.deps import CurrentUser, current_user
from server.app.errors import ApiError
from server.app.uploads.store import (
    ChunkWriter,
    UploadError,
    chunk_length,
    complete_upload,
    create_upload,
    delete_upload,
    get_upload,
    mark_chunk,
    received_chunks,
    total_chunks,
)
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])

WRITE_BATCH = 4 * 1024 * 1024  # столько буферим в памяти между записями на диск


class UploadCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1)
    kind: str | None = None


class UploadCreated(BaseModel):
    upload_id: str
    chunk_size: int
    total_chunks: int
    expires_at: str


class UploadStatus(BaseModel):
    upload_id: str
    received: list[int]
    total: int
    size: int
    chunk_size: int


class UploadCompleted(BaseModel):
    asset_id: str
    status: str


def api_error(exc: UploadError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message, exc.details)


def _owned(conn: sqlite3.Connection, user: CurrentUser, upload_id: str) -> sqlite3.Row:
    row = get_upload(conn, user.id, upload_id)
    if row is None:
        raise ApiError(404, "not_found", "Загрузка не найдена")
    return row


def _mismatch(expected: int, received: int) -> ApiError:
    return ApiError(
        422,
        "chunk_size_mismatch",
        "Длина части не совпала с ожидаемой",
        {"expected": expected, "received": received},
    )


@router.post("", status_code=201, response_model=UploadCreated)
def create(
    body: UploadCreate,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> UploadCreated:
    if not request.app.state.upload_limiter.allow(user.id):
        raise ApiError(429, "rate_limited", "Слишком много новых загрузок, подождите час")
    try:
        row = create_upload(
            conn, request.app.state.settings, user.id, filename=body.filename, size=body.size, kind=body.kind
        )
    except UploadError as exc:
        raise api_error(exc) from exc
    return UploadCreated(
        upload_id=row["id"],
        chunk_size=row["chunk_size"],
        total_chunks=total_chunks(row),
        expires_at=row["expires_at"],
    )


@router.get("/{upload_id}", response_model=UploadStatus)
def status(
    upload_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> UploadStatus:
    row = _owned(conn, user, upload_id)
    return UploadStatus(
        upload_id=row["id"],
        received=received_chunks(conn, row["id"]),
        total=total_chunks(row),
        size=row["size"],
        chunk_size=row["chunk_size"],
    )


@router.put("/{upload_id}/chunks/{idx}", status_code=204)
async def put_chunk(
    upload_id: str,
    idx: int,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    row = _owned(conn, user, upload_id)
    total = total_chunks(row)
    if idx < 0 or idx >= total:
        raise ApiError(404, "no_such_chunk", "Нет части с таким номером", {"total": total})
    expected = chunk_length(row, idx)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) != expected:
        raise _mismatch(expected, int(declared))
    try:
        writer = ChunkWriter(Path(row["path"]), offset=idx * row["chunk_size"], expected=expected)
    except OSError as exc:
        raise ApiError(410, "file_missing", "Файл загрузки пропал, начните заново") from exc
    try:
        buf = bytearray()
        async for piece in request.stream():
            buf += piece
            if len(buf) >= WRITE_BATCH:
                await run_in_threadpool(writer.write, bytes(buf))
                buf.clear()
        if buf:
            await run_in_threadpool(writer.write, bytes(buf))
    except UploadError as exc:
        raise api_error(exc) from exc
    finally:
        writer.close()
    if not writer.done():
        raise _mismatch(expected, writer.written)
    mark_chunk(conn, row["id"], idx)
    return Response(status_code=204)


@router.post("/{upload_id}/complete", response_model=UploadCompleted)
def complete(
    upload_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> UploadCompleted:
    row = _owned(conn, user, upload_id)
    try:
        asset = complete_upload(conn, request.app.state.settings, row)
    except UploadError as exc:
        raise api_error(exc) from exc
    return UploadCompleted(asset_id=asset["id"], status=asset["status"])


@router.delete("/{upload_id}", status_code=204)
def cancel(
    upload_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    delete_upload(conn, _owned(conn, user, upload_id))
    return Response(status_code=204)
