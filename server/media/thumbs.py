"""Полоска кадров: один спрайт JPEG плюс раскладка в JSON.

Кадры отбираются фильтром fps (равномерно по времени), а не по ключевым кадрам: с fps интервал
предсказуем, а декодирование мелкого потока стоит недорого. Неполная сетка тоже отдаётся целиком,
пустые клетки чёрные, поэтому клиент считает координату кадра прямо из номера.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from server.app.config import Settings


@dataclass(frozen=True)
class GridLayout:
    count: int
    cols: int
    rows: int
    interval: float
    frame_width: int
    frame_height: int
    still: bool = False


def grid_layout(settings: Settings, *, duration: float, width: int | None, height: int | None) -> GridLayout:
    """Сколько кадров, с каким шагом и какой сеткой. Кадров не больше thumb_max_frames:
    у длинного файла шаг растягивается, а не растёт число кадров."""
    interval = settings.thumb_interval_sec
    count = max(1, math.ceil(max(duration, 0.0) / interval))
    if count > settings.thumb_max_frames:
        count = settings.thumb_max_frames
        interval = round(max(duration, 0.0) / count, 3)
    cols = settings.thumb_cols
    rows = math.ceil(count / cols)
    fw = settings.thumb_width
    ratio = (height / width) if width and height else 9 / 16
    fh = max(2, round(fw * ratio / 2) * 2)  # чётная высота: scale=-2 округляет так же
    return GridLayout(count=count, cols=cols, rows=rows, interval=interval, frame_width=fw, frame_height=fh)


def still_layout(settings: Settings, *, width: int | None, height: int | None) -> GridLayout:
    """Раскадровка картинки: одна клетка.

    Все кадры картинки одинаковые, делить её не на что. Клиент берёт номер клетки как время,
    делённое на шаг, и зажимает его в count − 1 — поэтому любой момент клипа показывает эту
    единственную клетку, и шкала с карточкой рисуют картинку тем же кодом, что и ролик.
    Сетку в одну колонку ставим сами: общая дала бы спрайт в десять клеток, девять из них чёрные.
    """
    base = grid_layout(settings, duration=0.0, width=width, height=height)
    return GridLayout(
        count=1, cols=1, rows=1, interval=base.interval,
        frame_width=base.frame_width, frame_height=base.frame_height, still=True,
    )


def thumbs_args(settings: Settings, src: str, dst: str, layout: GridLayout) -> list[str]:
    # У картинки кадр один, и фильтр fps его выбрасывает: у кадра нет длительности, отбирать по
    # времени нечего — ffmpeg отвечает «Nothing was written… received no packets» (png, jpg).
    pick = "" if layout.still else f"fps=1/{layout.interval},"
    chain = f"{pick}scale={layout.frame_width}:-2,tile={layout.cols}x{layout.rows}"
    return [
        settings.ffmpeg_path, "-v", "error", "-y", "-i", src,
        "-vf", chain, "-frames:v", "1", "-q:v", "5", dst,
    ]


def thumbs_meta(layout: GridLayout) -> dict:
    return {
        "count": layout.count,
        "cols": layout.cols,
        "rows": layout.rows,
        "interval": layout.interval,
        "width": layout.frame_width,
        "height": layout.frame_height,
    }
