"""Сборка команды ffmpeg для рендера проекта (раздел 9.2 спеки).

Функция чистая: ни диска, ни базы, ни запуска процессов. На вход документ проекта и словарь
путей к исходникам, на выход список аргументов. Так самая хрупкая часть сервиса — фильтры,
экранирование и склейка — проверяется на фикстурах за миллисекунды, а не запуском ffmpeg.

Аргументы собираются только из полей документа, прошедшего проверку: клиент не передаёт
ни путей, ни кусков командной строки.
"""
from __future__ import annotations

from dataclasses import dataclass

from server.app.config import Settings

ASPECT_RATIOS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1)}
AUDIO_CHAIN = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
SILENCE = "anullsrc=channel_layout=stereo:sample_rate=48000"
SUBTITLE_STYLE = "FontName=DejaVu Sans,FontSize=24,OutlineColour=&H80000000,BorderStyle=3"

QUALITY = {
    "draft": {"preset": "ultrafast", "crf": "26", "audio": "128k"},
    "final": {"preset": "veryfast", "crf": "20", "audio": "160k"},
}


@dataclass(frozen=True)
class SourceInfo:
    path: str
    duration: float
    has_audio: bool


class RenderInvalid(Exception):
    """Документ нельзя собрать: пропал ассет, неизвестное качество, неподдержанный случай."""


def escape_for_filter(path: str) -> str:
    """Путь внутри значения фильтра: обратный слэш, двоеточие и апостроф там особые."""
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _even(value: float) -> int:
    """Чётный размер: yuv420p не кодируется при нечётной стороне."""
    return max(2, round(value / 2) * 2)


def output_size(aspect: str, short_side: int) -> tuple[int, int]:
    """Разрешение из пропорции и короткой стороны кадра."""
    ratio = ASPECT_RATIOS.get(aspect)
    if ratio is None:
        raise RenderInvalid(f"неизвестная пропорция: {aspect}")
    width_ratio, height_ratio = ratio
    if width_ratio >= height_ratio:
        return _even(short_side * width_ratio / height_ratio), _even(short_side)
    return _even(short_side), _even(short_side * height_ratio / width_ratio)


def total_duration(doc: dict) -> float:
    # Начальное значение 0.0 держит сумму float: клипы могут прийти с целыми секундами (int),
    # а склейка ниже форматирует длительности строго как "10.0", а не "10".
    return round(sum((c["out"] - c["in"] for c in doc.get("clips") or []), 0.0), 3)


def _video_chain(width: int, height: int, fit: str, fps: int) -> str:
    if fit == "crop":
        fitting = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    else:
        fitting = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        )
    return f"fps={fps},{fitting},setsar=1,format=yuv420p"


def _music_chain(music: dict, total: float) -> str:
    """Музыка: обрезать по длине ролика, приглушить, при желании затушевать края.

    Затухания не могут перекрыть друг друга: у короткого ролика каждое не длиннее половины.
    """
    half = total / 2
    fade_in = min(float(music.get("fade_in", 0) or 0), half)
    fade_out = min(float(music.get("fade_out", 0) or 0), half)
    parts = [AUDIO_CHAIN, f"atrim=0:{total}"]
    if fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        parts.append(f"afade=t=out:st={round(total - fade_out, 3)}:d={fade_out}")
    parts.append(f"volume={music['volume']}")
    return ",".join(parts)


def build_render_command(
    doc: dict,
    *,
    sources: dict[str, SourceInfo],
    quality: str,
    settings: Settings,
    out_path: str,
) -> list[str]:
    """Полная командная строка ffmpeg для сборки проекта."""
    preset = QUALITY.get(quality)
    if preset is None:
        raise RenderInvalid(f"неизвестное качество: {quality}")
    clips = doc.get("clips") or []
    if not clips:
        raise RenderInvalid("в проекте нет клипов")

    output = doc["output"]
    short_side = settings.final_short_side if quality == "final" else settings.draft_short_side
    width, height = output_size(output["aspect"], short_side)
    total = total_duration(doc)
    video_chain = _video_chain(width, height, output["fit"], int(output["fps"]))

    args: list[str] = [settings.ffmpeg_path, "-v", "error", "-y", "-progress", "pipe:1", "-nostats"]
    filters: list[str] = []
    concat_labels: list[str] = []
    index = 0

    for number, clip in enumerate(clips):
        source = sources.get(clip["asset_id"])
        if source is None:
            raise RenderInvalid(f"ассет {clip['asset_id']} недоступен")
        start = float(clip["in"])
        # float(): клип может прийти с целыми секундами (int), а ffmpeg-аргумент нужен как "1.0".
        length = round(float(clip["out"]) - start, 3)
        # -ss перед -i: поиск идёт по смещению до декодирования, полминуты из пятигигабайтной
        # записи вырезаются мгновенно.
        args += ["-ss", str(start), "-t", str(length), "-i", source.path]
        video_input = index
        index += 1
        if source.has_audio:
            audio_input = video_input
        else:
            args += ["-f", "lavfi", "-t", str(length), "-i", SILENCE]
            audio_input = index
            index += 1
        filters.append(f"[{video_input}:v]{video_chain}[v{number}]")
        filters.append(f"[{audio_input}:a]{AUDIO_CHAIN}[a{number}]")
        concat_labels.append(f"[v{number}][a{number}]")

    music = doc.get("music")
    subtitles = doc.get("subtitles")
    if subtitles and subtitles.get("source") == "transcript":
        raise RenderInvalid("субтитры из транскрипта появятся в M4")

    video_out, audio_out = "[v]", "[a]"
    filters.append(f"{''.join(concat_labels)}concat=n={len(clips)}:v=1:a=1{video_out}{audio_out}")

    if music:
        source = sources.get(music["asset_id"])
        if source is None:
            raise RenderInvalid(f"музыкальный ассет {music['asset_id']} недоступен")
        if music.get("loop", True):
            args += ["-stream_loop", "-1"]
        args += ["-i", source.path]
        music_input = index
        index += 1
        filters.append(f"[{music_input}:a]{_music_chain(music, total)}[music]")
        filters.append(f"{audio_out}[music]amix=inputs=2:duration=first:normalize=0[amixed]")
        audio_out = "[amixed]"

    subtitle_input: int | None = None
    if subtitles:
        source = sources.get(subtitles["asset_id"])
        if source is None:
            raise RenderInvalid(f"ассет субтитров {subtitles['asset_id']} недоступен")
        if subtitles["mode"] == "burn":
            escaped = escape_for_filter(source.path)
            filters.append(f"{video_out}subtitles='{escaped}':force_style='{SUBTITLE_STYLE}'[vsub]")
            video_out = "[vsub]"
        else:
            args += ["-i", source.path]
            subtitle_input = index
            index += 1

    args += ["-filter_complex", ";".join(filters), "-map", video_out, "-map", audio_out]
    if subtitle_input is not None:
        args += ["-map", f"{subtitle_input}:s", "-c:s", "mov_text", "-metadata:s:s:0", "language=rus"]
    args += [
        "-c:v", "libx264", "-preset", preset["preset"], "-crf", preset["crf"],
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", preset["audio"],
        "-movflags", "+faststart",
        # Временный файл называется .part: по такому имени ffmpeg контейнер не угадывает.
        "-f", "mp4", out_path,
    ]
    return args
