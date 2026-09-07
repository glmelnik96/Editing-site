"""Конвертер одного файла: белый список форматов, аргументы ffmpeg только из таблицы спеки.

Клиент передаёт строку format, не командную строку. Нет libmp3lame — отдельная ошибка с кодом
для 503, а не хвост stderr ffmpeg.
"""
from __future__ import annotations

from server.app.config import Settings
from server.media.run import MediaError, run_tool

FORMATS = ("mp3", "m4a", "wav", "mp4")
MP3_ENCODER = "libmp3lame"
SHORT_SIDE = 1080
# Короткая сторона не больше 1080, кадр меньше не растягиваем (min).
MP4_SCALE = (
    f"scale=w='if(gte(iw,ih),-2,min(iw,{SHORT_SIDE}))'"
    f":h='if(gte(iw,ih),min(ih,{SHORT_SIDE}),-2)'"
)


class ConvertInvalid(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ConvertUnavailable(Exception):
    """Кодека нет в этой сборке ffmpeg — отвечаем 503, не сырым stderr."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def convert_ext(fmt: str) -> str:
    if fmt not in FORMATS:
        raise ConvertInvalid("invalid_format", f"Неизвестный формат: {fmt}")
    return fmt


# Байты в секунду результата: порядок величины, как у рендера. WAV несжатый, mp4 как черновик.
CONVERT_BYTES_PER_SEC = {
    "mp3": 192_000 // 8,
    "m4a": 192_000 // 8,
    "wav": 48_000 * 2 * 2,
    "mp4": 2_000_000 // 8,
}
SIZE_SAFETY = 2


def estimate_convert_bytes(fmt: str, duration: float) -> int:
    rate = CONVERT_BYTES_PER_SEC.get(fmt, CONVERT_BYTES_PER_SEC["mp4"])
    return int(rate * max(duration, 0) * SIZE_SAFETY)


def has_mp3_encoder(settings: Settings) -> bool:
    """Есть ли libmp3lame в этой сборке. Нет — 503, не сырой stderr ffmpeg."""
    try:
        return MP3_ENCODER in run_tool([settings.ffmpeg_path, "-v", "error", "-encoders"], timeout=60)
    except MediaError:
        return False


def build_convert_command(
    settings: Settings,
    src: str,
    dst: str,
    *,
    fmt: str,
    has_audio: bool = True,
    mp3_encoder: bool = True,
) -> list[str]:
    """Список аргументов из белого списка. dst обычно *.part — контейнер задаём явно через -f."""
    if fmt not in FORMATS:
        raise ConvertInvalid("invalid_format", f"Неизвестный формат: {fmt}")
    if fmt == "mp3" and not mp3_encoder:
        raise ConvertUnavailable(
            "encoder_unavailable",
            "В этой сборке ffmpeg нет кодека MP3, выберите m4a или wav",
        )

    args = [
        settings.ffmpeg_path, "-v", "error", "-y",
        "-progress", "pipe:1", "-nostats",
        "-i", src,
    ]
    if fmt == "mp3":
        args += ["-vn", "-c:a", MP3_ENCODER, "-b:a", "192k", "-ar", "44100", "-ac", "2", "-f", "mp3"]
    elif fmt == "m4a":
        args += ["-vn", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-f", "ipod"]
    elif fmt == "wav":
        args += ["-vn", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", "-f", "wav"]
    else:
        args += [
            "-vf", MP4_SCALE,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        ]
        if has_audio:
            args += ["-c:a", "aac", "-b:a", "160k"]
        else:
            args += ["-an"]
        args += ["-movflags", "+faststart", "-f", "mp4"]
    args.append(dst)
    return args
