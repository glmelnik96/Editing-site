"""GET /healthz: база, свободный диск, пульс воркера. Без авторизации; поля описаны в схеме ответа.

Любая поломка даёт статус degraded и 200, а не 500: внешний пинг и deploy.sh читают тело.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from server.app.util import parse_iso, utcnow
from server.db.core import get_db

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
    return round(usage.free / usage.total * 100, 1)


def db_alive(conn: sqlite3.Connection) -> bool:
    """Настоящая таблица, а не SELECT 1: пустая или битая база отвечает False."""
    try:
        return conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] >= 1
    except sqlite3.Error:
        log.exception("healthz: база недоступна")
        return False


def worker_age_sec(conn: sqlite3.Connection) -> tuple[int | None, bool]:
    """(секунд с последнего пульса или None, если записи нет; читается ли запись вообще)."""
    try:
        row = conn.execute("SELECT at FROM heartbeats WHERE name = 'worker'").fetchone()
        if row is None:
            return None, True
        return int((utcnow() - parse_iso(row["at"])).total_seconds()), True
    except (sqlite3.Error, TypeError, ValueError):
        log.exception("healthz: пульс воркера не читается")
        return None, False


@router.get("/healthz", response_model=Health)
def healthz(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Health:  # noqa: B008
    settings = request.app.state.settings
    db_ok = db_alive(conn)
    try:
        free = disk_free_pct(settings.data_dir)
    except OSError:
        log.exception("healthz: диск не читается")
        free = -1.0
    worker_age, worker_ok = worker_age_sec(conn)
    stale = worker_age is not None and worker_age > WORKER_STALE_AFTER_SEC
    degraded = (not db_ok) or (not worker_ok) or free < DISK_LOW_PCT or stale
    return Health(status="degraded" if degraded else "ok", db=db_ok, disk_free_pct=free, worker_seen_sec_ago=worker_age)
