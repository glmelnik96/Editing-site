"""Запуск ffmpeg и ffprobe. Наружу только текст: ошибка несёт короткий хвост stderr и причину,
которую можно показать пользователю в карточке ассета.
"""
from __future__ import annotations

import logging
import subprocess

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
    log.debug("run: %s", " ".join(args[:6]))
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
    except subprocess.TimeoutExpired as exc:
        raise MediaError("timeout", f"{args[0]} не уложился в {timeout:.0f} с") from exc
    if proc.returncode != 0:
        raise MediaError(
            "tool_failed",
            f"{args[0]} завершился с кодом {proc.returncode}",
            tail_lines(proc.stderr or ""),
        )
    return proc.stderr if capture_stderr else proc.stdout
