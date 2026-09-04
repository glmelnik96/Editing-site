"""Загрузка по частям: запись в базе плюс файл в tmp/uploads; квота и свободный диск; завершение переносит
файл в папку ассета одним os.replace (тот же раздел диска) и ставит задание analyze.

Порядок «сначала база, потом файлы» при удалении и «сначала файл, потом база с откатом» при создании:
упавший процесс не оставляет записи без файла, а папку без записи подбирает janitor.
"""
from __future__ import annotations

import math
import os
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

from server.app.config import Settings
from server.app.health import disk_free_pct_safe
from server.app.jobs import enqueue_job
from server.app.storage import KINDS, asset_dir, kind_from_ext, safe_ext, upload_path
from server.app.util import iso, new_id, now_iso, utcnow
from server.db.core import transaction

ANALYZE_PRIORITY = 10  # выше рендера (0): раздел 7 спеки
MAX_FILENAME = 255


class UploadError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def total_chunks(upload: dict | sqlite3.Row) -> int:
    return max(1, math.ceil(upload["size"] / upload["chunk_size"]))


def chunk_length(upload: dict | sqlite3.Row, idx: int) -> int:
    """Все части ровно chunk_size, последняя короче."""
    last = total_chunks(upload) - 1
    return upload["chunk_size"] if idx < last else upload["size"] - last * upload["chunk_size"]


def used_bytes(conn: sqlite3.Connection, user_id: str) -> int:
    """Квота считает и готовые ассеты, и незавершённые загрузки: место под них уже занято."""
    assets = conn.execute(
        "SELECT coalesce(sum(size), 0) FROM assets WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    uploads = conn.execute(
        "SELECT coalesce(sum(size), 0) FROM uploads WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    return int(assets) + int(uploads)


def check_capacity(conn: sqlite3.Connection, settings: Settings, user_id: str, size: int) -> None:
    if size <= 0:
        raise UploadError(422, "empty_file", "Пустой файл")
    if size > settings.max_upload_bytes:
        raise UploadError(
            413, "too_large", "Файл больше допустимого", {"limit_bytes": settings.max_upload_bytes}
        )
    used = used_bytes(conn, user_id)
    if used + size > settings.user_quota_bytes:
        details = {"used_bytes": used, "limit_bytes": settings.user_quota_bytes}
        raise UploadError(413, "quota_exceeded", "Квота исчерпана", details)
    free = disk_free_pct_safe(settings.data_dir)
    if free < settings.disk_low_pct:
        raise UploadError(
            507, "disk_low", "На диске мало места, загрузки приостановлены", {"disk_free_pct": free}
        )


def clean_filename(filename: str) -> str:
    name = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > MAX_FILENAME:
        raise UploadError(422, "bad_filename", "Имя файла пустое или длиннее 255 знаков")
    return name


def resolve_kind(filename: str, kind: str | None) -> str:
    if kind is not None:
        if kind not in KINDS:
            raise UploadError(422, "bad_kind", "kind: video, audio или subtitle")
        return kind
    return kind_from_ext(safe_ext(filename)) or "video"


def reserve_file(path: Path, size: int) -> None:
    """Файл нужного размера. posix_fallocate занимает место сразу (ENOSPC при создании, а не на последней
    части); на Windows его нет, там разреженный файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        if hasattr(os, "posix_fallocate"):
            os.posix_fallocate(fd, 0, size)
        else:
            os.ftruncate(fd, size)
    finally:
        os.close(fd)


def create_upload(
    conn: sqlite3.Connection, settings: Settings, user_id: str, *, filename: str, size: int, kind: str | None
) -> dict:
    filename = clean_filename(filename)
    kind = resolve_kind(filename, kind)
    check_capacity(conn, settings, user_id, size)
    upload_id = new_id("upl")
    path = upload_path(settings, upload_id)
    try:
        reserve_file(path, size)
    except OSError as exc:
        raise UploadError(507, "disk_low", "Не удалось зарезервировать место под файл") from exc
    now = utcnow()
    row = {
        "id": upload_id,
        "user_id": user_id,
        "filename": filename,
        "size": size,
        "kind": kind,
        "chunk_size": settings.chunk_size,
        "path": str(path),
        "created_at": iso(now),
        "expires_at": iso(now + timedelta(hours=settings.upload_ttl_hours)),
    }
    try:
        conn.execute(
            "INSERT INTO uploads (id, user_id, filename, size, kind, chunk_size, path, created_at, "
            "expires_at) "
            "VALUES (:id, :user_id, :filename, :size, :kind, :chunk_size, :path, :created_at, :expires_at)",
            row,
        )
    except sqlite3.Error:
        path.unlink(missing_ok=True)
        raise
    return row


def get_upload(conn: sqlite3.Connection, user_id: str, upload_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM uploads WHERE id = ? AND user_id = ?", (upload_id, user_id)).fetchone()


class ChunkWriter:
    """Пишет одну часть по смещению кусками. Свой дескриптор на запрос: параллельные части не мешают
    друг другу. Не os.pwrite: его нет на Windows, где идёт разработка."""

    def __init__(self, path: Path, *, offset: int, expected: int) -> None:
        self.expected = expected
        self.written = 0
        self._f = open(path, "r+b")  # noqa: SIM115 - закрывается в close(), живёт дольше одного with
        self._f.seek(offset)

    def write(self, data: bytes) -> None:
        if self.written + len(data) > self.expected:
            raise UploadError(
                422, "chunk_size_mismatch", "Часть длиннее ожидаемой", {"expected": self.expected}
            )
        self._f.write(data)
        self.written += len(data)

    def done(self) -> bool:
        return self.written == self.expected

    def close(self) -> None:
        self._f.close()


def mark_chunk(conn: sqlite3.Connection, upload_id: str, idx: int) -> None:
    conn.execute("INSERT OR IGNORE INTO upload_chunks (upload_id, idx) VALUES (?, ?)", (upload_id, idx))


def received_chunks(conn: sqlite3.Connection, upload_id: str) -> list[int]:
    rows = conn.execute(
        "SELECT idx FROM upload_chunks WHERE upload_id = ? ORDER BY idx", (upload_id,)
    )
    return [r[0] for r in rows]


def finalize_file(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    user_id: str,
    src: Path,
    filename: str,
    size: int,
    kind: str,
    upload_id: str | None = None,
) -> dict:
    """Переносит готовый файл в папку ассета, создаёт запись ассета и задание analyze (кроме субтитров).
    При ошибке базы файл возвращается на место, чтобы завершение можно было повторить."""
    asset_id = new_id("ast")
    ext = safe_ext(filename)
    target_dir = asset_dir(settings, user_id, asset_id)
    target_dir.mkdir(parents=True, exist_ok=False)
    dst = target_dir / f"source.{ext}"
    try:
        os.replace(src, dst)  # тот же раздел; EXDEV означает неверный VIDEO_TMP_DIR
    except FileNotFoundError as exc:
        # Второй complete той же загрузки наперегонки с первым: файл уже переехал.
        target_dir.rmdir()
        raise UploadError(410, "file_missing", "Файл загрузки пропал, начните заново") from exc
    now = now_iso()
    status = "ready" if kind == "subtitle" else "uploaded"
    row = {
        "id": asset_id,
        "user_id": user_id,
        "kind": kind,
        "original_name": filename,
        "ext": ext,
        "size": size,
        "status": status,
        "created_at": now,
        "last_access_at": now,
    }
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, created_at, "
                "last_access_at) "
                "VALUES (:id, :user_id, :kind, :original_name, :ext, :size, :status, :created_at, "
                ":last_access_at)",
                row,
            )
            if upload_id is not None:
                conn.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
            if kind != "subtitle":
                enqueue_job(
                    conn, user_id=user_id, type_="analyze", target_id=asset_id, priority=ANALYZE_PRIORITY
                )
    except Exception:
        os.replace(dst, src)
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    return row


def complete_upload(conn: sqlite3.Connection, settings: Settings, upload: dict | sqlite3.Row) -> dict:
    total = total_chunks(upload)
    got = set(received_chunks(conn, upload["id"]))
    missing = [i for i in range(total) if i not in got]
    if missing:
        raise UploadError(409, "incomplete", "Дошли не все части", {"missing": missing[:100], "total": total})
    path = Path(upload["path"])
    try:
        actual = path.stat().st_size
    except OSError as exc:
        raise UploadError(410, "file_missing", "Файл загрузки пропал, начните заново") from exc
    if actual != upload["size"]:
        raise UploadError(409, "size_mismatch", "Размер файла не совпал с заявленным", {"actual": actual})
    return finalize_file(
        conn,
        settings,
        user_id=upload["user_id"],
        src=path,
        filename=upload["filename"],
        size=upload["size"],
        kind=upload["kind"],
        upload_id=upload["id"],
    )


def delete_upload(conn: sqlite3.Connection, upload: dict | sqlite3.Row) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM uploads WHERE id = ?", (upload["id"],))
    Path(upload["path"]).unlink(missing_ok=True)
