"""Переупаковка контейнера без перекодирования.

WebM, записанный прямо в браузере (MediaRecorder: запись экрана или камеры на сайте, расширения),
пишется потоком: длительность и индекс в файл не попадают, и ffprobe отвечает «длительность
неизвестна». Перекодировать такой файл незачем — достаточно переписать контейнер: ffmpeg с
-c copy в обычный файл ставит и длительность, и индекс. Это копия, а не кодирование: секунды даже
на большом файле.
"""
from __future__ import annotations

from server.app.config import Settings

# Семейство Matroska: так ffprobe называет и .mkv, и .webm («matroska,webm»).
MATROSKA = "matroska"


def can_remux(container: str) -> bool:
    """Поможет ли переупаковка. Только Matroska: длительность теряет запись потоком в WebM и MKV.
    Другой контейнер под тем же расширением стал бы Matroska — это запутало бы, а случай не встречался."""
    return MATROSKA in {name.strip() for name in container.split(",")}


def remux_args(settings: Settings, src: str, dst: str) -> list[str]:
    """Все дорожки как есть, контейнер Matroska. dst обычно *.part — формат задаём явно через -f.

    Пишем Matroska, а не WebM: браузер умеет записывать в .webm и H.264, а WebM-муксер такой файл
    не примет. Исходник наружу не отдаётся, его читает только ffmpeg — по содержимому, не по имени.
    """
    return [
        settings.ffmpeg_path, "-v", "error", "-y", "-i", src,
        "-map", "0", "-c", "copy", "-f", "matroska", dst,
    ]
