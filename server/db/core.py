"""Подключение к SQLite. Autocommit (isolation_level=None): транзакции явные, где нужны.

journal_mode=WAL хранится в самом файле базы и включается один раз в migrate();
здесь только прагмы, действующие на одно соединение.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import Request


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """Зависимость FastAPI: соединение на запрос; незавершённая транзакция откатывается."""
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE … COMMIT; при исключении ROLLBACK. Соединение в autocommit, поэтому явно."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
