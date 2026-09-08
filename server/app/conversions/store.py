"""Готовые конверсии: строки после успеха воркера, вытеснение старших сверх пула."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from server.app.storage import conversion_url
from server.db.core import transaction

MAX_READY = 10

_SELECT = (
    "SELECT conversions.*, assets.original_name FROM conversions "
    "JOIN assets ON assets.id = conversions.asset_id "
)


def _row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "asset_id": row["asset_id"],
        "format": row["format"],
        "size": row["size"],
        "duration": row["duration"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "original_name": row["original_name"],
        "download": conversion_url(row["user_id"], row["asset_id"], row["id"], row["format"]),
    }


def list_conversions(conn: sqlite3.Connection, user_id: str, asset_id: str) -> list[dict]:
    rows = conn.execute(
        _SELECT + "WHERE conversions.asset_id = ? AND conversions.user_id = ? "
        "ORDER BY conversions.created_at DESC, conversions.id",
        (asset_id, user_id),
    )
    return [_row(r) for r in rows]


def list_conversions_for_user(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = conn.execute(
        _SELECT + "WHERE conversions.user_id = ? ORDER BY conversions.created_at DESC, conversions.id",
        (user_id,),
    )
    return [_row(r) for r in rows]


def get_conversion(conn: sqlite3.Connection, user_id: str, conversion_id: str) -> dict | None:
    row = conn.execute(
        _SELECT + "WHERE conversions.id = ? AND conversions.user_id = ?",
        (conversion_id, user_id),
    ).fetchone()
    return _row(row) if row else None


def delete_conversion(conn: sqlite3.Connection, user_id: str, conversion_id: str) -> bool:
    row = conn.execute(
        "SELECT path FROM conversions WHERE id = ? AND user_id = ?", (conversion_id, user_id)
    ).fetchone()
    if row is None:
        return False
    with transaction(conn):
        conn.execute("DELETE FROM conversions WHERE id = ? AND user_id = ?", (conversion_id, user_id))
    Path(row["path"]).unlink(missing_ok=True)
    return True


def evict_oldest(conn: sqlite3.Connection, asset_id: str, *, keep: int = MAX_READY - 1) -> None:
    """При новой конверсии готовых не больше 10: старше вытесняем, как снимки проекта."""
    rows = conn.execute(
        "SELECT id, path FROM conversions WHERE asset_id = ? AND id NOT IN "
        "(SELECT id FROM conversions WHERE asset_id = ? ORDER BY created_at DESC, id DESC LIMIT ?)",
        (asset_id, asset_id, keep),
    ).fetchall()
    if not rows:
        return
    ids = [row["id"] for row in rows]
    conn.execute(
        f"DELETE FROM conversions WHERE id IN ({','.join('?' * len(ids))})",
        ids,
    )
    for row in rows:
        Path(row["path"]).unlink(missing_ok=True)
