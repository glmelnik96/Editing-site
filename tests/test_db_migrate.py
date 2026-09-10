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
    "conversions",
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
        assert migrate(conn) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
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
        assert migrate(conn) == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
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


def test_jobs_rebuild_keeps_old_rows_and_accepts_convert(tmp_path, monkeypatch):
    """SQLite CHECK нельзя расширить на месте: таблица jobs пересоздаётся, старые строки остаются."""
    conn = connect(tmp_path / "t.db")
    try:
        real_discover = migrate_mod.discover
        monkeypatch.setattr(
            migrate_mod, "discover", lambda: [item for item in real_discover() if item[0] <= 8]
        )
        assert migrate(conn) == [1, 2, 3, 4, 5, 6, 7, 8]
        conn.execute("INSERT INTO users (id, email, created_at) VALUES ('usr_000000000001', 'a@ya.ru', 'x')")
        conn.execute(
            "INSERT INTO jobs (id, user_id, type, lane, status, target_id, created_at) "
            "VALUES ('job_oldrender01', 'usr_000000000001', 'render', 'cpu', 'queued', 'prj_1', 'x')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO jobs (id, user_id, type, lane, status, target_id, created_at) "
                "VALUES ('job_noconvert01', 'usr_000000000001', 'convert', 'cpu', 'queued', 'ast_1', 'x')"
            )
        monkeypatch.setattr(migrate_mod, "discover", real_discover)
        assert migrate(conn) == [9, 10, 11, 12, 13, 14]
        names = {row[1] for row in conn.execute("PRAGMA index_list(jobs)")}
        assert "jobs_user_status_idx" in names
        assert conn.execute("SELECT type FROM jobs WHERE id = 'job_oldrender01'").fetchone()[0] == "render"
        conn.execute(
            "INSERT INTO jobs (id, user_id, type, lane, status, target_id, created_at) "
            "VALUES ('job_convert0001', 'usr_000000000001', 'convert', 'cpu', 'queued', 'ast_1', 'x')"
        )
        assert conn.execute("SELECT type FROM jobs WHERE id = 'job_convert0001'").fetchone()[0] == "convert"
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'conversions'"
        ).fetchone()[0]
        assert "asset_id" in sql and "expires_at" in sql
    finally:
        conn.close()


def test_conversions_rebuild_keeps_old_rows_and_accepts_new_formats(tmp_path, monkeypatch):
    """CHECK format на conversions нельзя расширить ALTER: таблицу пересоздаём."""
    conn = connect(tmp_path / "t.db")
    try:
        real_discover = migrate_mod.discover
        monkeypatch.setattr(
            migrate_mod, "discover", lambda: [item for item in real_discover() if item[0] <= 10]
        )
        assert migrate(conn) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        conn.execute(
            "INSERT INTO users (id, email, created_at) VALUES ('usr_000000000001', 'a@ya.ru', 'x')"
        )
        conn.execute(
            "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status,"
            " created_at, last_access_at) VALUES"
            " ('ast_000000000001', 'usr_000000000001', 'video', 'a.mp4', 'mp4', 1,"
            " 'proxy_ready', 'x', 'x')"
        )
        conn.execute(
            "INSERT INTO conversions"
            " (id, user_id, asset_id, job_id, format, path, size, duration, created_at, expires_at)"
            " VALUES ('cnv_oldmp3xxxxx1', 'usr_000000000001', 'ast_000000000001',"
            " 'job_1', 'mp3', 'p', 1, 1, 'x', 'x')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO conversions"
                " (id, user_id, asset_id, job_id, format, path, size, duration,"
                " created_at, expires_at) VALUES ('cnv_aacblocked01',"
                " 'usr_000000000001', 'ast_000000000001', 'job_2', 'aac', 'p', 1, 1, 'x', 'x')"
            )
        monkeypatch.setattr(migrate_mod, "discover", real_discover)
        assert migrate(conn) == [11, 12, 13, 14]
        assert (
            conn.execute("SELECT format FROM conversions WHERE id = 'cnv_oldmp3xxxxx1'").fetchone()[0]
            == "mp3"
        )
        for fmt in ("aac", "flac", "ogg", "webm"):
            conn.execute(
                "INSERT INTO conversions "
                "(id, user_id, asset_id, job_id, format, path, size, duration, created_at, expires_at) "
                "VALUES (?, 'usr_000000000001', 'ast_000000000001', 'job_x', ?, 'p', 1, 1, 'x', 'x')",
                (f"cnv_{fmt}00000001", fmt),
            )
    finally:
        conn.close()


def test_projects_lose_the_finished_state_and_keep_their_versions(tmp_path):
    """Состояние «завершён» уходит вместе с колонками, а строки, что ссылались на проекты, живут.

    Пересобрать таблицу здесь нельзя: на projects ссылаются project_versions и renders, и
    DROP TABLE каскадом снёс бы их. Поэтому DROP COLUMN — и проверяем, что каскад не сработал.
    """
    conn = connect(tmp_path / "video.db")
    migrate(conn)
    conn.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES ('usr_1', 'a@b.c', 'A', 't')"
    )
    conn.execute(
        "INSERT INTO projects (id, user_id, name, version, doc, created_at, updated_at) "
        "VALUES ('prj_1', 'usr_1', 'Живой', 1, '{}', 't', 't')"
    )
    conn.execute(
        "INSERT INTO project_versions (id, project_id, user_id, version, name, doc, label, created_at) "
        "VALUES ('ver_1', 'prj_1', 'usr_1', 1, 'Живой', '{}', 'снимок', 't')"
    )
    columns = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    assert "status" not in columns and "finished_at" not in columns
    assert conn.execute("SELECT count(*) FROM project_versions").fetchone()[0] == 1
    conn.close()



def test_assets_rebuild_keeps_transcripts_and_conversions(tmp_path, monkeypatch):
    """Перестройка assets ради вида «картинка» не должна унести детей по каскаду.

    На assets ссылаются transcripts и conversions с ON DELETE CASCADE, а DROP TABLE родителя при
    включённых внешних ключах делает неявный DELETE FROM: расшифровки и готовые конвертации всех
    пользователей исчезли бы молча. Спасает пометка «-- foreign_keys: off» в первой строке
    миграции — без неё этот тест падает на нулях.
    """
    conn = connect(tmp_path / "t.db")
    try:
        real_discover = migrate_mod.discover
        monkeypatch.setattr(
            migrate_mod, "discover", lambda: [item for item in real_discover() if item[0] <= 12]
        )
        migrate(conn)
        conn.execute(
            "INSERT INTO users (id, email, created_at) VALUES ('usr_000000000001', 'a@ya.ru', 'x')"
        )
        conn.execute(
            "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status,"
            " duration, created_at, last_access_at) VALUES"
            " ('ast_000000000001', 'usr_000000000001', 'video', 'a.mp4', 'mp4', 700, 'ready',"
            " 70.0, 'x', 'x')"
        )
        conn.execute(
            "INSERT INTO transcripts (asset_id, user_id, provider, model, language, duration,"
            " segments, stats, created_at) VALUES ('ast_000000000001', 'usr_000000000001',"
            " 'whisper', 'large', 'ru', 70.0, 12, '{}', 'x')"
        )
        conn.execute(
            "INSERT INTO conversions"
            " (id, user_id, asset_id, job_id, format, path, size, duration, created_at, expires_at)"
            " VALUES ('cnv_oldmp3xxxxx1', 'usr_000000000001', 'ast_000000000001',"
            " 'job_1', 'mp3', 'p', 1, 1, 'x', 'x')"
        )

        monkeypatch.setattr(migrate_mod, "discover", real_discover)
        assert migrate(conn) == [13, 14]

        assert conn.execute("SELECT count(*) FROM transcripts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM conversions").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM assets").fetchone()[0] == 1
        # Ключи вернулись включёнными, иначе соединение доживёт без проверок до конца процесса.
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # Ради этого всё и затевалось: картинка проходит CHECK, а битрейт есть куда записать.
        conn.execute(
            "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status,"
            " bit_rate, created_at, last_access_at) VALUES"
            " ('ast_000000000002', 'usr_000000000001', 'image', 'z.jpg', 'jpg', 5, 'ready',"
            " NULL, 'x', 'x')"
        )
        conn.execute(
            "UPDATE assets SET bit_rate = 8000000 WHERE id = 'ast_000000000001'"
        )
        assert conn.execute(
            "SELECT bit_rate FROM assets WHERE id = 'ast_000000000001'"
        ).fetchone()[0] == 8_000_000
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status,"
                " created_at, last_access_at) VALUES"
                " ('ast_000000000003', 'usr_000000000001', 'глупость', 'z.bin', 'bin', 5,"
                " 'ready', 'x', 'x')"
            )
    finally:
        conn.close()
