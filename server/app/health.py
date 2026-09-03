"""GET /healthz: база, свободный диск, пульс воркера. Без авторизации; поля описаны в схеме ответа.

Любая поломка базы, диска или пульса даёт статус degraded и 200, а не 500: внешний пинг и deploy.sh
читают тело.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from server.app.util import parse_iso, utcnow
from server.db.core import connect

router = APIRouter(tags=["health"])
log = logging.getLogger("video.health")

WORKER_STALE_AFTER_SEC = 120
DISK_LOW_PCT = 10.0


class Health(BaseModel):
    status: str
    db: bool
    disk_free_pct: float
    worker_seen_sec_ago: int | None


def disk_free_pct(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return round(usage.free / usage.total * 100, 1) if usage.total else 0.0


def disk_free_pct_safe(path: Path) -> float:
    try:
        return disk_free_pct(path)
    except OSError as exc:
        log.warning("healthz: диск не читается: %s", exc)
        return -1.0


def db_alive(conn: sqlite3.Connection) -> bool:
    """Настоящая таблица, а не SELECT 1: пустая или битая база отвечает False."""
    try:
        return conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] >= 1
    except sqlite3.Error as exc:
        log.warning("healthz: база недоступна: %s", exc)
        return False


def worker_age_sec(conn: sqlite3.Connection) -> tuple[int | None, bool]:
    """(секунд с последнего пульса или None, если записи нет; читается ли запись вообще)."""
    try:
        row = conn.execute("SELECT at FROM heartbeats WHERE name = 'worker'").fetchone()
        if row is None:
            return None, True
        return int((utcnow() - parse_iso(row["at"])).total_seconds()), True
    except (sqlite3.Error, TypeError, ValueError) as exc:
        log.warning("healthz: пульс воркера не читается: %s", exc)
        return None, False


@router.get("/healthz", response_model=Health)
def healthz(request: Request) -> Health:
    settings = request.app.state.settings
    free = disk_free_pct_safe(settings.data_dir)
    try:
        conn = connect(settings.db_path)
    except sqlite3.Error as exc:
        log.warning("healthz: база не открывается: %s", exc)
        return Health(status="degraded", db=False, disk_free_pct=free, worker_seen_sec_ago=None)
    try:
        db_ok = db_alive(conn)
        worker_age, worker_ok = worker_age_sec(conn) if db_ok else (None, False)
    finally:
        conn.close()
    stale = worker_age is not None and worker_age > WORKER_STALE_AFTER_SEC
    degraded = (not db_ok) or (not worker_ok) or free < DISK_LOW_PCT or stale
    return Health(
        status="degraded" if degraded else "ok",
        db=db_ok,
        disk_free_pct=free,
        worker_seen_sec_ago=worker_age,
    )


router.add_api_route("/healthz", healthz, methods=["HEAD"], include_in_schema=False)
