"""Миграции: файлы server/db/migrations/NNNN_name.sql применяются по возрастанию номера,
каждая в своей транзакции.

Первый вызов включает WAL (режим хранится в файле базы). Транзакция открывается внутри скрипта
(BEGIN IMMEDIATE), и первым statement'ом в ней идёт запись номера в schema_migrations: если два процесса
стартуют одновременно (API и воркер), второй после ожидания блокировки получает конфликт первичного ключа,
откатывается и пропускает миграцию. Любая другая ошибка откатывает миграцию целиком, номер не записывается.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
WAL_ATTEMPTS = 10
WAL_RETRY_STEP_SEC = 0.05

# Пометка в первой строке миграции: выполнить её с выключенными внешними ключами.
#
# Нужна там, где пересоздают таблицу, на которую ссылаются с ON DELETE CASCADE. DROP TABLE
# родителя при включённых ключах делает неявный DELETE FROM и уносит детей: перестройка assets
# ради нового CHECK стёрла бы все расшифровки и конвертации (проверено на SQLite 3.50).
# Внутри транзакции PRAGMA foreign_keys молча не действует, поэтому переключаем её снаружи.
NO_FK_MARK = "-- foreign_keys: off"


def enable_wal(conn: sqlite3.Connection) -> None:
    """Включить WAL на файле базы, переживая одновременный старт двух процессов.

    Смена режима журнала требует короткой эксклюзивной блокировки, а busy-handler на неё не действует:
    проигравший получает «database is locked» сразу, без ожидания. Поэтому несколько попыток с паузой,
    а если режим тем временем включил другой процесс, этого достаточно.
    """
    for attempt in range(WAL_ATTEMPTS):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
                return
            time.sleep(WAL_RETRY_STEP_SEC * (attempt + 1))
    conn.execute("PRAGMA journal_mode=WAL")


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
    enable_wal(conn)
    applied: list[int] = []
    for version, path in pending(conn):
        sql = path.read_text(encoding="utf-8-sig")
        keep_fk = not sql.lstrip().startswith(NO_FK_MARK)
        if not keep_fk:
            conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.executescript(_script(version, sql))
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
        finally:
            # Возвращаем ключи после отката: внутри транзакции PRAGMA не подействовала бы, и
            # соединение осталось бы без проверок до конца жизни процесса.
            if not keep_fk:
                conn.execute("PRAGMA foreign_keys=ON")
        if not keep_fk:
            _check_foreign_keys(conn, version)
        applied.append(version)
    return applied


def _check_foreign_keys(conn: sqlite3.Connection, version: int) -> None:
    """Не осталось ли висячих ссылок после перестройки таблицы.

    Проверка идёт уже после COMMIT: результат PRAGMA изнутри executescript не прочитать. Значит
    это не защита, а сигнал — увидев его, восстанавливают базу из резервной копии того же дня.
    Молчаливая порча была бы хуже: её заметили бы через неделю по пропавшим расшифровкам.
    """
    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        tables = sorted({str(row[0]) for row in broken})
        raise RuntimeError(
            f"миграция {version} оставила висячие ссылки в таблицах: {', '.join(tables)}; "
            "база нуждается в восстановлении из резервной копии"
        )


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
