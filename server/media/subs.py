"""Транскрипт → SRT и WebVTT. Чистое: ни диска, ни базы, ни настроек.

Реплика здесь — сегмент транскрипта как есть, одна на сегмент. Умной разбивки на строки (не больше
двух строк, около 42 знаков, разрыв на паузе или знаке препинания) тут нет намеренно: она приедет
в M4b вместе с пересчётом слов через клипы, и придумывать её второй раз здесь нельзя.

Времена — от начала исходника, как в самом транскрипте (спека §10.8).
"""
from __future__ import annotations

from math import isfinite

VTT_HEADER = "WEBVTT"


def _as_float(value: object) -> float | None:
    """Число или None. Транскрипт мог править человек, и падать на его опечатке экспорт не должен.
    (Тот же приём, что в media/transcribe.py: обе функции чистые и читают чужие данные.)"""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _timecode(seconds: float, *, sep: str) -> str:
    """00:00:01,230 для SRT и 00:00:01.230 для VTT; часы всегда двузначные.

    Отрицательное время прижимается к нулю: субтитр не может начаться раньше файла, а «-1» в
    таймкоде плеер не разберёт вовсе.
    """
    total = max(0, round(seconds * 1000))
    hours, rest = divmod(total, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def _text(value: object) -> str:
    """Текст реплики: перевод строки внутри сегмента остаётся (в SRT это вторая строка), а пустые
    строки схлопываются — пустая строка кончает реплику и разорвала бы файл надвое."""
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line)


def _cues(transcript: dict) -> list[tuple[float, float, str]]:
    """Реплики по возрастанию времени.

    Сегмент без разборчивых времён или без текста пропускается: показать его нечем, а пустая
    реплика ломает формат. Порядок задаём здесь, а не верим транскрипту: сегменты в нём собраны из
    чанков, и чужой файл мог прийти как угодно.
    """
    segments = transcript.get("segments") if isinstance(transcript, dict) else None
    out: list[tuple[float, float, str]] = []
    for item in segments or []:
        if not isinstance(item, dict):
            continue
        start, end = _as_float(item.get("start")), _as_float(item.get("end"))
        text = _text(item.get("text"))
        if start is None or end is None or not text:
            continue
        out.append((start, end, text))
    out.sort(key=lambda cue: (cue[0], cue[1]))
    return out


def to_srt(transcript: dict) -> str:
    """Транскрипт в SRT: номера с единицы, времена через запятую, пустая строка между репликами."""
    blocks = [
        f"{number}\n{_timecode(start, sep=',')} --> {_timecode(end, sep=',')}\n{text}"
        for number, (start, end, text) in enumerate(_cues(transcript), start=1)
    ]
    # Без реплик — пустой файл: номер и таймкод придумывать не из чего.
    return "\n\n".join(blocks) + "\n" if blocks else ""


def to_vtt(transcript: dict) -> str:
    """Транскрипт в WebVTT: заголовок, времена через точку, номера реплик не нужны."""
    blocks = [
        f"{_timecode(start, sep='.')} --> {_timecode(end, sep='.')}\n{text}"
        for start, end, text in _cues(transcript)
    ]
    body = "\n\n".join(blocks) + "\n" if blocks else ""
    # Заголовок и пустая строка после него стоят всегда, даже когда реплик нет: без заголовка это
    # уже не WebVTT, и плеер откажется от файла целиком.
    return f"{VTT_HEADER}\n\n{body}"
