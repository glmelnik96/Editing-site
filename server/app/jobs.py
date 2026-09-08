"""Постановка заданий в таблицу jobs. Воркер (план M1b) забирает их атомарным UPDATE ... RETURNING."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from server.app.util import iso, new_id, now_iso

LANES = {"analyze": "cpu", "proxy": "cpu", "render": "cpu", "convert": "cpu", "transcribe": "net"}
RECENT_SEC = 30
LIST_LIMIT = 50
ASSET_JOBS = ("analyze", "proxy", "transcribe", "convert")


def job_cancelable(type_: str, status: str) -> bool:
    return type_ in ("transcribe", "render", "convert") and status in ("queued", "running")


def job_label(type_: str, asset_name: str | None, project_name: str | None) -> str:
    if type_ in ASSET_JOBS:
        return asset_name or "запись удалена"
    if type_ == "render":
        return project_name or "проект удалён"
    return asset_name or project_name or ""


def list_jobs_for_user(conn: sqlite3.Connection, user_id: str, *, now: datetime) -> list[dict]:
    cutoff = iso(now - timedelta(seconds=RECENT_SEC))
    rows = conn.execute(
        """
        SELECT jobs.id, jobs.type, jobs.status, jobs.progress, jobs.error, jobs.created_at,
               jobs.finished_at, jobs.params, jobs.target_id,
               assets.original_name AS asset_name, projects.name AS project_name
        FROM jobs
        LEFT JOIN assets
          ON assets.id = jobs.target_id AND jobs.type IN ('analyze', 'proxy', 'transcribe', 'convert')
        LEFT JOIN projects
          ON projects.id = jobs.target_id AND jobs.type = 'render'
        WHERE jobs.user_id = ?
          AND (
            jobs.status IN ('queued', 'running')
            OR (
              jobs.status IN ('done', 'canceled', 'failed')
              AND jobs.finished_at IS NOT NULL
              AND jobs.finished_at >= ?
            )
          )
        ORDER BY jobs.created_at DESC
        LIMIT ?
        """,
        (user_id, cutoff, LIST_LIMIT),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        params = json.loads(row["params"] or "{}")
        quality = params.get("quality") if row["type"] == "render" else None
        out.append(
            {
                "id": row["id"],
                "type": row["type"],
                "status": row["status"],
                "progress": row["progress"],
                "error": row["error"],
                "created_at": row["created_at"],
                "finished_at": row["finished_at"],
                "label": job_label(row["type"], row["asset_name"], row["project_name"]),
                "cancelable": job_cancelable(row["type"], row["status"]),
                "quality": quality if quality in ("draft", "final") else None,
            }
        )
    return out


def enqueue_job(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    type_: str,
    target_id: str,
    priority: int = 0,
    params: dict | None = None,
) -> str:
    """Вставляет задание в статусе queued и возвращает его id. Транзакцию открывает вызывающий, если нужна."""
    if type_ not in LANES:
        raise ValueError(f"неизвестный тип задания: {type_}")
    job_id = new_id("job")
    conn.execute(
        "INSERT INTO jobs (id, user_id, type, lane, status, priority, target_id, params, created_at) "
        "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
        (job_id, user_id, type_, LANES[type_], priority, target_id, json.dumps(params or {}), now_iso()),
    )
    return job_id


def cancel_jobs_for_target(conn: sqlite3.Connection, target_id: str) -> int:
    """Отменяет незавершённые задания цели (ассета, проекта). Выполняющееся задание воркер прервёт сам,
    увидев статус canceled при следующем пульсе (M1b).

    target_id должен быть уже проверен на владение вызывающим: функция намеренно не фильтрует
    по пользователю, её вызывает и janitor, у которого владельца нет."""
    cur = conn.execute(
        "UPDATE jobs SET status = 'canceled', finished_at = ? "
        "WHERE target_id = ? AND status IN ('queued', 'running')",
        (now_iso(), target_id),
    )
    return cur.rowcount
