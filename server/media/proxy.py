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
}


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
