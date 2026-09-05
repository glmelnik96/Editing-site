import threading

import pytest

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate
from server.media.run import MediaError
from server.worker import __main__ as worker_main

USER = "usr_00000000000a"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", worker_poll_sec=0.1)


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = connect(settings.db_path)
    migrate(c)
    c.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)",
        (USER, now_iso()),
    )
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _reset_stopping():
    """STOPPING — модульный Event: не сбросить его после теста — сломать соседние тесты файла."""
    yield
    worker_main.STOPPING.clear()


def test_run_once_takes_a_job_and_marks_it_done(conn, settings, monkeypatch):
    job_id = enqueue_job(conn, user_id=USER, type_="analyze", target_id="ast_1", priority=10)
    seen = []
    monkeypatch.setitem(worker_main.HANDLERS, "analyze", lambda c, s, job: seen.append(job["id"]))
    assert worker_main.run_once(conn, settings) is True
    assert seen == [job_id]
    row = conn.execute("SELECT status, progress, finished_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "done" and row["progress"] == 1.0 and row["finished_at"]


def test_run_once_on_empty_queue(conn, settings):
    assert worker_main.run_once(conn, settings) is False


def test_failure_marks_the_job_failed_and_keeps_the_loop_alive(conn, settings, monkeypatch):
    job_id = enqueue_job(conn, user_id=USER, type_="analyze", target_id="ast_1")

    def boom(c, s, job):
        raise MediaError("tool_failed", "ffmpeg упал", "хвост stderr")

    monkeypatch.setitem(worker_main.HANDLERS, "analyze", boom)
    assert worker_main.run_once(conn, settings) is True
    row = conn.execute("SELECT status, error FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "failed" and "ffmpeg упал" in row["error"] and "stderr" in row["error"]


def test_unexpected_error_also_fails_the_job(conn, settings, monkeypatch):
    job_id = enqueue_job(conn, user_id=USER, type_="analyze", target_id="ast_1")

    def boom(c, s, job):
        raise ZeroDivisionError("делить на ноль")

    monkeypatch.setitem(worker_main.HANDLERS, "analyze", boom)
    assert worker_main.run_once(conn, settings) is True
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "failed"


def test_canceled_job_is_left_canceled(conn, settings, monkeypatch):
    job_id = enqueue_job(conn, user_id=USER, type_="proxy", target_id="ast_1")

    def cancel_midway(c, s, job):
        c.execute("UPDATE jobs SET status = 'canceled' WHERE id = ?", (job["id"],))
        raise MediaError("canceled", "Отменено")

    monkeypatch.setitem(worker_main.HANDLERS, "proxy", cancel_midway)
    worker_main.run_once(conn, settings)
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "canceled"


def test_unknown_job_type_fails_loudly(conn, settings, monkeypatch):
    """Задание без обработчика падает с внятной ошибкой, а не молча теряется.

    Все типы полосы cpu обработчики теперь имеют, поэтому пропажу изображаем подменой набора:
    так же выглядел бы старый воркер, встретивший задание нового типа после отката версии.
    """
    job_id = enqueue_job(conn, user_id=USER, type_="render", target_id="prj_1")
    monkeypatch.delitem(worker_main.HANDLERS, "render")
    worker_main.run_once(conn, settings)
    row = conn.execute("SELECT status, error FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "failed" and "render" in row["error"]


def test_heartbeat_thread_updates_the_row(conn, settings):
    job_id = enqueue_job(conn, user_id=USER, type_="analyze", target_id="ast_1")
    stop = threading.Event()
    beat = worker_main.Heartbeat(settings, job_id=job_id, interval=0.05)
    beat.start()
    try:
        assert beat.wait_for_first(timeout=5) is True
    finally:
        beat.stop()
        stop.set()
    row = conn.execute("SELECT heartbeat_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["heartbeat_at"] is not None
    assert conn.execute("SELECT at FROM heartbeats WHERE name = 'worker'").fetchone() is not None


def test_orphaned_running_jobs_return_to_the_queue(conn, settings):
    job_id = enqueue_job(conn, user_id=USER, type_="analyze", target_id="ast_1")
    conn.execute("UPDATE jobs SET status = 'running', worker_pid = 999 WHERE id = ?", (job_id,))
    assert worker_main.requeue_orphans(conn) == 1
    row = conn.execute("SELECT status, worker_pid FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "queued" and row["worker_pid"] is None


def test_stop_during_a_job_requeues_it(conn, settings, monkeypatch):
    job_id = enqueue_job(conn, user_id=USER, type_="proxy", target_id="ast_1")

    def stop_midway(c, s, job):
        worker_main.STOPPING.set()
        raise MediaError("canceled", "Отменено")

    monkeypatch.setitem(worker_main.HANDLERS, "proxy", stop_midway)
    try:
        worker_main.run_once(conn, settings)
    finally:
        worker_main.STOPPING.clear()
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "queued"


def test_heartbeat_stops_without_breaking_thread_join(conn, settings):
    """Поле _stop перекрыло бы Thread._stop, и join() падал бы TypeError после каждого задания."""
    job_id = enqueue_job(conn, user_id=USER, type_="analyze", target_id="ast_1")
    beat = worker_main.Heartbeat(settings, job_id=job_id, interval=0.01)
    beat.start()
    assert beat.wait_for_first(timeout=5) is True
    beat.stop()
    assert beat.is_alive() is False
