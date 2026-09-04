"""Воркер: python -m server.worker. Один процесс, полоса cpu, одно задание за раз.

Пульс идёт из отдельного потока со своим соединением: пока ffmpeg кодирует час, janitor не должен
считать задание зависшим. Отмена и остановка сервиса доходят до ffmpeg через should_stop у run_streaming.
"""
from __future__ import annotations

import logging
import signal
import sqlite3
import threading
import time
from types import FrameType

from server.app.config import Settings
from server.app.main import configure_logging
from server.db.core import connect
from server.media.run import MediaError
from server.worker.handlers import HANDLERS
from server.worker.queue import (
    claim_job,
    fail_job,
    finish_job,
    heartbeat,
    write_worker_heartbeat,
)

log = logging.getLogger("video.worker")

LANE = "cpu"
HEARTBEAT_SEC = 10.0
IDLE_LOG_EVERY = 300  # раз в сколько пустых кругов писать, что воркер жив
_stopping = threading.Event()


class Heartbeat(threading.Thread):
    """Обновляет пульс задания и процесса, пока идёт работа. Своё соединение: чужое занято ffmpeg-циклом."""

    def __init__(self, settings: Settings, *, job_id: str, interval: float = HEARTBEAT_SEC) -> None:
        super().__init__(daemon=True)
        self.settings = settings
        self.job_id = job_id
        self.interval = interval
        self._stop = threading.Event()
        self._first = threading.Event()

    def run(self) -> None:
        conn = connect(self.settings.db_path)
        try:
            while not self._stop.is_set():
                try:
                    heartbeat(conn, self.job_id)
                    write_worker_heartbeat(conn)
                    self._first.set()
                except sqlite3.Error as exc:  # база занята — не повод ронять работу
                    log.warning("пульс не записался: %s", exc)
                self._stop.wait(self.interval)
        finally:
            conn.close()

    def wait_for_first(self, timeout: float) -> bool:
        return self._first.wait(timeout)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=5)


def run_once(conn: sqlite3.Connection, settings: Settings) -> bool:
    """Взять одно задание и выполнить. True, если работа была."""
    job = claim_job(conn, lane=LANE, pid=_pid())
    if job is None:
        return False
    log.info("взято задание %s (%s, %s)", job["id"], job["type"], job["target_id"])
    beat = Heartbeat(settings, job_id=job["id"])
    beat.start()
    try:
        handler = HANDLERS.get(job["type"])
        if handler is None:
            raise MediaError("unknown_job", f"нет обработчика для задания {job['type']}")
        handler(conn, settings, job)
    except MediaError as exc:
        if exc.reason == "canceled":
            log.info("задание %s отменено", job["id"])
        else:
            log.warning("задание %s не выполнено: %s", job["id"], exc.message)
            fail_job(conn, job["id"], f"{exc.message}\n{exc.stderr}".strip())
    except Exception as exc:  # воркер не должен падать из-за одного задания
        log.exception("задание %s упало", job["id"])
        fail_job(conn, job["id"], f"внутренняя ошибка: {exc}")
    else:
        finish_job(conn, job["id"])
        log.info("задание %s выполнено", job["id"])
    finally:
        beat.stop()
    return True


def _pid() -> int:
    import os

    return os.getpid()


def _handle_stop(signum: int, _frame: FrameType | None) -> None:
    log.info("получен сигнал %s, останавливаемся после текущего задания", signum)
    _stopping.set()


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    conn = connect(settings.db_path)
    idle = 0
    try:
        write_worker_heartbeat(conn)
        log.info("воркер запущен, полоса %s", LANE)
        while not _stopping.is_set():
            try:
                worked = run_once(conn, settings)
            except sqlite3.Error as exc:
                log.warning("база недоступна: %s", exc)
                worked = False
            if worked:
                idle = 0
                continue
            idle += 1
            if idle % IDLE_LOG_EVERY == 0:
                log.info("очередь пуста, ждём")
            write_worker_heartbeat(conn)
            time.sleep(settings.worker_poll_sec)
    finally:
        conn.close()
        log.info("воркер остановлен")


if __name__ == "__main__":
    main()
