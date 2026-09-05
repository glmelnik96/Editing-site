"""SRT → WebVTT. Плеер в браузере понимает только VTT, а команда чаще приносит SRT.

Разбор нарочно простой: нас интересуют строки таймингов, остальное переносится как есть.
Стили и позиционирование SRT не поддерживаются и не нужны: в M3 субтитры вжигает ffmpeg из исходного файла.
"""
from __future__ import annotations

import re

TIME_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2})[,.](\d{1,3})"
)
INDEX_RE = re.compile(r"^\d+$")
VTT_HEADER = "WEBVTT"


class SubtitleInvalid(Exception):
    pass


def _normalize(text: str) -> str:
    return text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")


def _fix_time_line(line: str) -> str | None:
    match = TIME_RE.search(line)
    if match is None:
        return None
    start_h, start_ms, end_h, end_ms = match.groups()
    return f"{start_h}.{start_ms.ljust(3, '0')} --> {end_h}.{end_ms.ljust(3, '0')}"


def to_vtt(text: str, *, ext: str) -> str:
    """Готовый VTT или SubtitleInvalid. VTT пропускается как есть, но обязан иметь заголовок."""
    text = _normalize(text)
    if ext == "vtt":
        if not text.lstrip().startswith(VTT_HEADER):
            raise SubtitleInvalid("в файле VTT нет заголовка WEBVTT")
        return text
    if ext != "srt":
        raise SubtitleInvalid("субтитры принимаются в формате SRT или VTT")

    out: list[str] = []
    seen_times = False
    previous_blank = True
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        fixed = _fix_time_line(line)
        if fixed is not None:
            out.append(fixed)
            seen_times = True
            previous_blank = False
            continue
        # Номер реплики в SRT идёт отдельной строкой перед таймингом; в VTT он не нужен.
        if previous_blank and INDEX_RE.match(line.strip()):
            continue
        out.append(line)
        previous_blank = not line
    if not seen_times:
        raise SubtitleInvalid("в файле нет ни одной строки таймингов")
    body = "\n".join(out).strip("\n")
    return f"{VTT_HEADER}\n\n{body}\n"
