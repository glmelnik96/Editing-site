import sqlite3

from server.db.core import connect
from server.db.migrate import migrate


def test_migrate_creates_tables_and_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        assert migrate(conn) == [1]
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"users", "whitelist", "sessions", "api_tokens", "heartbeats", "schema_migrations"} <= names
        assert migrate(conn) == []
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_foreign_keys_are_enforced(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        migrate(conn)
        try:
            conn.execute(
                "INSERT INTO sessions (id, user_id, created_at, last_seen_at, absolute_expires_at, user_agent) "
                "VALUES ('s', 'no_such_user', 'x', 'x', 'x', '')"
            )
            raise AssertionError("expected IntegrityError")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()
