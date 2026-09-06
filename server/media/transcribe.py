"""Чистая часть транскрипции: план нарезки звука и аргументы ffmpeg для одного чанка.

Ни сети, ни базы, ни запуска процессов — только арифметика над временами и списки аргументов.
Здесь же остальные чистые шаги (нормализация ответа провайдера, швы, клэмп, интерполяция слов):
их дешевле проверять на фикстурах за миллисекунды, чем гонять настоящий ffmpeg и провайдера.
"""
from __future__ import annotations

from math import isfinite
from pathlib import Path

from server.app.config import Settings
from server.media.run import MediaError

CHUNK_CODEC = "libmp3lame"
CHUNK_BITRATE = "64k"  # около 5 МБ на десять минут: влезает в предел загрузки провайдера (20 МБ)
CHUNK_RATE = "16000"  # речь выше 8 кГц не несёт: провайдер всё равно ресемплит к 16 кГц
TAIL_FRACTION = 0.25  # хвост короче этой доли цели отдельным чанком не выделяем
MIN_CHUNK_BYTES = 1024  # меньше — ffmpeg записал один заголовок, звука в файле нет


def _cut_point(
    goal: float, silences: list[dict], *, start: float, duration: float, window: float
) -> float:
    """Ближайшая к цели середина паузы в окне ±window; такой паузы нет — сама цель.

    Пауза за окном не годится: чанк уехал бы далеко от цели и мог превысить предел загрузки.
    """
    best: float | None = None
    for silence in silences:
        middle = round((float(silence["start"]) + float(silence["end"])) / 2, 3)
        if abs(middle - goal) > window or not start < middle < duration:
            continue
        if best is None or abs(middle - goal) < abs(best - goal):
            best = middle
    return goal if best is None else best


def chunk_plan(
    *, duration: float, silences: list[dict], target: int, window: int
) -> list[tuple[float, float]]:
    """Границы чанков: сплошное покрытие [0, duration] без зазоров и нахлёстов.

    Цель — target секунд от начала текущего чанка; в пределах ±window ищем ближайшую к цели паузу
    и режем по её середине. По паузе, а не по времени: жёсткая граница попадает в середину слова,
    и провайдер выдаёт на шве две половинки фразы, которые потом приходится сшивать.
    Хвост короче четверти цели приклеивается к предыдущему чанку: отдельный запрос ради десяти
    секунд не окупается ни временем, ни риском лишнего шва.
    """
    if duration <= 0 or target <= 0:
        # Пустое аудио и бессмысленная цель: цикл ниже на таких значениях не сойдётся.
        return [(0.0, round(duration, 3))] if duration > 0 else []
    plan: list[tuple[float, float]] = []
    start = 0.0
    crumb = target * TAIL_FRACTION
    while duration - start > target:
        cut = _cut_point(start + target, silences, start=start, duration=duration, window=window)
        if duration - cut < crumb:
            break
        plan.append((round(start, 3), round(cut, 3)))
        start = cut
    plan.append((round(start, 3), round(duration, 3)))
    return plan


def chunk_args(settings: Settings, *, src: str, dst: str, start: float, end: float) -> list[str]:
    """Кусок [start, end] в MP3 64 кбит / 16 кГц / моно.

    -ss и -to стоят после -i: быстрый поиск до декодирования мажет на десятки миллисекунд, а
    времена чанка идут в транскрипт как есть, поэтому точность важнее скорости.
    Контейнер ffmpeg выбирает по расширению dst — имя обязано оканчиваться на .mp3. Временное
    имя с суффиксом .part не годится, по нему формат не угадывается (грабли M1b, см. proxy.py).
    """
    return [
        settings.ffmpeg_path, "-v", "error", "-y", "-i", src,
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-vn", "-ac", "1", "-ar", CHUNK_RATE, "-c:a", CHUNK_CODEC, "-b:a", CHUNK_BITRATE, dst,
    ]


def check_chunk_size(path: str | Path) -> int:
    """Размер нарезанного чанка. Пустой файл — ошибка нарезки, а не молчаливый фрагмент.

    MP3 даже полной тишины весит килобайты. Если файла нет или в нём один заголовок, провайдер
    вернёт пустой текст, и в транскрипте молча появится дыра длиной в целый чанк.
    """
    file = Path(path)
    size = file.stat().st_size if file.is_file() else 0
    if size < MIN_CHUNK_BYTES:
        raise MediaError("empty_chunk", f"Чанк {file.name} пуст ({size} Б): нарезка не удалась")
    return size


# ── Разбор ответа провайдера ───────────────────────────────────────────────────────────────────

QUALITY_FIELDS = ("no_speech_prob", "avg_logprob", "compression_ratio")
SEAM_TOLERANCE = 0.05  # дальше от границы чанка — уже не шов, а обычный нахлёст
SEAM_MIN_KEEP = 0.3  # остаток подрезанной головы короче — огрызок дубля, а не фраза
MIN_SEGMENT = 0.05  # после клэмпа: короче — не сегмент
NO_SPEECH_MAX = 0.5
LOGPROB_MIN = -1.0
COMPRESSION_MAX = 2.4
MIN_SILENCE = 0.3  # тишина короче — вдох внутри фразы, речь на ней не рвётся
LEADING_SLIVER = 0.4  # звучащий огрызок перед первой паузой сегмента (см. _speech_intervals)
WORD_OVERHEAD = 0.4  # добавка на слово: артикуляция и микропауза перед следующим
DIGIT_SYLLABLES = 2  # «2026» читается числительным: «две тысячи двадцать шесть»
VOWELS = frozenset("аеёиоуыэюяaeiouy")
DIGITS = frozenset("0123456789")
EPS = 1e-9


def _as_float(value: object) -> float | None:
    """Число или None. Провайдер иногда шлёт null и строки, и падать на этом нельзя."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _ms(value: float) -> float:
    """Времена везде до миллисекунд: дальше точность мнимая, а разница мешает сравнивать."""
    return round(value, 3)


def normalize_chunk(result: dict, *, offset: float) -> list[dict]:
    """Сегменты одного чанка, переведённые во время всего файла.

    Единственное место, где ко временам прибавляется смещение чанка. Второго пути нормализации
    быть не должно: в родственном плагине именно дублирующий путь однажды потерял вычитание точки
    входа и сдвинул весь транскрипт. Здесь же отсеивается мусор провайдера — пустой текст и
    перевёрнутые времена, — чтобы дальше по конвейеру шли только осмысленные сегменты.
    """
    raw = result.get("segments") if isinstance(result, dict) else None
    if not isinstance(raw, list):
        return []
    shift = _as_float(offset)
    if shift is None:
        shift = 0.0
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start, end = _as_float(item.get("start")), _as_float(item.get("end"))
        text = " ".join(str(item.get("text") or "").split())
        if start is None or end is None or end <= start or not text:
            continue
        segment: dict = {"start": _ms(start + shift), "end": _ms(end + shift), "text": text}
        # Поля качества несём дальше: по ним mark_suspect помечает подозрительные сегменты.
        for field in QUALITY_FIELDS:
            value = _as_float(item.get(field))
            if value is not None:
                segment[field] = value
        out.append(segment)
    return out


def _words_after(words: list, start: float) -> list[dict]:
    """Слова подрезанной головы, оставшиеся правее нового начала.

    Слово внахлёст подрезаем, а не выбрасываем: иначе в начале фразы появится дыра.
    """
    kept: list[dict] = []
    for item in words:
        if not isinstance(item, dict):
            continue
        word_start, word_end = _as_float(item.get("s")), _as_float(item.get("e"))
        if word_start is None or word_end is None or word_end <= start + EPS:
            continue
        word = dict(item)
        if word_start < start:
            word["s"] = _ms(start)
        kept.append(word)
    return kept


def fix_seams(segments: list[dict], *, boundaries: list[float]) -> tuple[list[dict], dict]:
    """Сшивание дублей на границах чанков: (сегменты, {"fixed": n, "dropped": m}).

    Фраза на шве приходит дважды — хвостом в чанке N и головой в N+1. Голову подрезаем к концу
    хвоста, а если от неё остаётся меньше SEAM_MIN_KEEP, выбрасываем целиком: это обрывок того же
    дубля, а не отдельная фраза. Нахлёст вдали от границы не трогаем — там провайдер так услышал,
    и чинить это должна не эта функция.
    """
    marks = [value for value in (_as_float(item) for item in boundaries or []) if value is not None]
    out: list[dict] = []
    stats = {"fixed": 0, "dropped": 0}
    for item in sorted(segments, key=lambda one: _as_float(one.get("start")) or 0.0):
        segment = dict(item)
        start, end = _as_float(segment.get("start")), _as_float(segment.get("end"))
        previous_end = _as_float(out[-1].get("end")) if out else None
        if start is None or end is None or previous_end is None or previous_end <= start:
            out.append(segment)
            continue
        if not any(abs(start - mark) <= SEAM_TOLERANCE for mark in marks):
            out.append(segment)
            continue
        if end - previous_end < SEAM_MIN_KEEP:
            stats["dropped"] += 1
            continue
        segment["start"] = _ms(previous_end)
        if isinstance(segment.get("words"), list):
            segment["words"] = _words_after(segment["words"], previous_end)
        stats["fixed"] += 1
        out.append(segment)
    return out, stats


def clamp_segments(segments: list[dict], *, duration: float) -> list[dict]:
    """Обрезка сегментов к [0, duration].

    Whisper регулярно тянет последний сегмент за конец аудио: в живом прогоне окно 600–780
    закончилось на 783.9. Без клэмпа такой сегмент уезжает за длительность ассета, и плеер
    показывает субтитр там, где кадров уже нет.
    """
    limit = _as_float(duration)
    if limit is None or limit <= 0:
        return []
    out: list[dict] = []
    for item in segments:
        start, end = _as_float(item.get("start")), _as_float(item.get("end"))
        if start is None or end is None:
            continue
        start, end = max(0.0, start), min(limit, end)
        if end - start < MIN_SEGMENT:
            continue
        segment = dict(item)
        segment["start"], segment["end"] = _ms(start), _ms(end)
        out.append(segment)
    return out


def mark_suspect(segments: list[dict]) -> list[dict]:
    """Проставляет suspect по метрикам провайдера.

    Помечаем, а не выбрасываем: на музыке и тихой речи пороги ошибаются в обе стороны, и решать,
    что делать с сомнительной репликой, должен человек, глядя на текст, а не порог.
    """
    out: list[dict] = []
    for item in segments:
        no_speech = _as_float(item.get("no_speech_prob")) or 0.0
        logprob = _as_float(item.get("avg_logprob")) or 0.0
        compression = _as_float(item.get("compression_ratio")) or 0.0
        segment = dict(item)
        segment["suspect"] = (
            no_speech > NO_SPEECH_MAX or logprob < LOGPROB_MIN or compression > COMPRESSION_MAX
        )
        out.append(segment)
    return out


def _word_weight(word: str) -> float:
    """Вес слова: слоги плюс постоянная добавка.

    Длительность слова держится за число слогов, а не букв: «и» и «вздрогнешь» отличаются в буквах
    вдесятеро, а во времени вдвое. Добавка WORD_OVERHEAD — на артикуляцию и микропаузу, без неё
    короткие служебные слова сжимаются почти в ноль.
    """
    lowered = word.lower()
    syllables = sum(1 for symbol in lowered if symbol in VOWELS)
    syllables += DIGIT_SYLLABLES * sum(1 for symbol in lowered if symbol in DIGITS)
    return max(syllables, 1) + WORD_OVERHEAD


def _speech_intervals(start: float, end: float, silences: list[dict]) -> list[tuple[float, float]]:
    """Сегмент минус измеренные тишины: куски, в которых речь действительно звучит."""
    pauses: list[tuple[float, float]] = []
    for item in silences or []:
        if not isinstance(item, dict):
            continue
        pause_start, pause_end = _as_float(item.get("start")), _as_float(item.get("end"))
        if pause_start is None or pause_end is None or pause_end - pause_start < MIN_SILENCE:
            continue
        inside_start, inside_end = max(pause_start, start), min(pause_end, end)
        if inside_end > inside_start:
            pauses.append((inside_start, inside_end))
    if not pauses:
        return [(start, end)]
    pauses.sort()
    out: list[tuple[float, float]] = []
    cursor = start
    for pause_start, pause_end in pauses:
        if pause_start > cursor + EPS:
            out.append((cursor, pause_start))
        cursor = max(cursor, pause_end)
    if cursor < end - EPS:
        out.append((cursor, end))
    if not out:
        # Сегмент целиком накрыт тишиной: раскладывать слова всё равно надо, кладём их по всему
        # сегменту. Спорить с провайдером о том, была ли там речь, — не дело этой функции.
        return [(start, end)]
    # Whisper открывает сегмент примерно на 0.2 с раньше паузы, отделяющей его от предыдущей фразы
    # (замерено живьём: сегмент с 8.0, тишина 8.217–9.267, речь началась в 9.267). Этот звучащий
    # огрызок — хвост соседа, а не первое слово, и слово на нём начинаться не должно.
    if len(out) > 1 and out[0][1] - out[0][0] < LEADING_SLIVER:
        out.pop(0)
    return out


def _at(position: float, intervals: list[tuple[float, float]], *, opening: bool) -> float:
    """Отметка position, отсчитанная по звучащему времени (паузы не считаются), во время файла.

    Конец слова и начало следующего на одной и той же отметке разъезжаются намеренно: конец
    садится на край паузы, а начало — уже за паузой, иначе слово «произносится» в тишине.
    """
    passed = 0.0
    for begin, finish in intervals:
        length = finish - begin
        if position < passed + length - EPS or (not opening and position <= passed + length + EPS):
            return begin + (position - passed)
        passed += length
    return intervals[-1][1]


def interpolate_words(segment: dict, *, silences: list[dict]) -> list[dict]:
    """Слова сегмента с временами: [{"w", "s", "e", "interpolated": True}].

    Пословных таймкодов провайдер не отдаёт, поэтому длительность сегмента делится между словами
    пропорционально слогам и раскладывается только по интервалам речи. Приём взят из родственного
    плагина (`lib/pure/subtitles.js`, `alignWords`), где обкатан на часовых подкастах.

    Флаг interpolated стоит на каждом слове не для красоты: точность такой раскладки около ±0.3 с,
    и резать по этим границам нельзя. Для резов есть снэп к измеренным паузам.
    """
    words = str(segment.get("text") or "").split()
    start, end = _as_float(segment.get("start")), _as_float(segment.get("end"))
    if not words or start is None or end is None or end <= start:
        return []
    intervals = _speech_intervals(start, end, silences)
    speech = sum(finish - begin for begin, finish in intervals)
    weights = [_word_weight(word) for word in words]
    total_weight = sum(weights)
    out: list[dict] = []
    spoken = 0.0
    for index, (word, weight) in enumerate(zip(words, weights, strict=True)):
        span = speech * weight / total_weight
        # Последнее слово кончается ровно на конце речи: накопленная ошибка деления не должна
        # оставлять хвостик или вылезать за сегмент.
        last = index == len(words) - 1
        finish = intervals[-1][1] if last else _at(spoken + span, intervals, opening=False)
        out.append({
            "w": word,
            "s": _ms(_at(spoken, intervals, opening=True)),
            "e": _ms(finish),
            "interpolated": True,
        })
        spoken += span
    return out
