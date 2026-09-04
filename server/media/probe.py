"""ffprobe: параметры файла. Разбор ответа отделён от запуска, поэтому проверяется на фикстурах."""
from __future__ import annotations

import json
from dataclasses import dataclass

from server.app.config import Settings
from server.media.run import MediaError, run_tool

PROBE_TIMEOUT_SEC = 60


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None

    @property
    def kind(self) -> str:
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


def parse_probe(data: dict) -> MediaInfo:
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and not _is_cover(s)), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise MediaError("no_streams", "В файле нет ни видео, ни звука")
    return MediaInfo(
        duration=_duration(data, video, audio),
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        fps=_fps(video) if video else None,
        has_audio=audio is not None,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
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
