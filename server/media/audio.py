"""Звук: WAV 16 кГц, пики, уровень речи и карты пауз.

Считает всё ffmpeg, Python только разбирает текст: поэлементная обработка семплов в Python слишком
медленная, numpy в зависимости не берём, а audioop удалён в Python 3.13.
"""
from __future__ import annotations

import math
import re
import statistics

from server.app.config import Settings
from server.media.run import run_tool

WAV_TIMEOUT_SEC = 3600
RMS_WINDOW_SEC = 0.05  # окно для оценки уровня речи (раздел 10.4 спеки)
LOUD_FRACTION = 0.02  # доля самых громких окон, по которым берётся медиана
SILENCE_FLOOR_DB = -60.0  # ниже не опускаемся: там уже собственный шум записи
FALLBACK_THRESHOLD_DB = -35.0  # если уровень речи оценить не удалось
RETRY_ABOVE_DB = -55.0  # если пауз не нашлось, а порог выше — повторяем на 10 дБ ниже
RETRY_STEP_DB = 10.0

_LEVEL_RE = re.compile(
    r"^lavfi\.astats\.Overall\.(?:Peak|RMS)_level=(-?[\d.]+|-?inf|-?nan)$", re.MULTILINE
)
_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


def wav_args(settings: Settings, src: str, dst: str) -> list[str]:
    return [
        settings.ffmpeg_path, "-v", "error", "-y", "-i", src,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst,
    ]


def levels_args(settings: Settings, wav: str, *, window_sec: float, key: str) -> list[str]:
    """Уровень в дБ на каждое окно: ffmpeg печатает метаданные astats в stdout."""
    samples = max(1, round(16000 * window_sec))
    chain = (
        f"asetnsamples=n={samples}:p=0,astats=metadata=1:reset=1,"
        f"ametadata=print:key=lavfi.astats.Overall.{key}:file=-"
    )
    return [settings.ffmpeg_path, "-v", "error", "-nostats", "-i", wav, "-af", chain, "-f", "null", "-"]


def silence_args(settings: Settings, wav: str, *, threshold_db: float, min_sec: float) -> list[str]:
    chain = f"silencedetect=noise={threshold_db:.1f}dB:d={min_sec}"
    return [settings.ffmpeg_path, "-v", "info", "-nostats", "-i", wav, "-af", chain, "-f", "null", "-"]


def parse_levels(text: str) -> list[float]:
    """Значения в дБ по порядку окон; тишина приходит как -inf."""
    out: list[float] = []
    for raw in _LEVEL_RE.findall(text):
        try:
            out.append(float(raw))
        except ValueError:
            out.append(-math.inf)
    return out


def db_to_amplitude(db: float) -> int:
    """дБ → 0..255. −inf и всё тише −60 дБ считаем нулём."""
    if not math.isfinite(db) or db <= SILENCE_FLOOR_DB:
        return 0
    return max(0, min(255, round(255 * (10 ** (min(db, 0.0) / 20)))))


def peaks_from_levels(levels: list[float]) -> list[int]:
    return [db_to_amplitude(db) for db in levels]


def speech_level_db(levels: list[float]) -> float | None:
    """Медиана самых громких LOUD_FRACTION окон. None, если звука нет вовсе."""
    finite = [db for db in levels if math.isfinite(db)]
    if not finite:
        return None
    finite.sort(reverse=True)
    take = max(1, round(len(finite) * LOUD_FRACTION))
    return round(statistics.median(finite[:take]), 3)


def silence_threshold_db(speech_db: float | None, *, offset: float) -> float:
    if speech_db is None:
        return FALLBACK_THRESHOLD_DB
    return round(max(SILENCE_FLOOR_DB, speech_db - offset), 1)


def parse_silences(text: str, *, duration: float) -> list[dict]:
    """Пары start/end из лога silencedetect. Последняя пауза без конца закрывается концом файла."""
    starts = [float(v) for v in _START_RE.findall(text)]
    ends = [float(v) for v in _END_RE.findall(text)]
    out: list[dict] = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else duration
        start = max(0.0, min(start, duration))
        end = max(0.0, min(end, duration))
        if end - start <= 0:
            continue
        out.append({"start": round(start, 3), "end": round(end, 3)})
    return out


def measure_levels(settings: Settings, wav: str, *, window_sec: float, key: str) -> list[float]:
    text = run_tool(levels_args(settings, wav, window_sec=window_sec, key=key), timeout=WAV_TIMEOUT_SEC)
    return parse_levels(text)


def detect_silences(
    settings: Settings, wav: str, *, threshold_db: float, min_sec: float, duration: float
) -> list[dict]:
    text = run_tool(
        silence_args(settings, wav, threshold_db=threshold_db, min_sec=min_sec),
        timeout=WAV_TIMEOUT_SEC,
        capture_stderr=True,
    )
    return parse_silences(text, duration=duration)


def analyze_audio(settings: Settings, wav: str, *, duration: float) -> dict:
    """peaks.json и analysis.json одним проходом по звуку.

    Пики берутся окнами 1/peaks_per_sec, уровень речи — окнами 50 мс (раздел 10.4 спеки).
    Если при выбранном пороге пауз не нашлось, а порог высокий, повторяем ниже: тихая запись
    целиком «звучит» и снэп резов остаётся без опоры.
    """
    peak_levels = measure_levels(settings, wav, window_sec=1 / settings.peaks_per_sec, key="Peak_level")
    rms_levels = measure_levels(settings, wav, window_sec=RMS_WINDOW_SEC, key="RMS_level")
    speech = speech_level_db(rms_levels)
    threshold = silence_threshold_db(speech, offset=settings.speech_offset_db)
    silences = detect_silences(
        settings, wav, threshold_db=threshold, min_sec=settings.silence_min_sec, duration=duration
    )
    if not silences and threshold > RETRY_ABOVE_DB:
        threshold = round(max(SILENCE_FLOOR_DB, threshold - RETRY_STEP_DB), 1)
        silences = detect_silences(
            settings, wav, threshold_db=threshold, min_sec=settings.silence_min_sec, duration=duration
        )
    dense = detect_silences(
        settings, wav, threshold_db=threshold, min_sec=settings.silence_dense_min_sec, duration=duration
    )
    peaks = {"rate": settings.peaks_per_sec, "peaks": peaks_from_levels(peak_levels)}
    analysis = {
        "duration": round(duration, 3),
        "speech_level_db": speech,
        "threshold_db": threshold,
        "silences": silences,
        "silences_dense": dense,
    }
    return {"peaks": peaks, "analysis": analysis}
