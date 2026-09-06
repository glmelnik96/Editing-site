r"""Расшифровка → реплики субтитров: типографика разбиения (спека §10.9, правила Т1–Т8).

Чистое: ни диска, ни базы, ни ffmpeg — только арифметика над строками и временами. Разбиение
решает, читаются субтитры или нет: одинокое «и» в конце строки читатель прочитает дважды, а
строку «Мы поехали в» с продолжением ниже перечитает целиком. Правила и числа штрафов перенесены
из плагина After Effects (Extensions-LLM-Chat/lib/pure/subtitles.js), где обкатаны на часовых
подкастах.

Слово — {"w": str, "s": float, "e": float, "interpolated": bool}; реплика —
{"start": float, "end": float, "text": str, "words": [слова]}, где text — строки, соединённые "\n".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import accumulate, islice
from math import isfinite

# Русские предлоги, союзы и частицы плюс английский минимум. Список выверен на подкастах в
# плагине, поэтому переносится целиком, а не пересобирается по грамматике.
GLUE_WORDS = frozenset(
    # предлоги
    ["в", "во", "на", "над", "под", "по", "за", "к", "ко", "с", "со", "о", "об", "от", "до",
     "из", "у", "для", "без", "при", "про", "через", "между", "перед"]
    # союзы
    + ["и", "а", "но", "да", "или", "либо", "что", "чтобы", "как", "когда", "если", "чем", "то"]
    # частицы
    + ["не", "ни", "же", "бы", "ли", "уж"]
    # английский минимум: в расшифровке попадаются названия и цитаты
    + ["a", "an", "the", "in", "on", "at", "of", "to", "for", "and", "or", "but", "is", "are",
       "was", "with"]
)

# Знаки, которые не мешают узнать служебное слово: «что,» — то же «что».
_PUNCT = re.compile(r"""[.,!?…:;«»"'()—–-]""")
# Слово со знаком на конце (закрывающие кавычки не в счёт) — естественное место разрыва.
_ENDS_PUNCT = re.compile(r"""[.,;:!?…]["»')\]]*$""")
_NUMBER_ONLY = re.compile(r"^[0-9]+(?:[.,][0-9]+)?$")
_ENDS_OPEN = re.compile(r"""[«"(\[—–]$""")
# Т1: с конца реплики снимаются точка, запятая, точка с запятой, двоеточие и тире.
_TAIL_PUNCT = re.compile(r"""[.,;:—–-]+(?=["»')\]]*$)""")
# Т2: с начала — точка, запятая, точка с запятой, двоеточие; тире диалога остаётся.
_HEAD_PUNCT = re.compile(r"^[.,;:]+")

BAD_BREAK_PENALTY = 100  # Т4: разрыв после служебного слова перевешивает любой перекос строк
PYRAMID_PENALTY = 0.5  # Т8: дробный, чтобы решать только ничьи — остальные слагаемые целые
# Число разбиений растёт как (слов в строке) ^ (строк − 1). Реплика в две строки по 42 знака даёт
# их пару десятков, но max_lines приходит из настроек, и на пяти строках перебор считался бы
# секундами. Дойдя до предела, выбираем лучшее из уже перебранного: разбиения идут от меньшего
# числа строк к большему, то есть отсекается заведомо менее желанный хвост.
MAX_SPLITS = 20_000


@dataclass(frozen=True)
class _Word:
    """Слово с разобранными временами; source хранит исходный словарь со всеми его полями."""

    text: str
    start: float
    end: float
    source: dict


def _as_float(value: object) -> float | None:
    """Число или None. Тот же приём, что в media/subs.py: расшифровку правит человек, и падать
    на его опечатке сборка субтитров не должна."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def is_glue_word(word: str) -> bool:
    """Служебное ли слово — предлог, союз или частица (Т3, Т4)."""
    return _PUNCT.sub("", str(word)).lower() in GLUE_WORDS


def is_bad_break(word: str) -> bool:
    """Плохо ли рвать строку сразу после этого слова (Т4, Т5, Т6)."""
    text = str(word or "")
    if not text:
        return False
    if _ENDS_PUNCT.search(text):
        return False
    if _NUMBER_ONLY.match(text):
        return True  # Т5: «5 кг» не рвётся — число без своего слова ничего не значит
    if _ENDS_OPEN.search(text):
        return True  # Т6: открытая кавычка, скобка или тире тянут за собой следующее слово
    return is_glue_word(text)


def _strip_tail(word: str) -> str:
    match = _TAIL_PUNCT.search(word)
    if match is None:
        return word
    # Многоточие остаётся: оно меняет интонацию (Т1), а расшифровка пишет его тремя точками
    # куда чаще, чем знаком «…», и снимать их — терять паузу в речи.
    if match.group().count(".") >= 3:
        return word
    return word[: match.start()] + word[match.end() :]


def polish_edges(words: list[str]) -> list[str]:
    """Знаки препинания на краях реплики (Т1, Т2).

    Список возвращается той же длины: слово, ставшее пустым, выбрасывает вызывающий вместе с его
    временем — иначе текст и времена слов разъедутся, а по ним подсвечивается речь в панели.
    """
    if not words:
        return []
    out = [str(word) for word in words]
    out[-1] = _strip_tail(out[-1])
    out[0] = _HEAD_PUNCT.sub("", out[0])
    return out


def _line_len(prefix: list[int], start: int, end: int) -> int:
    """Длина строки из слов start..end включительно вместе с пробелами между ними."""
    return prefix[end + 1] - prefix[start] + (end - start)


def _splits(prefix: list[int], start: int, end: int, lines: int, max_chars: int):
    """Разбиения слов start..end ровно на `lines` строк не длиннее max_chars.

    Разбиение — кортеж индексов последних слов каждой строки.
    """
    if lines == 1:
        if _line_len(prefix, start, end) <= max_chars:
            yield (end,)
        return
    for stop in range(start, end):
        # Длина строки растёт со stop, а слов на оставшиеся строки становится меньше: как только
        # одно из двух нарушено, дальше по этой ветке смотреть нечего.
        if _line_len(prefix, start, stop) > max_chars or end - stop < lines - 1:
            break
        for rest in _splits(prefix, stop + 1, end, lines - 1, max_chars):
            yield (stop, *rest)


def _score(words: list[str], prefix: list[int], split: tuple[int, ...]) -> float:
    """Штраф разбиения: чем меньше, тем лучше читается."""
    penalty = 0.0
    for stop in split[:-1]:
        if is_bad_break(words[stop]):
            penalty += BAD_BREAK_PENALTY
    lengths = []
    start = 0
    for stop in split:
        lengths.append(_line_len(prefix, start, stop))
        start = stop + 1
    penalty += max(lengths) - min(lengths)  # Т7: разброс длин строк
    # Т8: глаз читает сверху вниз и с расширяющейся строкой справляется легче, а широкая строка
    # над короткой читается как брошенный остаток.
    if len(lengths) > 1 and lengths[0] > lengths[-1]:
        penalty += PYRAMID_PENALTY
    return penalty


def _greedy_wrap(words: list[str], max_chars: int, max_lines: int) -> str | None:
    """Жадный перенос: слова подряд, пока строка влезает. None — не влезло ни так."""
    lines = [""]
    for word in words:
        current = lines[-1]
        if not current:
            if len(word) > max_chars:
                return None
            lines[-1] = word
        elif len(current) + 1 + len(word) <= max_chars:
            lines[-1] = f"{current} {word}"
        elif len(lines) >= max_lines or len(word) > max_chars:
            return None
        else:
            lines.append(word)
    return "\n".join(lines)


def _spread_evenly(words: list[str], max_lines: int) -> str:
    """Аварийное разбиение поровну по словам: ширина уже нарушена, и остаётся хотя бы не выйти
    за число строк (под него свёрстан кадр) и не разорвать слово переносом."""
    rows = max(1, min(max_lines, len(words)))
    size, extra = divmod(len(words), rows)
    lines = []
    start = 0
    for index in range(rows):
        stop = start + size + (1 if index < extra else 0)
        lines.append(" ".join(words[start:stop]))
        start = stop
    return "\n".join(lines)


def wrap_lines(words: list[str], max_chars: int, max_lines: int) -> str:
    """Слова реплики → строки, соединённые переводом строки (Т4, Т7, Т8).

    Перебираются все разбиения на 1..max_lines строк; побеждает наименьший штраф, при равенстве —
    меньше строк, затем разрыв ближе к середине.
    """
    limit = max_chars if max_chars > 0 else 20
    rows = max_lines if max_lines > 0 else 2
    if not words:
        return ""
    if len(words) == 1:
        return str(words[0])

    prefix = list(accumulate((len(word) for word in words), initial=0))
    last = len(words) - 1
    # Разбиения идут от меньшего числа строк к большему: усечение по MAX_SPLITS отрезает хвост,
    # который и так проигрывает при равном штрафе.
    found = (
        split
        for count in range(1, min(rows, len(words)) + 1)
        for split in _splits(prefix, 0, last, count, limit)
    )
    candidates = list(islice(found, MAX_SPLITS))
    if not candidates:
        return _greedy_wrap(words, limit, rows) or _spread_evenly(words, rows)

    middle = last / 2
    best = min(
        candidates,
        key=lambda split: (_score(words, prefix, split), len(split), abs(split[0] - middle)),
    )
    lines = []
    start = 0
    for stop in best:
        lines.append(" ".join(words[start:stop + 1]))
        start = stop + 1
    return "\n".join(lines)


def _usable(words: list[dict]) -> list[_Word]:
    """Слова, которые вообще можно показать: с текстом и разборчивыми временами.

    Пустое слово ломает подсчёт ширины строки, а слово без времени нечем поставить в реплику.
    """
    out: list[_Word] = []
    for word in words or []:
        if not isinstance(word, dict):
            continue
        text = " ".join(str(word.get("w") or "").split())
        start, end = _as_float(word.get("s")), _as_float(word.get("e"))
        if text and start is not None and end is not None:
            out.append(_Word(text=text, start=start, end=end, source=word))
    return out


def _hand_back_glue(take: list[str], index: int, total: int) -> int:
    """Т3: реплика не кончается словом, после которого плохо рвать, пока дальше есть слова —
    оно уезжает в следующую. Не больше двух слов подряд: иначе цепочка «и в то» растащит
    половину фразы.

    Условие то же, что у разрыва строки (Т4–Т6): голое число и открывающая кавычка на конце
    реплики так же нехороши, как и висящий предлог, а слово со знаком препинания — законный конец.

    Возвращает новый индекс следующего необработанного слова.
    """
    for _ in range(2):
        if index >= total or len(take) < 2:
            break
        last = take[-1]
        if not is_bad_break(last):
            break
        take.pop()
        index -= 1
    return index


def build_cues(words: list[dict], *, max_chars: int, max_lines: int, max_dur: float) -> list[dict]:
    """Слова расшифровки → реплики субтитров: набор по ширине и длительности плюс правила Т1–Т8.

    Времена реплики берутся из первого и последнего уцелевшего слова, а не считаются: слово уже
    несёт время, пересчитанное через клипы проекта.
    """
    items = _usable(words)
    total = len(items)
    cues: list[dict] = []
    index = 0
    while index < total:
        first = index
        take: list[str] = []
        while index < total:
            fits = _greedy_wrap([*take, items[index].text], max_chars, max_lines)
            too_long = items[index].end - items[first].start > max_dur + 1e-9
            if take and (fits is None or too_long):
                break
            take.append(items[index].text)
            index += 1
            if fits is None:
                break  # слово шире строки: рвать его переносом хуже, чем нарушить ширину
        index = _hand_back_glue(take, index, total)

        polished = polish_edges(take)
        kept = [(text, items[first + shift]) for shift, text in enumerate(polished) if text]
        if not kept:
            continue  # реплика была из одних знаков препинания — показывать нечего
        cue_words = [{**item.source, "w": text} for text, item in kept]
        cues.append(
            {
                "start": round(kept[0][1].start, 3),
                "end": round(kept[-1][1].end, 3),
                "text": wrap_lines([text for text, _ in kept], max_chars, max_lines),
                "words": cue_words,
            }
        )
    return cues
