"""Сверка границ сегментов транскрипта с измеренной картой пауз (веха M4a).

Времена Whisper не акустические: границы квантуются к целым секундам, а конец фразы в среднем
опаздывает на треть секунды от реального конца речи. По таким временам нельзя ни резать, ни
подписывать — фраза начнётся с обрубленного слога или повиснет хвостом тишины. Зато у ассета есть
собственная измеренная карта пауз (silencedetect, `silences_dense` в analysis.json), и по ней
настоящий край речи виден точно.

Правило асимметричное: сдвиг, который речь расширяет, разрешён щедро (до секунды), сжимающий —
скупо (до четверти). Молча сжимать речь нельзя. Края речи рядом нет — граница остаётся как есть
с флагом false: «проверить нечем» честнее, чем двигать вслепую (та же политика, что у подтяжки
резов, см. server/app/projects/snap.py).

Модуль чистый: ни диска, ни базы, ни ffmpeg — только арифметика над временами.
"""
from __future__ import annotations

HEAD_ZONE_SEC = 0.05  # начало записи: там 0.0 — заглушка провайдера, а не измерение
MOVED_SEC = 0.0005  # сдвиг мельче — след округления до миллисекунд, а не работа


def _pauses(silences: list[dict]) -> list[tuple[float, float]]:
    """Карта пауз парами (начало, конец), отсортированная по началу.

    Порядок — контракт раннего выхода в _nearest_speech_edge, а прийти карта может как угодно.
    """
    pairs = [(float(s["start"]), float(s["end"])) for s in silences]
    return sorted(pair for pair in pairs if pair[1] > pair[0])


def _nearest_speech_edge(
    value: float, pauses: list[tuple[float, float]], *, onset: bool, wide: float, narrow: float
) -> float | None:
    """Ближайший измеренный край речи для границы value; такого края в окне нет — None.

    onset=True — речь начинается (конец паузы), это граница начала сегмента; onset=False — речь
    кончается (начало паузы), это граница конца. Допуски по направлениям разные: наружу от речи
    отпущено wide, внутрь — narrow.
    """
    back, forward = (wide, narrow) if onset else (narrow, wide)
    window = max(wide, narrow)
    best: float | None = None
    for start, end in pauses:
        if start > value + window:
            break  # паузы отсортированы: дальше только ещё более поздние
        if start <= value <= end:
            # Граница угодила внутрь паузы: край речи известен точно, окна тут ни при чём.
            return end if onset else start
        edge = end if onset else start
        shift = edge - value  # > 0 — край впереди границы
        if shift > forward or -shift > back:
            continue
        if best is None or abs(shift) < abs(best - value):
            best = edge
    return best


def verify_segment_boundaries(
    segments: list[dict],
    dense_silences: list[dict],
    *,
    wide: float = 1.0,
    narrow: float = 0.25,
    min_segment: float = 0.05,
) -> tuple[list[dict], dict]:
    """Подтягивает границы сегментов к измеренным краям речи. Вход не мутируется.

    Возвращает копии сегментов (прочие поля сохраняются) с флагами start_verified/end_verified и
    сводку работы: total, starts_verified, ends_verified, verified_pct, adjusted, max_drift.

    Инварианты сильнее подтяжки: сегменты не переставляются и не наезжают друг на друга, короче
    min_segment не становятся. Снэп, который это ломает, откатывается, а граница остаётся
    неподтверждённой — лучше неточная граница, чем перепутанный порядок фраз.
    """
    pauses = _pauses(dense_silences)
    out: list[dict] = []
    starts_verified = ends_verified = adjusted = 0
    max_drift = 0.0
    prev_end = float("-inf")  # уже уточнённый конец предыдущего сегмента

    for index, segment in enumerate(segments):
        copy = dict(segment)
        start, end = float(segment["start"]), float(segment["end"])
        # Отрицание, а не `end <= start`: так в эту же ветку уходит NaN, с которым любое
        # сравнение ложно, — у вырожденного сегмента подтягивать нечего.
        if not (end - start > 0):
            copy["start_verified"] = copy["end_verified"] = False
            out.append(copy)
            prev_end = max(prev_end, end)
            continue

        new_start, new_end, start_ok, end_ok = start, end, False, False
        # Потолок для конца — начало следующего сегмента: иначе рез уедет в чужую речь.
        following = segments[index + 1] if index + 1 < len(segments) else None
        ceiling = float(following["start"]) if following is not None else float("inf")

        # Начало на нуле не трогаем: там пауза — это комнатный тон в голове записи, а не
        # признак того, что речь начинается позже.
        if start > HEAD_ZONE_SEC:
            edge = _nearest_speech_edge(start, pauses, onset=True, wide=wide, narrow=narrow)
            if edge is not None and prev_end <= edge < end - min_segment:
                new_start, start_ok = round(edge, 3), True

        edge = _nearest_speech_edge(end, pauses, onset=False, wide=wide, narrow=narrow)
        if edge is not None and new_start + min_segment < edge <= ceiling:
            new_end, end_ok = round(edge, 3), True

        drift = max(abs(new_start - start), abs(new_end - end))
        max_drift = max(max_drift, drift)
        if drift > MOVED_SEC:
            adjusted += 1
        starts_verified += int(start_ok)
        ends_verified += int(end_ok)

        copy["start"], copy["end"] = round(new_start, 3), round(new_end, 3)
        copy["start_verified"], copy["end_verified"] = start_ok, end_ok
        out.append(copy)
        prev_end = new_end

    total = len(out)
    return out, {
        "total": total,
        "starts_verified": starts_verified,
        "ends_verified": ends_verified,
        # Границ вдвое больше, чем сегментов: у каждого своя пара.
        "verified_pct": round((starts_verified + ends_verified) * 100 / (2 * total)) if total else 0,
        "adjusted": adjusted,
        "max_drift": round(max_drift, 3),
    }
