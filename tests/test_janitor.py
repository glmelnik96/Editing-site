import os
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.storage import asset_dir
from server.app.uploads.store import create_upload, finalize_file
from server.app.util import iso, now_iso, utcnow
from server.db.migrate import migrate
from server.janitor import rules
from server.janitor.__main__ import run

USER = "usr_000000000001"
# Настоящее «сейчас», а не константа: mtime свежесозданных папок должен быть моложе часа относительно NOW.
NOW = utcnow()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", chunk_size=1024, session_idle_days=7)


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = sqlite3.connect(str(settings.db_path), isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    migrate(c)
    c.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)", (USER, now_iso())
    )
    yield c
    c.close()


def _asset(conn, settings, tmp_path, name="a.mp4", last_access=None):
    src = tmp_path / f"src-{name}"
    src.write_bytes(b"x" * 10)
    row = finalize_file(conn, settings, user_id=USER, src=src, filename=name, size=10, kind="video")
    if last_access:
        conn.execute("UPDATE assets SET last_access_at = ? WHERE id = ?", (iso(last_access), row["id"]))
    return row["id"]


def _age(path: Path, hours: float) -> None:
    ts = (NOW - timedelta(hours=hours)).timestamp()
    os.utime(path, (ts, ts))


def test_expired_uploads_are_deleted_with_files(conn, settings):
    old = create_upload(conn, settings, USER, filename="old.mp4", size=100, kind=None)
    fresh = create_upload(conn, settings, USER, filename="new.mp4", size=100, kind=None)
    conn.execute(
        "UPDATE uploads SET expires_at = ? WHERE id = ?", (iso(NOW - timedelta(minutes=1)), old["id"])
    )
    assert rules.delete_expired_uploads(conn, NOW) == 1
    assert not Path(old["path"]).exists() and Path(fresh["path"]).exists()
    assert [r[0] for r in conn.execute("SELECT id FROM uploads")] == [fresh["id"]]


def test_expired_assets_are_deleted_and_jobs_canceled(conn, settings, tmp_path):
    old = _asset(conn, settings, tmp_path, "old.mp4", last_access=NOW - timedelta(hours=25))
    fresh = _asset(conn, settings, tmp_path, "new.mp4", last_access=NOW - timedelta(hours=23))
    assert rules.delete_expired_assets(conn, settings, NOW) == 1
    assert not asset_dir(settings, USER, old).exists() and asset_dir(settings, USER, fresh).exists()
    statuses = dict(conn.execute("SELECT target_id, status FROM jobs").fetchall())
    assert statuses == {old: "canceled", fresh: "queued"}


def test_asset_touched_during_pass_survives(conn, settings, tmp_path):
    """DELETE в delete_expired_assets переспрашивает last_access_at: ассет, который открыли
    (last_access_at продлился), пока janitor шёл по пачке, не должен удаляться.

    sqlite3.Connection в этом окружении (Python 3.14) - неизменяемый тип: monkeypatch.setattr
    на execute/backup (что на экземпляре, что на классе) падает с TypeError "cannot set ...
    attribute of immutable type". Подкласс Connection через параметр factory= - штатный
    механизм, им и подменяем момент выполнения DELETE, чтобы воспроизвести гонку."""
    old = _asset(conn, settings, tmp_path, "old.mp4", last_access=NOW - timedelta(hours=25))

    class TouchingConnection(sqlite3.Connection):
        def execute(self, sql, params=()):
            if sql.startswith("DELETE FROM assets"):
                super().execute("UPDATE assets SET last_access_at = ? WHERE id = ?", (iso(NOW), old))
            return super().execute(sql, params)

    touching = sqlite3.connect(str(settings.db_path), isolation_level=None, factory=TouchingConnection)
    touching.row_factory = sqlite3.Row
    try:
        assert rules.delete_expired_assets(touching, settings, NOW) == 0
    finally:
        touching.close()
    assert conn.execute("SELECT count(*) FROM assets").fetchone()[0] == 1
    assert asset_dir(settings, USER, old).exists()


def test_orphan_dirs_and_files_older_than_an_hour(conn, settings, tmp_path):
    kept = _asset(conn, settings, tmp_path)
    orphan_old = settings.data_dir / USER / "assets" / "ast_00000000dead"
    orphan_old.mkdir(parents=True)
    (orphan_old / "source.mp4").write_bytes(b"x")
    _age(orphan_old, 2)
    orphan_young = settings.data_dir / USER / "assets" / "ast_0000000young"
    orphan_young.mkdir()
    settings.uploads_tmp_path.mkdir(parents=True, exist_ok=True)
    stray = settings.uploads_tmp_path / "upl_000000000bad"
    stray.write_bytes(b"x")
    _age(stray, 2)
    live = create_upload(conn, settings, USER, filename="live.mp4", size=10, kind=None)
    _age(Path(live["path"]), 2)
    assert rules.delete_orphans(conn, settings, NOW) == 2
    assert not orphan_old.exists() and orphan_young.exists() and not stray.exists()
    assert asset_dir(settings, USER, kept).exists() and Path(live["path"]).exists()


def test_stale_running_jobs_requeue_once_then_fail(conn, settings, tmp_path):
    asset = _asset(conn, settings, tmp_path)
    first = conn.execute("SELECT id FROM jobs").fetchone()[0]
    stale = iso(NOW - timedelta(minutes=3))
    conn.execute(
        "UPDATE jobs SET status = 'running', attempts = 1, started_at = ?, heartbeat_at = ? WHERE id = ?",
        (stale, stale, first),
    )
    conn.execute("UPDATE assets SET status = 'analyzing' WHERE id = ?", (asset,))
    second = enqueue_job(conn, user_id=USER, type_="proxy", target_id=asset)
    conn.execute(
        "UPDATE jobs SET status = 'running', attempts = 2, started_at = ?, heartbeat_at = ? WHERE id = ?",
        (stale, stale, second),
    )
    alive = enqueue_job(conn, user_id=USER, type_="proxy", target_id=asset)
    conn.execute(
        "UPDATE jobs SET status = 'running', attempts = 1, started_at = ?, heartbeat_at = ? WHERE id = ?",
        (stale, iso(NOW - timedelta(seconds=30)), alive),
    )
    assert rules.requeue_stale_jobs(conn, NOW) == (1, 1)
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM jobs")}
    assert rows[first]["status"] == "queued" and rows[first]["heartbeat_at"] is None
    assert rows[second]["status"] == "failed" and "воркер" in rows[second]["error"]
    assert rows[alive]["status"] == "running"
    assert conn.execute("SELECT status FROM assets WHERE id = ?", (asset,)).fetchone()[0] == "analyzing"


def test_failed_analyze_marks_asset_failed(conn, settings, tmp_path):
    asset = _asset(conn, settings, tmp_path)
    job = conn.execute("SELECT id FROM jobs").fetchone()[0]
    stale = iso(NOW - timedelta(minutes=3))
    conn.execute(
        "UPDATE jobs SET status = 'running', attempts = 2, started_at = ?, heartbeat_at = ? WHERE id = ?",
        (stale, stale, job),
    )
    conn.execute("UPDATE assets SET status = 'analyzing' WHERE id = ?", (asset,))
    assert rules.requeue_stale_jobs(conn, NOW) == (0, 1)
    row = conn.execute("SELECT status, error FROM assets WHERE id = ?", (asset,)).fetchone()
    assert row["status"] == "failed" and row["error"]


def test_expired_sessions(conn, settings):
    def add(sid, last_seen, absolute):
        conn.execute(
            "INSERT INTO sessions (id, user_id, created_at, last_seen_at, absolute_expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, USER, iso(NOW - timedelta(days=40)), iso(last_seen), iso(absolute)),
        )

    add("s_idle", NOW - timedelta(days=8), NOW + timedelta(days=1))
    add("s_absolute", NOW - timedelta(hours=1), NOW - timedelta(minutes=1))
    add("s_live", NOW - timedelta(hours=1), NOW + timedelta(days=1))
    assert rules.delete_expired_sessions(conn, settings, NOW) == 2
    assert [r[0] for r in conn.execute("SELECT id FROM sessions")] == ["s_live"]


def test_backup_once_a_day_keeps_seven(conn, settings):
    backups = settings.data_dir / "backups"
    backups.mkdir()
    for d in range(1, 10):
        (backups / f"video-202608{d:02d}.db").write_bytes(b"")
    made = rules.backup_if_due(settings, NOW)
    assert made == backups / f"video-{NOW:%Y%m%d}.db"
    check = sqlite3.connect(str(made))
    assert check.execute("SELECT count(*) FROM users").fetchone()[0] == 1
    check.close()
    assert rules.backup_if_due(settings, NOW) is None
    assert len(list(backups.glob("video-*.db"))) == 7
    assert made.exists() and not (backups / "video-20260801.db").exists()


def test_backup_leaves_no_partial_file_when_copy_fails(conn, settings, monkeypatch):
    """Копия пишется во временный .part и переименовывается после успеха: при сбое sqlite backup
    API не должно остаться video-*.db, который сошёл бы за сегодняшний бэкап.

    sqlite3.Connection.backup нельзя подменить monkeypatch'ем (неизменяемый тип, см. комментарий
    в test_asset_touched_during_pass_survives) - подменяем rules.connect на фабрику подкласса,
    чей backup() всегда падает."""

    class BoomConnection(sqlite3.Connection):
        def backup(self, *args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")

    def fake_connect(path):
        return sqlite3.connect(str(path), isolation_level=None, factory=BoomConnection)

    monkeypatch.setattr(rules, "connect", fake_connect)
    with pytest.raises(sqlite3.OperationalError):
        rules.backup_if_due(settings, NOW)
    backups = settings.data_dir / "backups"
    assert list(backups.glob("video-*.db")) == []


def test_run_returns_stats(conn, settings):
    conn.close()
    stats = run(settings, NOW)
    assert stats == {
        "uploads_expired": 0,
        "assets_expired": 0,
        "renders_expired": 0,
        "orphans": 0,
        "sessions_expired": 0,
        "jobs_requeued": 0,
        "jobs_failed": 0,
        "error": 0,
        "backup": 1,
    }


def test_run_backs_up_even_when_a_rule_fails(conn, settings, monkeypatch):
    conn.close()

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(rules, "delete_expired_uploads", boom)
    stats = run(settings, NOW)
    assert stats["error"] == 1 and stats["backup"] == 1


def test_asset_used_by_a_draft_project_survives_its_ttl(conn, settings, tmp_path):
    """Проект может лежать нетронутым неделю: его файлы не исчезают по сроку обращений."""
    from server.app.projects.store import create_project

    asset = _asset(conn, settings, tmp_path, "used.mp4", last_access=NOW - timedelta(hours=30))
    conn.execute("UPDATE assets SET status = 'ready', duration = 30 WHERE id = ?", (asset,))
    project = create_project(
        conn, settings, USER, name="Черновик",
        raw_doc={"clips": [{"asset_id": asset, "in": 0, "out": 5}]},
    )
    conn.execute("UPDATE assets SET last_access_at = ? WHERE id = ?", (iso(NOW - timedelta(hours=30)), asset))
    assert rules.delete_expired_assets(conn, settings, NOW) == 0
    assert asset_dir(settings, USER, asset).exists()

    conn.execute("UPDATE projects SET status = 'finished' WHERE id = ?", (project["id"],))
    assert rules.delete_expired_assets(conn, settings, NOW) == 1
    assert not asset_dir(settings, USER, asset).exists()


def _render(conn, settings, expires_at, render_id="rnd_000000000001"):
    """Проект с роликом на диске: строка в renders плюс файл, срок которого мы задаём."""
    from server.app.storage import render_dir

    project_id = f"prj_{render_id[4:]}"
    conn.execute(
        "INSERT INTO projects (id, user_id, name, doc, created_at, updated_at) "
        "VALUES (?, ?, 'Ролик', '{}', ?, ?)",
        (project_id, USER, now_iso(), now_iso()),
    )
    folder = render_dir(settings, USER, project_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{render_id}.mp4"
    path.write_bytes(b"x" * 10)
    conn.execute(
        "INSERT INTO renders (id, project_id, user_id, job_id, quality, path, size, duration, "
        "created_at, expires_at) VALUES (?, ?, ?, 'job_1', 'draft', ?, 10, 5, ?, ?)",
        (render_id, project_id, USER, str(path), now_iso(), iso(expires_at)),
    )
    return path


def test_expired_renders_are_deleted_with_files(conn, settings):
    old = _render(conn, settings, NOW - timedelta(minutes=1), "rnd_000000000001")
    fresh = _render(conn, settings, NOW + timedelta(hours=1), "rnd_000000000002")
    assert rules.delete_expired_renders(conn, NOW) == 1
    assert not old.exists() and fresh.exists()
    assert [r[0] for r in conn.execute("SELECT id FROM renders")] == ["rnd_000000000002"]
    # Каталог проекта остаётся: в нём лежит живой ролик.
    assert fresh.parent.is_dir()


def test_expired_render_deleted_by_someone_else_leaves_no_trace(conn, settings):
    """Проект успели завершить между выборкой и удалением: строки нет, файл не наш — не трогаем."""
    path = _render(conn, settings, NOW - timedelta(minutes=1))
    conn.execute("DELETE FROM renders")
    assert rules.delete_expired_renders(conn, NOW) == 0
    assert path.exists()
