"""GET /healthz: база, свободный диск, пульс воркера. Без авторизации и без подробностей."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from server.app.util import parse_iso, utcnow
from server.db.core import get_db

router = APIRouter(tags=["health"])

WORKER_STALE_AFTER_SEC = 120
DISK_LOW_PCT = 10.0


def disk_free_pct(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return round(usage.free / usage.total * 100, 1)


@router.get("/healthz")
def healthz(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> dict:  # noqa: B008
    settings = request.app.state.settings
    db_ok = conn.execute("SELECT 1").fetchone()[0] == 1
    free = disk_free_pct(settings.data_dir)
    row = conn.execute("SELECT at FROM heartbeats WHERE name = 'worker'").fetchone()
    worker_age = int((utcnow() - parse_iso(row["at"])).total_seconds()) if row else None
    degraded = (not db_ok) or free < DISK_LOW_PCT or (worker_age is not None and worker_age > WORKER_STALE_AFTER_SEC)
    return {
        "status": "degraded" if degraded else "ok",
        "db": db_ok,
        "disk_free_pct": free,
        "worker_seen_sec_ago": worker_age,
    }
