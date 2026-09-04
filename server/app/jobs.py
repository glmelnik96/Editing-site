"""Постановка заданий в таблицу jobs. Воркер (план M1b) забирает их атомарным UPDATE ... RETURNING."""
from __future__ import annotations

import json
import sqlite3

from server.app.util import new_id, now_iso

LANES = {"analyze": "cpu", "proxy": "cpu", "render": "cpu", "transcribe": "net"}


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
