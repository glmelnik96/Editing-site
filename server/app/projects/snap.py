"""Подтяжка точек реза к измеренным паузам (раздел 10.6 спеки).

Правило: рез ставится не там, где попросил клиент, а на краю ближайшей измеренной паузы, отступив
внутрь неё на буфер. Речь при этом не обрезается. Если подходящей паузы рядом нет, значение остаётся
как есть, а флаг подтверждения — false: «проверить нечем» честнее, чем двигать вслепую.
"""
from __future__ import annotations

import json
import logging

from server.app.config import Settings
from server.app.storage import asset_dir

log = logging.getLogger("video.projects")

ANALYSIS_NAME = "analysis.json"


def load_silences(settings: Settings, user_id: str, asset_id: str) -> list[dict]:
    """Плотная карта пауз ассета. Файла нет или он битый — пустой список, это не ошибка."""
    path = asset_dir(settings, user_id, asset_id) / ANALYSIS_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    raw = data.get("silences_dense") or data.get("silences") or []
    out: list[dict] = []
    for item in raw:
        try:
            start, end = float(item["start"]), float(item["end"])
        except (TypeError, KeyError, ValueError):
            continue
        if end > start:
            out.append({"start": start, "end": end})
    return out


def _edge_within(value: float, edges: list[tuple[float, float]], window: float) -> tuple[float, float] | None:
    """Ближайшая пауза, чей нужный край не дальше окна. edges: (край, другой край паузы)."""
    best: tuple[float, float] | None = None
    best_distance = window
    for edge, other in edges:
        distance = abs(edge - value)
        if distance <= best_distance:
            best_distance = distance
            best = (edge, other)
    return best


def snap_in(value: float, silences: list[dict], *, window: float, buffer: float) -> float | None:
    """Точка входа встаёт перед началом речи: конец паузы минус буфер, но не дальше её середины."""
    edges = [(p["end"], p["start"]) for p in silences]
    found = _edge_within(value, edges, window)
    if found is None:
        return None
    end, start = found
    middle = (start + end) / 2
    return round(max(middle, end - buffer), 3)


def snap_out(value: float, silences: list[dict], *, window: float, buffer: float) -> float | None:
    """Точка выхода встаёт после конца речи: начало паузы плюс буфер, но не дальше её середины."""
    edges = [(p["start"], p["end"]) for p in silences]
    found = _edge_within(value, edges, window)
    if found is None:
        return None
    start, end = found
    middle = (start + end) / 2
    return round(min(middle, start + buffer), 3)


def snap_clips(
    clips: list[dict],
    *,
    settings: Settings,
    user_id: str | None = None,
    silences_by_asset: dict[str, list[dict]] | None = None,
) -> None:
    """Правит клипы на месте: время и флаги подтверждения. Карты пауз читаются по одному разу на ассет.

    silences_by_asset задают тесты; в бою карта читается с диска по user_id.
    """
    cache: dict[str, list[dict]] = dict(silences_by_asset or {})
    for clip in clips:
        if not clip.get("snap_to_pauses"):
            continue
        asset_id = clip["asset_id"]
        if asset_id not in cache:
            cache[asset_id] = load_silences(settings, user_id, asset_id) if user_id else []
        silences = cache[asset_id]
        if not silences:
            continue
        window, buffer = settings.snap_window_sec, settings.snap_buffer_sec
        new_in = snap_in(clip["in"], silences, window=window, buffer=buffer)
        new_out = snap_out(clip["out"], silences, window=window, buffer=buffer)
        candidate_in = clip["in"] if new_in is None else new_in
        candidate_out = clip["out"] if new_out is None else new_out
        if candidate_out - candidate_in < settings.min_clip_sec or candidate_in < 0:
            # Подтяжка сломала бы клип: откатываемся целиком, границы остаются неподтверждёнными.
            log.debug("снэп откачен для клипа %s", clip.get("id"))
            continue
        clip["in"], clip["out"] = candidate_in, candidate_out
        clip["in_verified"] = new_in is not None
        clip["out_verified"] = new_out is not None
