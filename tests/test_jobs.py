import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from server.app.jobs import (
    LANES,
    LIST_LIMIT,
    RECENT_SEC,
    cancel_jobs_for_target,
    enqueue_job,
    job_cancelable,
    job_label,
    list_jobs_for_user,
)
from server.app.projects.store import active_renders
from server.app.util import iso, now_iso
from server.db.core import connect
from server.db.migrate import migrate


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    c.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES ('usr_000000000001', 'a@b.c', 'A', ?)",
        (now_iso(),),
    )
    yield c
    c.close()


def test_enqueue_sets_lane_and_defaults(conn):
    job_id = enqueue_job(conn, user_id="usr_000000000001", type_="analyze", target_id="ast_1", priority=10)
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job_id.startswith("job_")
    assert row["lane"] == "cpu" and row["status"] == "queued" and row["priority"] == 10
    assert row["params"] == "{}" and row["progress"] == 0 and row["attempts"] == 0
    assert LANES["transcribe"] == "net"
    assert LANES["convert"] == "cpu"


def test_convert_uses_cpu_lane_and_default_priority(conn):
    job_id = enqueue_job(conn, user_id="usr_000000000001", type_="convert", target_id="ast_1")
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["type"] == "convert" and row["lane"] == "cpu" and row["priority"] == 0


def test_active_renders_counts_convert_jobs_too(conn):
    """Лимит очереди общий: конвертация часового файла не должна отодвинуть сборку без правила."""
    enqueue_job(conn, user_id="usr_000000000001", type_="render", target_id="prj_1")
    enqueue_job(conn, user_id="usr_000000000001", type_="convert", target_id="ast_1")
    done = enqueue_job(conn, user_id="usr_000000000001", type_="convert", target_id="ast_2")
    conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (done,))
    enqueue_job(conn, user_id="usr_000000000001", type_="analyze", target_id="ast_3")
    assert active_renders(conn, "usr_000000000001") == 2


def test_enqueue_rejects_unknown_type(conn):
    with pytest.raises(ValueError):
        enqueue_job(conn, user_id="usr_000000000001", type_="explode", target_id="x")


def test_cancel_only_touches_open_jobs(conn):
    a = enqueue_job(conn, user_id="usr_000000000001", type_="analyze", target_id="ast_1")
    b = enqueue_job(conn, user_id="usr_000000000001", type_="proxy", target_id="ast_1")
    conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (b,))
    enqueue_job(conn, user_id="usr_000000000001", type_="analyze", target_id="ast_2")
    assert cancel_jobs_for_target(conn, "ast_1") == 1
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (a,)).fetchone()[0] == "canceled"
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (b,)).fetchone()[0] == "done"
    assert conn.execute("SELECT count(*) FROM jobs WHERE status = 'queued'").fetchone()[0] == 1


def test_schema_checks_reject_bad_values(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (id, user_id, type, lane, status, target_id, created_at) "
            "VALUES ('job_x', 'usr_000000000001', 'analyze', 'cpu', 'bogus', 't', ?)",
            (now_iso(),),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (id, user_id, type, lane, status, target_id, progress, created_at) "
            "VALUES ('job_y', 'usr_000000000001', 'analyze', 'cpu', 'queued', 't', 1.5, ?)",
            (now_iso(),),
        )


def test_deleting_user_cascades_to_jobs_uploads_assets(conn):
    enqueue_job(conn, user_id="usr_000000000001", type_="analyze", target_id="ast_1")
    conn.execute(
        "INSERT INTO uploads (id, user_id, filename, size, kind, chunk_size, path, created_at, expires_at) "
        "VALUES ('upl_1', 'usr_000000000001', 'a.mp4', 1, 'video', 1, '/x', ?, ?)",
        (now_iso(), now_iso()),
    )
    conn.execute("INSERT INTO upload_chunks (upload_id, idx) VALUES ('upl_1', 0)")
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, created_at, "
        "last_access_at) VALUES ('ast_1', 'usr_000000000001', 'video', 'a.mp4', 'mp4', 1, 'uploaded', ?, ?)",
        (now_iso(), now_iso()),
    )
    conn.execute("DELETE FROM users WHERE id = 'usr_000000000001'")
    for table in ("jobs", "uploads", "upload_chunks", "assets"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


def test_index_jobs_user_status_exists(conn):
    names = {row[1] for row in conn.execute("PRAGMA index_list(jobs)")}
    assert "jobs_user_status_idx" in names


def test_cancelable_only_live_transcribe_render_and_convert():
    assert job_cancelable("transcribe", "queued") is True
    assert job_cancelable("render", "running") is True
    assert job_cancelable("convert", "queued") is True
    assert job_cancelable("analyze", "running") is False
    assert job_cancelable("proxy", "queued") is False
    assert job_cancelable("render", "done") is False
    assert job_cancelable("convert", "done") is False


def test_label_falls_back_when_target_is_gone():
    assert job_label("analyze", None, None) == "запись удалена"
    assert job_label("render", None, None) == "проект удалён"
    assert job_label("transcribe", "Нарезка.mp4", None) == "Нарезка.mp4"
    assert job_label("convert", None, None) == "запись удалена"
    assert job_label("convert", "утренний.mp3", None) == "утренний.mp3"


def _stamp(conn, job_id, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))


def test_list_includes_open_jobs_and_recent_finished_only(conn):
    now = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    uid = "usr_000000000001"
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, "
        "created_at, last_access_at) "
        "VALUES ('ast_list1', ?, 'video', 'Нарезка.mp4', 'mp4', 1, 'proxy_ready', ?, ?)",
        (uid, now_iso(), now_iso()),
    )
    conn.execute(
        "INSERT INTO projects (id, user_id, name, status, version, doc, created_at, updated_at) "
        "VALUES ('prj_list1', ?, 'Ролик', 'draft', 1, '{}', ?, ?)",
        (uid, now_iso(), now_iso()),
    )
    conn.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES ('usr_otheruser01', 'o@b.c', 'O', ?)",
        (now_iso(),),
    )
    open_id = enqueue_job(conn, user_id=uid, type_="analyze", target_id="ast_list1")
    fresh = enqueue_job(conn, user_id=uid, type_="render", target_id="prj_list1", params={"quality": "draft"})
    stale = enqueue_job(conn, user_id=uid, type_="transcribe", target_id="ast_list1")
    _stamp(conn, fresh, status="done", finished_at=iso(now - timedelta(seconds=5)), progress=1)
    _stamp(conn, stale, status="done", finished_at=iso(now - timedelta(seconds=RECENT_SEC + 1)), progress=1)
    other = enqueue_job(conn, user_id="usr_otheruser01", type_="analyze", target_id="ast_x")
    rows = list_jobs_for_user(conn, uid, now=now)
    ids = [r["id"] for r in rows]
    assert open_id in ids and fresh in ids
    assert stale not in ids and other not in ids
    assert len(rows) <= LIST_LIMIT
    analyze = next(r for r in rows if r["id"] == open_id)
    assert analyze["label"] == "Нарезка.mp4" and analyze["cancelable"] is False
    render = next(r for r in rows if r["id"] == fresh)
    assert render["label"] == "Ролик" and render["quality"] == "draft" and render["cancelable"] is False
    converting = enqueue_job(conn, user_id=uid, type_="convert", target_id="ast_list1")
    conv = next(r for r in list_jobs_for_user(conn, uid, now=now) if r["id"] == converting)
    assert conv["label"] == "Нарезка.mp4" and conv["cancelable"] is True
    assert conv["target_id"] == "ast_list1"
