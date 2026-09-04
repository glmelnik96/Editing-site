"""Запуск ffmpeg и ffprobe. Наружу только текст: ошибка несёт короткий хвост stderr и причину,
которую можно показать пользователю в карточке ассета.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable

log = logging.getLogger("video.media")

STDERR_TAIL_LINES = 50


class MediaError(Exception):
    def __init__(self, reason: str, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.stderr = stderr


def tail_lines(text: str, count: int = STDERR_TAIL_LINES) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-count:])


def run_tool(args: list[str], *, timeout: float, capture_stderr: bool = False) -> str:
    """Запускает инструмент и возвращает stdout. При ненулевом коде или таймауте бросает MediaError.

    capture_stderr=True возвращает stderr вместо stdout: silencedetect пишет находки именно туда.
    """
    log.debug("run: %s ... %s", args[0], args[-1])
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise MediaError("tool_missing", f"Не найден {args[0]}") from exc
    except PermissionError as exc:
        raise MediaError("tool_missing", f"Нет прав на запуск {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("timeout", f"{args[0]} не уложился в {timeout:.0f} с") from exc
    if proc.returncode != 0:
        raise MediaError(
            "tool_failed",
            f"{args[0]} завершился с кодом {proc.returncode}",
            tail_lines(proc.stderr or ""),
        )
    return proc.stderr if capture_stderr else proc.stdout


KILL_AFTER_SEC = 10.0  # после SIGTERM даём процессу столько на выход, затем убиваем


def _terminate(proc: subprocess.Popen) -> None:
    """Сначала мягко, потом жёстко: ffmpeg по SIGTERM дописывает контейнер и выходит сам."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=KILL_AFTER_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=KILL_AFTER_SEC)


def run_streaming(
    args: list[str],
    *,
    timeout: float,
    on_line: Callable[[str], None],
    should_stop: Callable[[], bool] | None = None,
    stop_check_sec: float = 2.0,
) -> None:
    """Запускает инструмент и отдаёт строки stdout по мере поступления.

    Нужен для долгих кодирований: on_line получает строки -progress, а should_stop опрашивается
    раз в stop_check_sec и позволяет прервать работу по отмене задания. stderr копится в памяти
    (у ffmpeg он короткий) и попадает в MediaError при ненулевом коде.
    """
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise MediaError("tool_missing", f"Не найден {args[0]}") from exc

    stderr_parts: list[str] = []

    def drain_stderr() -> None:
        assert proc.stderr is not None
        stderr_parts.extend(proc.stderr)

    def watch() -> None:
        """Отдельный поток: отмена и таймаут не должны ждать следующей строки stdout."""
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if time.monotonic() > deadline:
                reasons.append("timeout")
                _terminate(proc)
                return
            if should_stop is not None and should_stop():
                reasons.append("canceled")
                _terminate(proc)
                return
            time.sleep(min(stop_check_sec, 0.5))

    reasons: list[str] = []
    err_thread = threading.Thread(target=drain_stderr, daemon=True)
    watch_thread = threading.Thread(target=watch, daemon=True)
    err_thread.start()
    watch_thread.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line(line.rstrip("\n"))
    finally:
        proc.wait()
        err_thread.join(timeout=5)
        watch_thread.join(timeout=KILL_AFTER_SEC + 5)

    stderr_text = tail_lines("".join(stderr_parts))
    if reasons:
        reason = reasons[0]
        message = "Отменено" if reason == "canceled" else f"{args[0]} не уложился в {timeout:.0f} с"
        raise MediaError(reason, message, stderr_text)
    if proc.returncode != 0:
        raise MediaError("tool_failed", f"{args[0]} завершился с кодом {proc.returncode}", stderr_text)
