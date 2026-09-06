"""Воркер: python -m server.worker. Один процесс, полоса на поток, одно задание за раз в каждой.

Полос две: `cpu` (анализ, прокси, рендер) и `net` (транскрипция). Разделены они потому, что
транскрипция ждёт сеть десятками минут, и держать за ней очередь рендеров нельзя. У каждого потока
своё соединение с базой: sqlite3.Connection не потокобезопасен, и общее соединение здесь было бы
гонкой, а не экономией.

Пульс идёт из отдельного потока со своим соединением: пока ffmpeg кодирует час, janitor не должен
считать задание зависшим. Отмена и остановка сервиса доходят до ffmpeg через should_stop у run_streaming.
"""
from __future__ import annotations

import logging
import signal
import sqlite3
import threading
from types import FrameType

from server.app.config import Settings
from server.app.main import configure_logging
from server.db.core import connect
from server.media.run import MediaError
from server.worker.handlers import HANDLERS
from server.worker.queue import (
    STOPPING,
    claim_job,
    fail_job,
    finish_job,
    heartbeat,
    requeue_job,
    requeue_orphans,
    write_worker_heartbeat,
)

log = logging.getLogger("video.worker")

HEARTBEAT_SEC = 10.0
IDLE_LOG_EVERY = 300  # раз в сколько пустых кругов писать, что воркер жив


class Heartbeat(threading.Thread):
    """Обновляет пульс задания и процесса, пока идёт работа. Своё соединение: чужое занято ffmpeg-циклом."""

    def __init__(self, settings: Settings, *, job_id: str, interval: float = HEARTBEAT_SEC) -> None:
        super().__init__(daemon=True)
        self.settings = settings
        self.job_id = job_id
        self.interval = interval
        # Имя _stop занято самим threading.Thread: подмена его атрибутом Event ломает join(),
        # который зовёт self._stop() как метод. На Python 3.12 это роняло воркер после каждого задания.
        self._done = threading.Event()
        self._beat_seen = threading.Event()

    def run(self) -> None:
        conn = connect(self.settings.db_path)
        try:
            while not self._done.is_set():
                try:
                    heartbeat(conn, self.job_id)
                    write_worker_heartbeat(conn)
                    self._beat_seen.set()
                except sqlite3.Error as exc:  # база занята — не повод ронять работу
                    log.warning("пульс не записался: %s", exc)
                self._done.wait(self.interval)
        finally:
            conn.close()

    def wait_for_first(self, timeout: float) -> bool:
        return self._beat_seen.wait(timeout)

    def stop(self) -> None:
        self._done.set()
        self.join(timeout=5)


def run_once(conn: sqlite3.Connection, settings: Settings, lane: str) -> bool:
    """Взять одно задание своей полосы и выполнить. True, если работа была."""
    job = claim_job(conn, lane=lane, pid=_pid())
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
        if STOPPING.is_set():
            requeue_job(conn, job["id"])
            log.info("задание %s возвращено в очередь: воркер останавливается", job["id"])
        elif exc.reason == "canceled":
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
    STOPPING.set()


def serve(settings: Settings, lane: str) -> None:
    """Цикл одной полосы. Соединение открывается здесь: оно принадлежит этому потоку и никому больше."""
    conn = connect(settings.db_path)
    idle = 0
    try:
        log.info("полоса %s запущена", lane)
        while not STOPPING.is_set():
            try:
                worked = run_once(conn, settings, lane)
            except sqlite3.Error as exc:
                log.warning("полоса %s: база недоступна: %s", lane, exc)
                worked = False
            if worked:
                idle = 0
                continue
            idle += 1
            if idle % IDLE_LOG_EVERY == 0:
                log.info("полоса %s: очередь пуста, ждём", lane)
            write_worker_heartbeat(conn)
            # Ждём с оглядкой на сигнал: иначе остановка сервиса упирается в полный сон полосы.
            STOPPING.wait(settings.worker_poll_sec)
    finally:
        conn.close()
        log.info("полоса %s остановлена", lane)


def lanes_of(settings: Settings) -> list[str]:
    names = [lane.strip() for lane in settings.worker_lanes.split(",") if lane.strip()]
    return names or ["cpu"]


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    conn = connect(settings.db_path)
    try:
        write_worker_heartbeat(conn)
        returned = requeue_orphans(conn)
        if returned:
            log.info("заданий возвращено в очередь после перезапуска: %d", returned)
    finally:
        conn.close()
    lanes = lanes_of(settings)
    log.info("воркер запущен, полосы: %s", ", ".join(lanes))
    threads = [
        threading.Thread(target=serve, args=(settings, lane), name=f"lane-{lane}") for lane in lanes
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    log.info("воркер остановлен")


if __name__ == "__main__":
    main()
