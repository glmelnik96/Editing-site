"""Очередь заданий со стороны воркера: атомарный захват, пульс, отмена, финал.

Захват одним UPDATE … RETURNING: два процесса не возьмут одно задание даже без внешней блокировки
(SQLite ≥ 3.35). Порядок выбора — приоритет, затем справедливость между пользователями, затем возраст.
"""
from __future__ import annotations

import sqlite3

from server.app.util import now_iso

ERROR_MAX_CHARS = 2000

# Среди заданий одного приоритета первым идёт пользователь, чьё последнее задание закончилось раньше:
# один человек с длинной очередью не занимает воркер целиком (раздел 9.1 спеки).
_PICK_SQL = """
UPDATE jobs SET
    status = 'running',
    started_at = :now,
    heartbeat_at = :now,
    worker_pid = :pid,
    attempts = attempts + 1,
    progress = 0
WHERE id = (
    SELECT j.id FROM jobs AS j
    LEFT JOIN (
        SELECT user_id, max(coalesce(finished_at, started_at, created_at)) AS last_at
        FROM jobs WHERE status IN ('done', 'failed', 'running') GROUP BY user_id
    ) AS seen ON seen.user_id = j.user_id
    WHERE j.status = 'queued' AND j.lane = :lane
    ORDER BY j.priority DESC, coalesce(seen.last_at, ''), j.created_at
    LIMIT 1
)
RETURNING *
"""


def claim_job(conn: sqlite3.Connection, *, lane: str, pid: int) -> sqlite3.Row | None:
    row = conn.execute(_PICK_SQL, {"now": now_iso(), "pid": pid, "lane": lane}).fetchone()
    return row


def heartbeat(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (now_iso(), job_id))


def is_canceled(conn: sqlite3.Connection, job_id: str) -> bool:
    """True и когда задание отменили, и когда его строки уже нет: работать дальше незачем."""
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row is None or row["status"] != "running"


def set_progress(conn: sqlite3.Connection, job_id: str, value: float) -> None:
    clamped = round(min(1.0, max(0.0, value)), 3)
    conn.execute("UPDATE jobs SET progress = ? WHERE id = ? AND status = 'running'", (clamped, job_id))


def finish_job(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'done', progress = 1, finished_at = ? WHERE id = ? AND status = 'running'",
        (now_iso(), job_id),
    )


def fail_job(conn: sqlite3.Connection, job_id: str, error: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'failed', finished_at = ?, error = ? WHERE id = ? AND status = 'running'",
        (now_iso(), error[:ERROR_MAX_CHARS], job_id),
    )


def write_worker_heartbeat(conn: sqlite3.Connection) -> None:
    """Пульс процесса для /healthz: одна строка на воркер."""
    conn.execute(
        "INSERT INTO heartbeats (name, at) VALUES ('worker', ?) "
        "ON CONFLICT(name) DO UPDATE SET at = excluded.at",
        (now_iso(),),
    )
