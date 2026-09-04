import sqlite3

import pytest

from server.app.jobs import LANES, cancel_jobs_for_target, enqueue_job
from server.app.util import now_iso
from server.db.migrate import migrate


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"), isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
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
