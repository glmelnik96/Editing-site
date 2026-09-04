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


def proxy_name(kind: str) -> str:
    if kind == "video":
        return "proxy.mp4"
    if kind == "audio":
        return "proxy.m4a"
    raise ValueError(f"нет прокси для вида {kind}")


def scale_filter(long_side: int) -> str:
    """Длинная сторона в long_side, короткая пропорционально и чётной (-2)."""
    return f"scale=w='if(gte(iw,ih),{long_side},-2)':h='if(gte(iw,ih),-2,{long_side})'"


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
    args += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-movflags", "+faststart", dst]
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
