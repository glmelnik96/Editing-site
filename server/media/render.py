"""Сборка команды ffmpeg для рендера проекта (раздел 9.2 спеки).

Функция чистая: ни диска, ни базы, ни запуска процессов. На вход документ проекта и словарь
путей к исходникам, на выход список аргументов. Так самая хрупкая часть сервиса — фильтры,
экранирование и склейка — проверяется на фикстурах за миллисекунды, а не запуском ffmpeg.

Аргументы собираются только из полей документа, прошедшего проверку: клиент не передаёт
ни путей, ни кусков командной строки.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from server.app.config import Settings
from server.app.storage import RENDER_FORMATS
from server.media.timeline import clip_length, clips_duration, fade_into

ASPECT_RATIOS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1)}
AUDIO_RATE = 48000
AUDIO_CHAIN = f"aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
SILENCE = "anullsrc=channel_layout=stereo:sample_rate=48000"
SUBTITLE_STYLE = "FontName=DejaVu Sans,FontSize=24,OutlineColour=&H80000000,BorderStyle=3"

# Качество сборки. Прежние draft и final остаются для агента и старых вызовов: разрешение у них
# из настроек. Остальные выбирает человек в панели и разрешение называет сам.
# crf — для H.264, vp9 — для VP9 (у него своя шкала, 0–63), audio — битрейт звука.
# Пресет x264 у всех не медленнее veryfast: ВМ двухъядерная и общая с соседями, и «высокое»
# добирает качество низким crf, а не часами кодирования.
QUALITY = {
    "draft": {"preset": "ultrafast", "crf": "26", "vp9": "36", "audio": "128k"},
    "final": {"preset": "veryfast", "crf": "20", "vp9": "30", "audio": "160k"},
    "preview": {"preset": "ultrafast", "crf": "30", "vp9": "40", "audio": "96k"},
    "medium": {"preset": "veryfast", "crf": "23", "vp9": "33", "audio": "128k"},
    "high": {"preset": "veryfast", "crf": "18", "vp9": "28", "audio": "192k"},
    "target": {"preset": "veryfast", "crf": "", "vp9": "", "audio": "160k"},
}
# Разрешение по умолчанию, когда его не назвали. У draft и final — из настроек, как было.
DEFAULT_SHORT_SIDE = {"preview": 480, "medium": 720, "high": 1080, "target": 1080}
SHORT_SIDES = (360, 480, 720, 1080, 1440, 2160)
AUDIO_ONLY_BITRATE = "192k"
# Источники субтитров, у которых файла на диске ещё нет: его собирает вызывающий и передаёт путём.
BUILT_SOURCES = ("transcript", "cues")


@dataclass(frozen=True)
class SourceInfo:
    path: str
    duration: float
    has_audio: bool
    still: bool = False
    # Битрейт видео исходника: потолок для «высокого». None — неизвестен (у картинки его нет).
    bit_rate: int | None = None


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


# Отступ угловых наложений от края кадра: доля его ширины, и по вертикали те же пиксели.
OVERLAY_MARGIN = 0.02


def overlay_box(place: str, size: float, width: int, height: int) -> tuple[int, int, str, str]:
    """Коробка наложения: во что его вписать (ширина, высота) и куда поставить (x, y для overlay).

    Наложение вписывается в коробку с сохранением пропорций, поэтому x и y — выражения через его
    итоговые w и h: угловые прижимаются к своему углу, остальные центрируются в коробке. Размер
    значим только у угловых и центра: весь кадр и половины сами задают коробку.
    """
    margin = _even(width * OVERLAY_MARGIN)
    if place == "full":
        return width, height, "(W-w)/2", "(H-h)/2"
    half = _even(width / 2)
    if place == "left":
        return half, height, f"({half}-w)/2", "(H-h)/2"
    if place == "right":
        return half, height, f"{half}+({half}-w)/2", "(H-h)/2"
    box_w, box_h = _even(width * size / 100), _even(height * size / 100)
    if place == "center":
        return box_w, box_h, "(W-w)/2", "(H-h)/2"
    if place not in ("tl", "tr", "bl", "br"):
        raise RenderInvalid(f"неизвестное место наложения: {place}")
    x = str(margin) if place in ("tl", "bl") else f"W-w-{margin}"
    y = str(margin) if place in ("tl", "tr") else f"H-h-{margin}"
    return box_w, box_h, x, y


def render_short_side(quality: str, settings: Settings, short_side: int | None) -> int:
    """Короткая сторона кадра: названная, иначе по качеству, иначе из настроек."""
    if short_side is not None:
        if short_side not in SHORT_SIDES:
            raise RenderInvalid(f"неизвестное разрешение: {short_side}")
        return short_side
    if quality in DEFAULT_SHORT_SIDE:
        return DEFAULT_SHORT_SIDE[quality]
    return settings.final_short_side if quality == "final" else settings.draft_short_side


def render_dimensions(
    doc: dict, *, quality: str, settings: Settings, fmt: str = "mp4", short_side: int | None = None
) -> tuple[int, int] | None:
    """Размер кадра будущего ролика. None — картинки нет вовсе: «только звук»."""
    if fmt == "m4a":
        return None
    return output_size(doc["output"]["aspect"], render_short_side(quality, settings, short_side))


def source_bitrate_cap(clips: list[dict], sources: dict[str, SourceInfo]) -> int | None:
    """Самый большой битрейт среди видеоисходников клипов — потолок для «высокого».

    Картинки не в счёт: битрейта у кадра нет. Неизвестный битрейт тоже: потолок, взятый
    с потолка, резал бы качество ни за что.
    """
    rates = [
        source.bit_rate
        for clip in clips
        if (source := sources.get(clip["asset_id"])) is not None and not source.still and source.bit_rate
    ]
    return max(rates) if rates else None


def _video_codec_args(
    fmt: str, quality: str, preset: dict, bitrate_kbps: int | None, cap: int | None
) -> list[str]:
    """Кодек, режим битрейта и контейнер.

    «Высокое» — постоянное качество (crf) с потолком по исходнику: не хуже того, что сняли, но
    и не раздуваем файл сверх него — 4K-исходник на 40 Мбит/с при выводе в 1080p упёрся бы в
    потолок, а crf сам остановится там, где картинке больше не нужно. «Целевое» — средний
    битрейт с запасом на сложные сцены (maxrate в полтора раза).
    """
    if fmt == "webm":
        speed = "8" if quality in ("draft", "preview") else "6"
        if quality == "target":
            rate = ["-b:v", f"{bitrate_kbps}k"]
        else:
            # У VP9 постоянное качество — это crf вместе с -b:v 0; с числом вместо нуля тот же
            # режим становится «качество, но не больше» — ровно то, что обещает «высокое».
            ceiling = str(cap) if quality == "high" and cap else "0"
            rate = ["-crf", preset["vp9"], "-b:v", ceiling]
        return [
            "-c:v", "libvpx-vp9", *rate, "-deadline", "realtime", "-cpu-used", speed, "-row-mt", "1",
            "-pix_fmt", "yuv420p", "-c:a", "libopus", "-b:a", preset["audio"], "-f", "webm",
        ]
    if quality == "target":
        rate = ["-b:v", f"{bitrate_kbps}k", "-maxrate", f"{bitrate_kbps * 3 // 2}k",
                "-bufsize", f"{bitrate_kbps * 2}k"]
    else:
        rate = ["-crf", preset["crf"]]
        if quality == "high" and cap:
            rate += ["-maxrate", str(cap), "-bufsize", str(cap * 2)]
    return [
        "-c:v", "libx264", "-preset", preset["preset"], *rate,
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", preset["audio"],
        "-movflags", "+faststart",
        # Временный файл называется .part: по такому имени ffmpeg контейнер не угадывает.
        "-f", "mp4",
    ]


def total_duration(doc: dict) -> float:
    # Начальное значение 0.0 держит сумму float: клипы могут прийти с целыми секундами (int),
    # а склейка ниже форматирует длительности строго как "10.0", а не "10".
    # Переход «в» клип укорачивает ролик: xfade перекрывает хвост предыдущего и голову следующего.
    return clips_duration(doc.get("clips") or [])


def _video_chain(width: int, height: int, fit: str, fps: int) -> str:
    if fit == "crop":
        fitting = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    else:
        fitting = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        )
    # settb=AVTB приводит шкалу времени к общей (1/1000000). Без неё xfade между зацикленной
    # картинкой (её шкала 1/30) и видео (1/1000000) отказывается настраиваться: «First input link
    # main timebase do not match». Ставим всем клипам одинаково — разбираться, откуда пришёл
    # кусок, фильтру переходов не должно быть нужно.
    return f"fps={fps},{fitting},setsar=1,settb=AVTB,format=yuv420p"


def _fade_parts(item: dict, span: float) -> list[str]:
    """Появление и затухание звука. Каждое не длиннее половины отрезка: иначе они перекрылись бы."""
    half = span / 2
    fade_in = min(float(item.get("fade_in", 0) or 0), half)
    fade_out = min(float(item.get("fade_out", 0) or 0), half)
    parts: list[str] = []
    if fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        parts.append(f"afade=t=out:st={round(span - fade_out, 3)}:d={fade_out}")
    return parts


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


def _join_clips(
    clips: list[dict], video_out: str, audio_out: str, with_video: bool = True
) -> list[str]:
    """Склеить сегменты: concat встык, xfade+acrossfade на переходах.

    with_video=False — «только звук»: картинку не строим вовсе, иначе ffmpeg декодировал бы
    и масштабировал весь ролик ради того, чтобы выбросить его на выходе.

    Без переходов остаётся одна склейка concat — так же, как до пакета B, чтобы команда
    и тесты микса не менялись. Смешанный список (стык и fade) собирается попарно по порядку.
    """
    count = len(clips)
    if not any(fade_into(clip, index) > 0 for index, clip in enumerate(clips)):
        if not with_video:
            labels = "".join(f"[a{index}]" for index in range(count))
            return [f"{labels}concat=n={count}:v=0:a=1{audio_out}"]
        labels = "".join(f"[v{index}][a{index}]" for index in range(count))
        return [f"{labels}concat=n={count}:v=1:a=1{video_out}{audio_out}"]

    filters: list[str] = []
    video_acc = "[v0]"
    audio_acc = "[a0]"
    acc_len = clip_length(clips[0])
    last = count - 1
    for index in range(1, count):
        fade = fade_into(clips[index], index)
        is_last = index == last
        video_next = video_out if is_last else f"[vx{index}]"
        audio_next = audio_out if is_last else f"[ax{index}]"
        length = clip_length(clips[index])
        if fade > 0:
            offset = round(acc_len - fade, 3)
            if with_video:
                filters.append(
                    f"{video_acc}[v{index}]xfade=transition=fade:duration={fade}:offset={offset}{video_next}"
                )
            filters.append(f"{audio_acc}[a{index}]acrossfade=d={fade}{audio_next}")
            acc_len = round(acc_len + length - fade, 3)
        elif not with_video:
            filters.append(f"{audio_acc}[a{index}]concat=n=2:v=0:a=1{audio_next}")
            acc_len = round(acc_len + length, 3)
        else:
            filters.append(
                f"{video_acc}{audio_acc}[v{index}][a{index}]concat=n=2:v=1:a=1{video_next}{audio_next}"
            )
            acc_len = round(acc_len + length, 3)
        video_acc = video_next
        audio_acc = audio_next
    return filters


def _subtitles_file(
    subtitles: dict, sources: dict[str, SourceInfo], subtitles_path: Path | None
) -> str:
    """Откуда взять файл субтитров. Вжигание и мягкая дорожка дальше работают одинаково: источник
    решает только это.

    У загруженного файла он лежит рядом с ассетом, а реплики — из транскрипта или вычитанные
    человеком — пишет в кэш вызывающий: сборщик команды на диск не ходит и написать этот файл
    не может.
    """
    if subtitles.get("source") in BUILT_SOURCES:
        if subtitles_path is None:
            raise RenderInvalid(
                "субтитры из реплик не собраны: вызывающий не передал subtitles_path"
            )
        return str(subtitles_path)
    source = sources.get(subtitles["asset_id"])
    if source is None:
        raise RenderInvalid(f"ассет субтитров {subtitles['asset_id']} недоступен")
    return source.path


def build_render_command(
    doc: dict,
    *,
    sources: dict[str, SourceInfo],
    quality: str,
    settings: Settings,
    out_path: str,
    subtitles_path: Path | None = None,
    fmt: str = "mp4",
    short_side: int | None = None,
    bitrate_kbps: int | None = None,
) -> list[str]:
    """Полная командная строка ffmpeg для сборки проекта."""
    preset = QUALITY.get(quality)
    if preset is None:
        raise RenderInvalid(f"неизвестное качество: {quality}")
    if fmt not in RENDER_FORMATS:
        raise RenderInvalid(f"неизвестный формат: {fmt}")
    if quality == "target" and not bitrate_kbps:
        raise RenderInvalid("для целевого качества нужен битрейт")
    audio_only = fmt == "m4a"
    clips = doc.get("clips") or []
    if not clips:
        raise RenderInvalid("в проекте нет клипов")

    output = doc["output"]
    size = render_dimensions(doc, quality=quality, settings=settings, fmt=fmt, short_side=short_side)
    total = total_duration(doc)
    video_chain = _video_chain(size[0], size[1], output["fit"], int(output["fps"])) if size else ""

    args: list[str] = [settings.ffmpeg_path, "-v", "error", "-y", "-progress", "pipe:1", "-nostats"]
    filters: list[str] = []
    index = 0

    for number, clip in enumerate(clips):
        source = sources.get(clip["asset_id"])
        if source is None:
            raise RenderInvalid(f"ассет {clip['asset_id']} недоступен")
        start = float(clip["in"])
        # float(): клип может прийти с целыми секундами (int), а ffmpeg-аргумент нужен как "1.0".
        length = round(float(clip["out"]) - start, 3)
        if source.still:
            # Картинку не мотают, её повторяют: -loop 1 крутит единственный кадр, -t говорит
            # сколько. Смещение -ss тут смысла не имеет — кадр один и тот же в любой момент.
            args += ["-loop", "1", "-t", str(length), "-i", source.path]
        else:
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
        if not audio_only:
            filters.append(f"[{video_input}:v]{video_chain}[v{number}]")
        volume = float(clip.get("volume", 1))
        audio_chain = AUDIO_CHAIN if volume == 1 else f"{AUDIO_CHAIN},volume={volume}"
        filters.append(f"[{audio_input}:a]{audio_chain}[a{number}]")

    music = doc.get("music")
    subtitles = doc.get("subtitles")
    if not isinstance(subtitles, dict) or subtitles.get("enabled") is False:
        subtitles = None

    video_out, audio_out = "[v]", "[a]"
    filters.extend(_join_clips(clips, video_out, audio_out, with_video=not audio_only))

    # Звуковая дорожка вливается в речь до музыки: озвучка — такая же речь, и приглушение
    # музыки должно слышать и её. duration=first держит длину по склейке клипов, поэтому звук,
    # свисающий за конец ролика, обрезается здесь, а не делает ролик длиннее картинки.
    sound_labels: list[str] = []
    # Наложения: картинка или видео поверх собранной основы, каждое в коробке своего пресета и
    # только в своём окне времени. Идут до субтитров — те вжигаются поверх всего. Звук
    # видеоналожения по умолчанию выключен; включённый вливается в речь вместе со звуками дорожки.
    for number, overlay in enumerate(doc.get("overlays") or []):
        source = sources.get(overlay["asset_id"])
        if source is None:
            raise RenderInvalid(f"ассет наложения {overlay['asset_id']} недоступен")
        start = float(overlay["in"])
        length = round(float(overlay["out"]) - start, 3)
        at = float(overlay["at"])
        volume = float(overlay.get("volume", 0) or 0)
        with_audio = volume > 0 and source.has_audio and not source.still
        if audio_only and not with_audio:
            continue  # ни картинки, ни звука: вход был бы лишним
        if source.still:
            # Картинку крутим кадрами с частотой ролика ровно столько, сколько она висит.
            fps = str(int(output["fps"]))
            args += ["-loop", "1", "-framerate", fps, "-t", str(length), "-i", source.path]
        else:
            args += ["-ss", str(start), "-t", str(length), "-i", source.path]
        overlay_input = index
        index += 1
        if not audio_only and size is not None:
            box_w, box_h, x, y = overlay_box(overlay["place"], float(overlay["size"]), size[0], size[1])
            parts = [
                f"scale={box_w}:{box_h}:force_original_aspect_ratio=decrease:force_divisible_by=2",
                "setsar=1",
                "format=yuva420p",
            ]
            fade_in = float(overlay.get("fade_in", 0) or 0)
            fade_out = float(overlay.get("fade_out", 0) or 0)
            if fade_in > 0:
                parts.append(f"fade=t=in:st=0:d={fade_in}:alpha=1")
            if fade_out > 0:
                parts.append(f"fade=t=out:st={round(length - fade_out, 3)}:d={fade_out}:alpha=1")
            # Сдвиг на своё время: кадры наложения совпадают с кадрами основы, а окно enable
            # решает, когда его рисовать. eof_action=pass: наложение кончилось — основа идёт дальше.
            parts.append(f"setpts=PTS+{at}/TB")
            filters.append(f"[{overlay_input}:v]{','.join(parts)}[o{number}]")
            filters.append(
                f"{video_out}[o{number}]overlay={x}:{y}:eof_action=pass"
                f":enable='between(t,{at},{round(at + length, 3)})'[vo{number}]"
            )
            video_out = f"[vo{number}]"
        if with_audio:
            delay = round(at * 1000)
            chain = AUDIO_CHAIN if volume == 1 else f"{AUDIO_CHAIN},volume={volume}"
            filters.append(f"[{overlay_input}:a]{chain},adelay={delay}|{delay}[oa{number}]")
            sound_labels.append(f"[oa{number}]")
    ducked: list[str] = []  # фон под приглушение: под речью он тише
    for number, sound in enumerate(doc.get("sounds") or []):
        source = sources.get(sound["asset_id"])
        if source is None:
            raise RenderInvalid(f"звуковой ассет {sound['asset_id']} недоступен")
        if not source.has_audio:
            continue  # у видеозаписи без звука брать нечего, а [N:a] уронил бы сборку
        start = float(sound["in"])
        length = round(float(sound["out"]) - start, 3)
        at = float(sound["at"])
        loop = bool(sound.get("loop"))
        # Сколько звук занимает на шкале: кусок, а по кругу — до конца ролика.
        span = round(total - at, 3) if loop else length
        if span <= 0:
            continue  # звук по кругу, положенный за конец ролика: слышать его нечему
        args += ["-ss", str(start), "-t", str(length), "-i", source.path]
        sound_input = index
        index += 1
        parts = [AUDIO_CHAIN]
        if loop:
            # aloop повторяет первые size отсчётов без конца, atrim обрезает по концу ролика.
            # -stream_loop здесь не годится: с -ss он повторял бы файл целиком, а не кусок.
            parts.append(f"aloop=loop=-1:size={round(length * AUDIO_RATE)}")
            parts.append(f"atrim=0:{span}")
        parts.extend(_fade_parts(sound, span))
        volume = float(sound.get("volume", 1))
        if volume != 1:
            parts.append(f"volume={volume}")
        # Задержка на обе стороны стерео: AUDIO_CHAIN уже привёл звук к двум каналам.
        delay = round(at * 1000)
        parts.append(f"adelay={delay}|{delay}")
        filters.append(f"[{sound_input}:a]{','.join(parts)}[s{number}]")
        (ducked if sound.get("duck") else sound_labels).append(f"[s{number}]")
    if sound_labels:
        filters.append(
            f"{audio_out}{''.join(sound_labels)}"
            f"amix=inputs={len(sound_labels) + 1}:duration=first:normalize=0[speech]"
        )
        audio_out = "[speech]"
    if ducked:
        # Фон сначала сводится в одно, потом приглушается речью: так и один компрессор, и общий
        # уровень фона, а не гонка нескольких. Речь раздваивается: одна копия управляет, другая
        # идёт в микс — одна метка на два входа ffmpeg 6.1 не даёт.
        if len(ducked) == 1:
            background = ducked[0]
        else:
            filters.append(f"{''.join(ducked)}amix=inputs={len(ducked)}:duration=longest:normalize=0[bg]")
            background = "[bg]"
        filters.append(f"{audio_out}asplit=2[bg_sc][bg_mix]")
        filters.append(
            f"{background}[bg_sc]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400[bg_duck]"
        )
        filters.append("[bg_mix][bg_duck]amix=inputs=2:duration=first:normalize=0[bgmixed]")
        audio_out = "[bgmixed]"

    if music:
        source = sources.get(music["asset_id"])
        if source is None:
            raise RenderInvalid(f"музыкальный ассет {music['asset_id']} недоступен")
        if music.get("loop", True):
            args += ["-stream_loop", "-1"]
        args += ["-i", source.path]
        music_input = index
        index += 1
        speech = audio_out
        speech_volume = float(music.get("speech_volume", 1))
        if speech_volume != 1:
            filters.append(f"{speech}volume={speech_volume}[spk]")
            speech = "[spk]"
        filters.append(f"[{music_input}:a]{_music_chain(music, total)}[music]")
        if music.get("duck"):
            # Одна метка — один вход. Повтор [speech] на ffmpeg 6.1 читается как
            # stream specifier и роняет сборку, если ползунок «Речь» не единица.
            filters.append(f"{speech}asplit=2[spk_sc][spk_mix]")
            filters.append(
                "[music][spk_sc]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400[duck]"
            )
            filters.append("[spk_mix][duck]amix=inputs=2:duration=first:normalize=0[amixed]")
        else:
            filters.append(f"{speech}[music]amix=inputs=2:duration=first:normalize=0[amixed]")
        audio_out = "[amixed]"

    subtitle_input: int | None = None
    # У «только звука» нет кадра, куда вжигать, а мягкую дорожку m4a не держит.
    if subtitles and not audio_only:
        subtitle_file = _subtitles_file(subtitles, sources, subtitles_path)
        if subtitles["mode"] == "burn":
            escaped = escape_for_filter(subtitle_file)
            filters.append(f"{video_out}subtitles='{escaped}':force_style='{SUBTITLE_STYLE}'[vsub]")
            video_out = "[vsub]"
        else:
            args += ["-i", subtitle_file]
            subtitle_input = index
            index += 1

    args += ["-filter_complex", ";".join(filters)]
    if audio_only:
        return [*args, "-map", audio_out, "-vn", "-c:a", "aac", "-b:a", AUDIO_ONLY_BITRATE,
                "-f", "ipod", out_path]
    args += ["-map", video_out, "-map", audio_out]
    if subtitle_input is not None:
        # Мягкие субтитры в webm — это WebVTT: mov_text живёт только в mp4.
        codec = "webvtt" if fmt == "webm" else "mov_text"
        args += ["-map", f"{subtitle_input}:s", "-c:s", codec, "-metadata:s:s:0", "language=rus"]
    cap = source_bitrate_cap(clips, sources) if quality == "high" else None
    return [*args, *_video_codec_args(fmt, quality, preset, bitrate_kbps, cap), out_path]
