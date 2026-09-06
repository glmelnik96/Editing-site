"""Правила очистки. Каждая функция делает одно действие и возвращает счётчик; журнал пишет вызывающий.
Порядок «сначала запись, потом файлы» (раздел 6.3 спеки): упавший процесс не оставляет записи без файлов.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.config import Settings
from server.app.jobs import cancel_jobs_for_target
from server.app.projects.store import assets_in_drafts
from server.app.storage import asset_dir
from server.app.util import iso
from server.db.core import connect, transaction

ORPHAN_MIN_AGE_SEC = 3600  # моложе часа не трогаем: загрузка может завершаться прямо сейчас
JOB_STALE_AFTER_SEC = 120
JOB_MAX_ATTEMPTS = 2
BACKUP_KEEP = 7
WORKER_LOST = "воркер пропал без вести (нет пульса дольше 2 минут)"

log = logging.getLogger("video.janitor")


def _rmtree(path: Path) -> None:
    """Удаляет каталог, не падая на первой ошибке. Janitor существует ради свободного места,
    поэтому если каталог всё же остался — это видно в логе, а не теряется молча."""
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        log.warning("не удалось удалить каталог %s", path)


def delete_expired_uploads(conn: sqlite3.Connection, now: datetime) -> int:
    """DELETE условный по expires_at: если загрузку успели завершить (finalize_file убрал
    строку) между SELECT и этим DELETE, rowcount будет 0 и файл трогать не нужно."""
    cutoff = iso(now)
    rows = conn.execute("SELECT id, path FROM uploads WHERE expires_at < ?", (cutoff,)).fetchall()
    deleted = 0
    for row in rows:
        with transaction(conn):
            cur = conn.execute(
                "DELETE FROM uploads WHERE id = ? AND expires_at < ?", (row["id"], cutoff)
            )
            if cur.rowcount == 0:
                continue  # успели завершить или уже удалили, пока мы шли по пачке
        Path(row["path"]).unlink(missing_ok=True)
        deleted += 1
    return deleted


def delete_expired_assets(conn: sqlite3.Connection, settings: Settings, now: datetime) -> int:
    """DELETE условный по last_access_at: пользователь мог открыть ассет (см. touch_last_access),
    пока мы шли по пачке, и продлить срок. rowcount == 0 значит запись уже не подходит под
    условие, и файлы трогать не нужно."""
    cutoff = iso(now - timedelta(hours=settings.asset_ttl_hours))
    rows = conn.execute("SELECT id, user_id FROM assets WHERE last_access_at < ?", (cutoff,)).fetchall()
    # Файл, стоящий в незавершённом проекте, не удаляем: срок считается от обращений, а к проекту
    # можно не возвращаться неделю, и монтаж от этого не устаревает.
    protected = assets_in_drafts(conn)
    deleted = 0
    for row in rows:
        if row["id"] in protected:
            continue
        with transaction(conn):
            cur = conn.execute(
                "DELETE FROM assets WHERE id = ? AND last_access_at < ?", (row["id"], cutoff)
            )
            if cur.rowcount == 0:
                continue  # ассет открыли, пока мы шли по пачке: срок продлён
            cancel_jobs_for_target(conn, row["id"])
        _rmtree(asset_dir(settings, row["user_id"], row["id"]))
        deleted += 1
    return deleted


def delete_expired_renders(conn: sqlite3.Connection, now: datetime) -> int:
    """Готовый ролик живёт render_ttl_hours (срок проставлен при сборке). Условие по expires_at
    повторяется в DELETE: если проект успели завершить и строка ушла, rowcount будет 0.
    Путь берём из строки: файл лежит в каталоге проекта, а сам каталог сносить нельзя —
    рядом могут быть свежие ролики."""
    cutoff = iso(now)
    rows = conn.execute("SELECT id, path FROM renders WHERE expires_at < ?", (cutoff,)).fetchall()
    deleted = 0
    for row in rows:
        with transaction(conn):
            cur = conn.execute(
                "DELETE FROM renders WHERE id = ? AND expires_at < ?", (row["id"], cutoff)
            )
            if cur.rowcount == 0:
                continue
        Path(row["path"]).unlink(missing_ok=True)
        deleted += 1
    return deleted


def _older_than(path: Path, now: datetime, seconds: int) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return False
    return (now - mtime).total_seconds() > seconds


def delete_orphans(conn: sqlite3.Connection, settings: Settings, now: datetime) -> int:
    """Папки ассетов и проектов без записи и файлы загрузок без записи, старше часа."""
    count = 0
    known_assets = {r[0] for r in conn.execute("SELECT id FROM assets")}
    for assets_root in settings.data_dir.glob("usr_*/assets"):
        for d in assets_root.iterdir():
            if d.is_dir() and d.name not in known_assets and _older_than(d, now, ORPHAN_MIN_AGE_SEC):
                _rmtree(d)
                count += 1
    # Каталог проекта переживает саму запись, если удаление оборвалось на полпути: в нём лежат
    # готовые ролики и кэш субтитров — по файлу на каждую собранную версию.
    known_projects = {r[0] for r in conn.execute("SELECT id FROM projects")}
    for projects_root in settings.data_dir.glob("usr_*/projects"):
        for d in projects_root.iterdir():
            if d.is_dir() and d.name not in known_projects and _older_than(d, now, ORPHAN_MIN_AGE_SEC):
                _rmtree(d)
                count += 1
    known_uploads = {r[0] for r in conn.execute("SELECT id FROM uploads")}
    if settings.uploads_tmp_path.is_dir():
        for f in settings.uploads_tmp_path.iterdir():
            if f.is_file() and f.name not in known_uploads and _older_than(f, now, ORPHAN_MIN_AGE_SEC):
                f.unlink(missing_ok=True)
                count += 1
    return count


def requeue_stale_jobs(conn: sqlite3.Connection, now: datetime) -> tuple[int, int]:
    """Задание в running без пульса дольше 2 минут: один раз назад в очередь, затем failed.
    Упавший analyze переводит ассет в failed, чтобы он не висел в analyzing вечно.

    Оба UPDATE повторяют условие по времени из SELECT: воркер мог прислать пульс, пока мы шли
    по выборке, тогда rowcount == 0, задание не трогаем и ассет в failed не переводим."""
    cutoff = iso(now - timedelta(seconds=JOB_STALE_AFTER_SEC))
    rows = conn.execute(
        "SELECT id, type, target_id, attempts FROM jobs "
        "WHERE status = 'running' AND coalesce(heartbeat_at, started_at, created_at) < ?",
        (cutoff,),
    ).fetchall()
    requeued = failed = 0
    with transaction(conn):
        for row in rows:
            if row["attempts"] < JOB_MAX_ATTEMPTS:
                cur = conn.execute(
                    "UPDATE jobs SET status = 'queued', worker_pid = NULL, heartbeat_at = NULL, "
                    "started_at = NULL, progress = 0 "
                    "WHERE id = ? AND coalesce(heartbeat_at, started_at, created_at) < ?",
                    (row["id"], cutoff),
                )
                requeued += cur.rowcount
            else:
                cur = conn.execute(
                    "UPDATE jobs SET status = 'failed', finished_at = ?, error = ? "
                    "WHERE id = ? AND coalesce(heartbeat_at, started_at, created_at) < ?",
                    (iso(now), WORKER_LOST, row["id"], cutoff),
                )
                if cur.rowcount == 0:
                    continue  # воркер прислал пульс между выборкой и апдейтом
                failed += 1
                if row["type"] == "analyze":
                    conn.execute(
                        "UPDATE assets SET status = 'failed', error = ? "
                        "WHERE id = ? AND status IN ('uploaded', 'analyzing')",
                        (WORKER_LOST, row["target_id"]),
                    )
    return requeued, failed


def delete_expired_sessions(conn: sqlite3.Connection, settings: Settings, now: datetime) -> int:
    idle_cutoff = iso(now - timedelta(days=settings.session_idle_days))
    cur = conn.execute(
        "DELETE FROM sessions WHERE absolute_expires_at < ? OR last_seen_at < ?", (iso(now), idle_cutoff)
    )
    return cur.rowcount


def backup_if_due(settings: Settings, now: datetime, keep: int = BACKUP_KEEP) -> Path | None:
    """Копия базы раз в сутки в data/backups/video-YYYYMMDD.db через sqlite backup API; хранится keep штук.
    Пишем во временный файл .part и переименовываем после успеха: оборванная копия не должна
    сойти за сегодняшний бэкап. .part не попадает под ротацию (та ищет video-*.db).
    Копия вне VM (scp на ПК) остаётся ручной операцией."""
    backups = settings.data_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    target = backups / f"video-{now:%Y%m%d}.db"
    if target.exists():
        return None
    tmp = target.with_suffix(".part")
    tmp.unlink(missing_ok=True)
    src = connect(settings.db_path)
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(tmp, target)
    for old in sorted(backups.glob("video-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return target
