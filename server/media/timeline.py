"""Времена исходника в шкале готового ролика.

Чистая часть: транскрипт снят с исходного файла, а субтитры нужны к смонтированному ролику,
где от файла остались только выбранные куски и стоят они в другом порядке. Пересчёт делается
здесь одной функцией — второй путь означал бы второе место, где субтитры разъедутся с картинкой.
"""
from __future__ import annotations


def _ms(value: float) -> float:
    return round(value, 3)


def _all_words(transcript: dict) -> list[dict]:
    words: list[dict] = []
    for segment in transcript.get("segments") or []:
        for word in segment.get("words") or []:
            if isinstance(word, dict) and "s" in word and "e" in word:
                words.append(word)
    return words


def words_through_clips(transcript: dict, clips: list[dict], *, asset_id: str) -> list[dict]:
    """Слова исходника в шкале ролика (спека §10.9).

    Слово с временем t внутри клипа k получает offset_k + (t − in_k); слова на стыках обрезаются
    по границам клипа, не пересёкшиеся ни с одним — выбрасываются.

    Клипы других ассетов слов не дают, но место в ролике занимают, поэтому смещение считается
    по всем клипам подряд: иначе субтитры уехали бы на длину чужого куска.
    """
    words = _all_words(transcript)
    out: list[dict] = []
    offset = 0.0
    for clip in clips:
        start = float(clip["in"])
        end = float(clip["out"])
        length = end - start
        if length <= 0:
            continue
        if clip.get("asset_id") == asset_id:
            for word in words:
                left = max(float(word["s"]), start)
                right = min(float(word["e"]), end)
                if right - left <= 0:
                    continue
                out.append({**word, "s": _ms(offset + left - start), "e": _ms(offset + right - start)})
        offset += length
    return out
