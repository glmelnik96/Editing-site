import sqlite3

import pytest

from server.app.jobs import LANES, cancel_jobs_for_target, enqueue_job
from server.app.util import now_iso
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
