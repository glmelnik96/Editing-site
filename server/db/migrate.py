"""Миграции: файлы server/db/migrations/NNNN_name.sql применяются по возрастанию номера, каждый в транзакции."""
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


def pending(conn: sqlite3.Connection) -> list[tuple[int, Path]]:
    done = applied_versions(conn)
    out: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = re.match(r"^(\d+)_", path.name)
        if not m:
            raise ValueError(f"bad migration file name: {path.name}")
        version = int(m.group(1))
        if version not in done:
            out.append((version, path))
    return out


def migrate(conn: sqlite3.Connection) -> list[int]:
    applied: list[int] = []
    for version, path in pending(conn):
        sql = path.read_text(encoding="utf-8")
        conn.executescript(
            "BEGIN;\n"
            f"{sql}\n"
            f"INSERT INTO schema_migrations (version, applied_at) VALUES ({version}, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));\n"
            "COMMIT;"
        )
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
    print("migrations applied:", applied or "none")


if __name__ == "__main__":
    main()
