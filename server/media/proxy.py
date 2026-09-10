"""Прокси для плеера: H.264 640 px с частыми ключевыми кадрами или AAC для звука.

Короткий интервал ключевых кадров нужен для точной перемотки: подрезка клипа в браузере должна
попадать в нужный кадр (раздел 7 спеки).
"""
from __future__ import annotations

from server.app.config import Settings

GOP = 30
CRF = "28"
AUDIO_BITRATE = "96k"
PRESET = "veryfast"

# вид ассета → (имя файла прокси, формат контейнера для ffmpeg -f).
# Формат нужен явно: кодирование идёт во временный файл с суффиксом .part (см. handle_proxy),
# а по такому расширению ffmpeg не может выбрать контейнер сам («Unable to choose an output format»).
PROXY_BY_KIND = {
    "video": ("proxy.mp4", "mp4"),
    "audio": ("proxy.m4a", "ipod"),
    # Прокси картинки — неподвижное видео, а не JPEG. Сцена редактора целиком стоит на двух
    # элементах video: их currentTime — это часы склейки, а переход — их прозрачность. Картинка
    # в виде ролика проходит по этому пути без единого частного случая, а для отдельного <img>
    # пришлось бы заводить искусственные часы в самом хрупком месте редактора.
    "image": ("proxy.mp4", "mp4"),
}
# Кадров в секунду у неподвижного прокси. Больше незачем: картинка не меняется, а часы
# склейки браузер ведёт по времени, а не по кадрам. Пять кадров — секунды на кодирование.
STILL_FPS = 5
# Ключевой кадр раз в 50 секунд. Перемотка к любому месту декодирует от ближайшего ключевого,
# но у неподвижной картинки промежуточные кадры пустые и декодируются мгновенно, а файл на
# 600 секунд весит 210 КБ вместо 700 при ключевом кадре раз в 10 (замерено).
STILL_GOP = 250


def proxy_name(kind: str) -> str:
    if kind not in PROXY_BY_KIND:
        raise ValueError(f"нет прокси для вида {kind}")
    return PROXY_BY_KIND[kind][0]


def scale_filter(long_side: int) -> str:
    """Длинная сторона до long_side, короткая пропорционально и чётной (-2).

    min() не даёт увеличивать кадр: прокси из ролика меньше long_side весил бы больше исходника
    и кодировался бы дольше без выигрыша в качестве.
    """
    return (
        f"scale=w='if(gte(iw,ih),min(iw,{long_side}),-2)'"
        f":h='if(gte(iw,ih),-2,min(ih,{long_side}))'"
    )


def proxy_args(settings: Settings, src: str, dst: str, *, kind: str) -> list[str]:
    args = [
        settings.ffmpeg_path, "-v", "error", "-y",
        "-progress", "pipe:1", "-nostats",
        "-i", src,
    ]
    if kind == "image":
        # Длина прокси — предел, сколько картинка может висеть в кадре: клип длиннее проверка
        # документа не пропустит, и сцене всегда хватит ролика. Звука у картинки нет (-an).
        return [
            settings.ffmpeg_path, "-v", "error", "-y",
            "-progress", "pipe:1", "-nostats",
            "-loop", "1", "-framerate", str(STILL_FPS), "-t", str(settings.max_still_sec),
            "-i", src,
            "-vf", f"{scale_filter(settings.proxy_long_side)},format=yuv420p",
            "-c:v", "libx264", "-preset", PRESET, "-tune", "stillimage", "-crf", CRF,
            "-g", str(STILL_GOP), "-an", "-movflags", "+faststart",
            "-f", PROXY_BY_KIND[kind][1], dst,
        ]
    if kind == "video":
        args += [
            "-vf", scale_filter(settings.proxy_long_side),
            "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
            "-g", str(GOP), "-keyint_min", str(GOP), "-sc_threshold", "0",
            "-pix_fmt", "yuv420p",
        ]
    else:
        args += ["-vn"]
    args += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-movflags", "+faststart"]
    args += ["-f", PROXY_BY_KIND[kind][1], dst]
    return args


def parse_progress(line: str, *, total: float) -> float | None:
    """Доля выполнения из строки -progress. Возвращает None, если строка не про время."""
    key, _, value = line.strip().partition("=")
    if key != "out_time_us" or total <= 0:
        return None
    try:
        micros = int(value)
    except ValueError:
        return None
    return min(1.0, max(0.0, micros / 1_000_000 / total))
