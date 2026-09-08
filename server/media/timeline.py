"""Времена исходника в шкале готового ролика.

Чистая часть: транскрипт снят с исходного файла, а субтитры нужны к смонтированному ролику,
где от файла остались только выбранные куски и стоят они в другом порядке. Пересчёт делается
здесь одной функцией — второй путь означал бы второе место, где субтитры разъедутся с картинкой.
"""
from __future__ import annotations


def _ms(value: float) -> float:
    return round(value, 3)


def fade_into(clip: dict, index: int) -> float:
    """Длительность перехода «в» этот клип из предыдущего. У первого клипа перехода нет.

    Поле документа: ``transition: {kind: "fade", duration}``. Ноль и отсутствие — стык встык.
    """
    if index <= 0:
        return 0.0
    raw = clip.get("transition")
    if not isinstance(raw, dict) or raw.get("kind") != "fade":
        return 0.0
    duration = raw.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, int | float):
        return 0.0
    value = float(duration)
    return value if value > 0 else 0.0


def clip_length(clip: dict) -> float:
    return float(clip["out"]) - float(clip["in"])


def clips_duration(clips: list[dict]) -> float:
    """Длина готового ролика: сумма клипов минус переходы, которые их перекрывают."""
    total = 0.0
    for index, clip in enumerate(clips):
        total += clip_length(clip) - fade_into(clip, index)
    return _ms(total)


def _all_words(transcript: dict) -> list[dict]:
    words: list[dict] = []
    for segment in transcript.get("segments") or []:
        for word in segment.get("words") or []:
            if isinstance(word, dict) and "s" in word and "e" in word:
                words.append(word)
    return words


def clip_asset_ids(clips: list[dict]) -> list[str]:
    """Уникальные ассеты шкалы в порядке появления: субтитры собираются только из них."""
    ids: list[str] = []
    for clip in clips:
        asset_id = clip.get("asset_id")
        if isinstance(asset_id, str) and asset_id not in ids:
            ids.append(asset_id)
    return ids


def words_through_clips(transcript: dict, clips: list[dict], *, asset_id: str) -> list[dict]:
    """Слова исходника в шкале ролика (спека §10.9).

    Слово с временем t внутри клипа k получает offset_k + (t − in_k); слова на стыках обрезаются
    по границам клипа, не пересёкшиеся ни с одним — выбрасываются.

    Клипы других ассетов слов не дают, но место в ролике занимают, поэтому смещение считается
    по всем клипам подряд: иначе субтитры уехали бы на длину чужого куска.

    Переход укорачивает шкалу: следующий клип начинается на duration раньше конца предыдущего,
    и слова после стыка сдвигаются на ту же величину.
    """
    words = _all_words(transcript)
    out: list[dict] = []
    offset = 0.0
    for index, clip in enumerate(clips):
        start = float(clip["in"])
        end = float(clip["out"])
        length = end - start
        if length <= 0:
            continue
        offset -= fade_into(clip, index)
        if clip.get("asset_id") == asset_id:
            for word in words:
                left = max(float(word["s"]), start)
                right = min(float(word["e"]), end)
                if right - left <= 0:
                    continue
                out.append({**word, "s": _ms(offset + left - start), "e": _ms(offset + right - start)})
        offset += length
    return out
