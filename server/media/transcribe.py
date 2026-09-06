"""Чистая часть транскрипции: план нарезки звука и аргументы ffmpeg для одного чанка.

Ни сети, ни базы, ни запуска процессов — только арифметика над временами и списки аргументов.
Сюда же лягут остальные чистые шаги (нормализация ответа провайдера, швы, клэмп, интерполяция
слов): их дешевле проверять на фикстурах за миллисекунды, чем гонять настоящий ffmpeg и провайдера.
"""
from __future__ import annotations

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
