import re
import sqlite3

import pytest

from server.db import migrate as migrate_mod
from server.db.core import connect
from server.db.migrate import discover, enable_wal, migrate

TABLES = {
    "users",
    "whitelist",
    "sessions",
    "api_tokens",
    "heartbeats",
    "schema_migrations",
    "uploads",
    "upload_chunks",
    "assets",
    "jobs",
    "projects",
    "project_versions",
    "renders",
    "transcripts",
}


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _migrations_dir(tmp_path, monkeypatch, files):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    for name, sql in files.items():
        (mdir / name).write_text(sql, encoding="utf-8")
    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", mdir)
    return mdir


def test_migrate_creates_tables_and_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        assert migrate(conn) == [1, 2, 3, 4, 5, 6, 7, 8]
        assert TABLES <= _tables(conn)
        assert migrate(conn) == []
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.in_transaction is False
    finally:
        conn.close()


def test_foreign_keys_are_enforced(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        migrate(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sessions (id, user_id, created_at, last_seen_at, absolute_expires_at, "
                "user_agent) VALUES ('s', 'no_such_user', 'x', 'x', 'x', '')"
            )
    finally:
        conn.close()


def test_emails_are_unique_case_insensitively(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        migrate(conn)
        conn.execute("INSERT INTO users (id, email, created_at) VALUES ('u1', 'A@ya.ru', 'x')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO users (id, email, created_at) VALUES ('u2', 'a@ya.ru', 'x')")
        conn.execute("INSERT INTO whitelist (email, added_at) VALUES ('B@ya.ru', 'x')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO whitelist (email, added_at) VALUES ('b@ya.ru', 'x')")
    finally:
        conn.close()


def test_applied_at_has_iso_millisecond_shape(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        migrate(conn)
        at = conn.execute("SELECT applied_at FROM schema_migrations WHERE version = 1").fetchone()[0]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", at)
    finally:
        conn.close()


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path, monkeypatch):
    _migrations_dir(
        tmp_path,
        monkeypatch,
        {
            "0001_ok.sql": "CREATE TABLE a (x INTEGER);",
            "0002_bad.sql": "CREATE TABLE b (x INTEGER);\nCREATE TABLE b (x INTEGER);",
        },
    )
    conn = connect(tmp_path / "t.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn)
        assert conn.in_transaction is False
        assert "a" in _tables(conn)
        assert "b" not in _tables(conn)
        assert [r[0] for r in conn.execute("SELECT version FROM schema_migrations")] == [1]
    finally:
        conn.close()


def test_discover_orders_by_version_and_rejects_bad_names_and_duplicates(tmp_path, monkeypatch):
    mdir = _migrations_dir(
        tmp_path, monkeypatch, {"0002_b.sql": "SELECT 1;", "10_j.sql": "SELECT 1;", "3_c.sql": "SELECT 1;"}
    )
    assert [version for version, _ in discover()] == [2, 3, 10]
    (mdir / "2_dup.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        discover()
    (mdir / "2_dup.sql").unlink()
    (mdir / "nope.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError, match="bad migration"):
        discover()


def test_enable_wal_retries_then_raises_while_locked_and_succeeds_after_release(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate_mod, "WAL_ATTEMPTS", 2)
    monkeypatch.setattr(migrate_mod, "WAL_RETRY_STEP_SEC", 0.01)
    holder = connect(tmp_path / "t.db")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("CREATE TABLE z (x INTEGER)")
    other = connect(tmp_path / "t.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            enable_wal(other)
        holder.execute("COMMIT")
        enable_wal(other)
        assert other.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        other.close()
        holder.close()


def test_enable_wal_accepts_mode_switched_by_another_process():
    class Row:
        def fetchone(self):
            return ("WAL",)

    class Stub:
        def __init__(self):
            self.calls = []

        def execute(self, sql):
            self.calls.append(sql)
            if sql == "PRAGMA journal_mode=WAL":
                raise sqlite3.OperationalError("database is locked")
            return Row()

    stub = Stub()
    enable_wal(stub)
    assert stub.calls == ["PRAGMA journal_mode=WAL", "PRAGMA journal_mode"]


def test_second_migration_upgrades_a_version_one_database(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    try:
        real_discover = migrate_mod.discover
        monkeypatch.setattr(migrate_mod, "discover", lambda: real_discover()[:1])
        assert migrate(conn) == [1]
        assert "yandex_id" not in {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        monkeypatch.setattr(migrate_mod, "discover", real_discover)
        assert migrate(conn) == [2, 3, 4, 5, 6, 7, 8]
        assert "yandex_id" in {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        conn.execute(
            "INSERT INTO users (id, email, created_at, yandex_id) VALUES ('u1', 'a@ya.ru', 'x', '42')"
        )
        conn.execute(
            "INSERT INTO users (id, email, created_at, yandex_id) VALUES ('u2', 'b@ya.ru', 'x', '42')"
        )
        conn.execute(
            "INSERT INTO users (id, email, created_at, yandex_id) VALUES ('u3', 'c@ya.ru', 'x', NULL)"
        )
        assert conn.execute("SELECT count(*) FROM users WHERE yandex_id = '42'").fetchone()[0] == 2
    finally:
        conn.close()


def test_transaction_rolls_back_on_error(tmp_path):
    from server.db.core import transaction

    conn = connect(tmp_path / "t.db")
    try:
        migrate(conn)
        with pytest.raises(RuntimeError), transaction(conn):
            conn.execute("INSERT INTO heartbeats (name, at) VALUES ('w', 'x')")
            raise RuntimeError("boom")
        assert conn.execute("SELECT count(*) FROM heartbeats").fetchone()[0] == 0
        assert conn.in_transaction is False
        with transaction(conn):
            conn.execute("INSERT INTO heartbeats (name, at) VALUES ('w', 'x')")
        assert conn.execute("SELECT count(*) FROM heartbeats").fetchone()[0] == 1
    finally:
        conn.close()
