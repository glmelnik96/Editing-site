"""ffprobe: параметры файла. Разбор ответа отделён от запуска, поэтому проверяется на фикстурах."""
from __future__ import annotations

import json
from dataclasses import dataclass

from server.app.config import Settings
from server.media.run import MediaError, run_tool

PROBE_TIMEOUT_SEC = 60


# Контейнеры одиночной картинки. Всё, что оканчивается на «_pipe» (png_pipe, webp_pipe,
# bmp_pipe, tiff_pipe), — тоже они: ffmpeg читает такой файл как поток одного кадра.
# gif сюда попадает условно: он бывает и анимацией, её отличает число кадров.
IMAGE_FORMATS = {"image2", "image2pipe", "gif"}


@dataclass(frozen=True)
class MediaInfo:
    duration: float | None
    width: int | None
    height: int | None
    fps: float | None
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None
    bit_rate: int | None = None
    still: bool = False

    @property
    def kind(self) -> str:
        if self.still:
            return "image"
        return "video" if self.video_codec else "audio"


def _fps(stream: dict) -> float | None:
    """avg_frame_rate вида «30000/1001»; «0/0» у обложек и части контейнеров."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = str(stream.get(key) or "")
        if "/" not in value:
            continue
        num, den = value.split("/", 1)
        try:
            num_f, den_f = float(num), float(den)
        except ValueError:
            continue
        if den_f > 0 and num_f > 0:
            return round(num_f / den_f, 3)
    return None


def _is_cover(stream: dict) -> bool:
    """Обложка звукового файла приходит видеопотоком: у неё стоит attached_pic."""
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def _duration(data: dict, video: dict | None, audio: dict | None) -> float:
    for source in (data.get("format") or {}, video or {}, audio or {}):
        raw = source.get("duration")
        if raw in (None, "", "N/A"):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return round(value, 3)
    raise MediaError("no_duration", "Не удалось определить длительность файла")


def _frames(stream: dict) -> int | None:
    raw = stream.get("nb_frames")
    if raw in (None, "", "N/A"):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_still(fmt: dict, video: dict | None, audio: dict | None) -> bool:
    """Одиночная картинка, а не ролик.

    По длительности не отличить: JPEG приходит с честными на вид 0.04 секунды — это один кадр
    при 25 к/с, — а PNG и WebP не приходят с ней вовсе. Отличает контейнер. Единственный
    двусмысленный — gif: анимация в нём живёт кадрами, поэтому там смотрим на их число.
    """
    if video is None or audio is not None:
        return False
    names = {name.strip() for name in str(fmt.get("format_name") or "").split(",")}
    if not (names & IMAGE_FORMATS or any(name.endswith("_pipe") for name in names)):
        return False
    frames = _frames(video)
    return frames is None or frames <= 1


def _bit_rate(fmt: dict, video: dict | None) -> int | None:
    """Битрейт исходника. У видеопотока он точнее — без звука и обвязки контейнера, — но в
    mkv и webm его обычно нет, и тогда берём общий по файлу."""
    for source in (video or {}, fmt):
        raw = source.get("bit_rate")
        if raw in (None, "", "N/A"):
            continue
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def parse_probe(data: dict) -> MediaInfo:
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and not _is_cover(s)), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise MediaError("no_streams", "В файле нет ни видео, ни звука")
    fmt = data.get("format") or {}
    still = _is_still(fmt, video, audio)
    return MediaInfo(
        # У картинки длительности нет: сколько она висит в кадре, решают на шкале. Битрейт
        # одного кадра тоже ничего не значит — «6 Мбит/с» у JPEG это его вес, делённый на 0.04 с.
        duration=None if still else _duration(data, video, audio),
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        fps=None if still else (_fps(video) if video else None),
        has_audio=audio is not None,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        bit_rate=None if still else _bit_rate(fmt, video),
        still=still,
    )


def probe_args(settings: Settings, path: str) -> list[str]:
    return [
        settings.ffprobe_path, "-v", "error",
        "-print_format", "json", "-show_format", "-show_streams", path,
    ]


def probe_file(settings: Settings, path: str) -> MediaInfo:
    raw = run_tool(probe_args(settings, path), timeout=PROBE_TIMEOUT_SEC)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MediaError("bad_probe", "ffprobe вернул не JSON") from exc
    return parse_probe(data)
