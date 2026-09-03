"""Миграции: файлы server/db/migrations/NNNN_name.sql применяются по возрастанию номера, каждая в своей транзакции.

Первый вызов включает WAL (режим хранится в файле базы). Транзакция открывается внутри скрипта
(BEGIN IMMEDIATE), и первым statement'ом в ней идёт запись номера в schema_migrations: если два процесса
стартуют одновременно (API и воркер), второй после ожидания блокировки получает конфликт первичного ключа,
откатывается и пропускает миграцию. Любая другая ошибка откатывает миграцию целиком, номер не записывается.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def discover() -> list[tuple[int, Path]]:
    """Все файлы миграций по возрастанию номера; плохое имя или дубликат номера — ошибка."""
    found: dict[int, Path] = {}
    for path in MIGRATIONS_DIR.glob("*.sql"):
        m = re.match(r"^(\d+)_", path.name)
        if not m:
            raise ValueError(f"bad migration file name: {path.name}")
        version = int(m.group(1))
        if version in found:
            raise ValueError(f"duplicate migration version {version}: {found[version].name} and {path.name}")
        found[version] = path
    return sorted(found.items())


def pending(conn: sqlite3.Connection) -> list[tuple[int, Path]]:
    done = applied_versions(conn)
    return [(version, path) for version, path in discover() if version not in done]


def _script(version: int, sql: str) -> str:
    body = sql.strip()
    if body and not body.endswith(";"):
        body += ";"
    return (
        "BEGIN IMMEDIATE;\n"
        f"INSERT INTO schema_migrations (version, applied_at) "
        f"VALUES ({version}, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));\n"
        f"{body}\n"
        "COMMIT;"
    )


def migrate(conn: sqlite3.Connection) -> list[int]:
    conn.execute("PRAGMA journal_mode=WAL")
    applied: list[int] = []
    for version, path in pending(conn):
        try:
            conn.executescript(_script(version, path.read_text(encoding="utf-8-sig")))
        except sqlite3.IntegrityError:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            if version in applied_versions(conn):
                continue  # применил другой процесс, пока мы ждали блокировку
            raise
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


def main() -> None:
    from server.app.config import Settings
    from server.db.core import connect

    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.db_path)
    try:
        applied = migrate(conn)
    finally:
        conn.close()
    print("migrations applied:", ", ".join(str(v) for v in applied) or "none")


if __name__ == "__main__":
    main()
