import pytest

from server.app.jobs import enqueue_job
from server.app.util import iso, now_iso, utcnow
from server.db.core import connect
from server.db.migrate import migrate
from server.worker.queue import (
    claim_job,
    fail_job,
    finish_job,
    heartbeat,
    is_canceled,
    set_progress,
    write_worker_heartbeat,
)

A = "usr_00000000000a"
B = "usr_00000000000b"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    for uid in (A, B):
        c.execute(
            "INSERT INTO users (id, email, name, created_at) VALUES (?, ?, 'U', ?)",
            (uid, f"{uid}@ya.ru", now_iso()),
        )
    yield c
    c.close()


def test_claim_takes_priority_then_age(conn):
    old_render = enqueue_job(conn, user_id=A, type_="render", target_id="prj_1")
    analyze = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1", priority=10)
    job = claim_job(conn, lane="cpu", pid=42)
    assert job["id"] == analyze
    assert job["status"] == "running" and job["worker_pid"] == 42
    assert job["attempts"] == 1 and job["started_at"] and job["heartbeat_at"]
    finish_job(conn, analyze)
    assert claim_job(conn, lane="cpu", pid=42)["id"] == old_render


def test_claim_returns_none_when_queue_is_empty_or_other_lane(conn):
    assert claim_job(conn, lane="cpu", pid=1) is None
    enqueue_job(conn, user_id=A, type_="transcribe", target_id="ast_1")
    assert claim_job(conn, lane="cpu", pid=1) is None
    assert claim_job(conn, lane="net", pid=1)["type"] == "transcribe"


def test_claim_rotates_between_users(conn):
    """У одного человека очередь из трёх, у второго одно задание: второй не ждёт всю очередь."""
    first = enqueue_job(conn, user_id=A, type_="proxy", target_id="ast_1")
    enqueue_job(conn, user_id=A, type_="proxy", target_id="ast_2")
    enqueue_job(conn, user_id=A, type_="proxy", target_id="ast_3")
    other = enqueue_job(conn, user_id=B, type_="proxy", target_id="ast_4")
    got = claim_job(conn, lane="cpu", pid=1)
    assert got["id"] == first
    finish_job(conn, first)
    assert claim_job(conn, lane="cpu", pid=1)["id"] == other


def test_two_workers_do_not_take_the_same_job(conn, tmp_path):
    job_id = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1")
    other = connect(tmp_path / "t.db")
    try:
        first = claim_job(conn, lane="cpu", pid=1)
        second = claim_job(other, lane="cpu", pid=2)
    finally:
        other.close()
    assert first["id"] == job_id and second is None


def test_heartbeat_and_cancel(conn):
    job_id = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1")
    claim_job(conn, lane="cpu", pid=7)
    stale = iso(utcnow().replace(year=2020))
    conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (stale, job_id))
    heartbeat(conn, job_id)
    assert conn.execute("SELECT heartbeat_at FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] > stale
    assert is_canceled(conn, job_id) is False
    conn.execute("UPDATE jobs SET status = 'canceled' WHERE id = ?", (job_id,))
    assert is_canceled(conn, job_id) is True
    assert is_canceled(conn, "job_missing") is True  # задание исчезло — работать дальше незачем


def test_progress_is_clamped_and_rounded(conn):
    job_id = enqueue_job(conn, user_id=A, type_="proxy", target_id="ast_1")
    claim_job(conn, lane="cpu", pid=1)
    set_progress(conn, job_id, 0.4567)
    assert conn.execute("SELECT progress FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == 0.457
    set_progress(conn, job_id, 5.0)
    assert conn.execute("SELECT progress FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == 1.0
    set_progress(conn, job_id, -1.0)
    assert conn.execute("SELECT progress FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == 0.0


def test_finish_and_fail_are_final(conn):
    job_id = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1")
    claim_job(conn, lane="cpu", pid=1)
    fail_job(conn, job_id, "битый файл")
    row = conn.execute(
        "SELECT status, error, finished_at, progress FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["status"] == "failed" and row["error"] == "битый файл" and row["finished_at"]
    finish_job(conn, job_id)  # уже завершено: не воскрешаем
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "failed"


def test_fail_trims_a_long_message(conn):
    job_id = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1")
    claim_job(conn, lane="cpu", pid=1)
    fail_job(conn, job_id, "х" * 5000)
    stored = conn.execute("SELECT error FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert len(stored) <= 2000


def test_worker_heartbeat_row(conn):
    write_worker_heartbeat(conn)
    first = conn.execute("SELECT at FROM heartbeats WHERE name = 'worker'").fetchone()[0]
    write_worker_heartbeat(conn)
    assert conn.execute("SELECT at FROM heartbeats WHERE name = 'worker'").fetchone()[0] >= first
    assert conn.execute("SELECT count(*) FROM heartbeats").fetchone()[0] == 1


def test_claim_ignores_a_job_whose_target_is_gone(conn):
    """Ассет удалили, а задание осталось queued: janitor его отменит, воркер не должен за него браться."""
    job_id = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1")
    conn.execute("UPDATE jobs SET status = 'canceled' WHERE id = ?", (job_id,))
    assert claim_job(conn, lane="cpu", pid=1) is None
