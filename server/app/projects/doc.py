"""Проверка и нормализация документа проекта (раздел 4 спеки).

Чистые функции: ни базы, ни диска. О состоянии ассетов знают только через словарь AssetInfo,
который собирает вызывающий. Ошибки копятся списком, чтобы клиент увидел сразу все проблемы,
а не исправлял их по одной.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

from server.app.config import Settings
from server.media.timeline import clips_duration

ASPECTS = ("16:9", "9:16", "1:1")
FITS = ("pad", "crop")
FPS_VALUES = (25, 30, 50, 60)
SUB_SOURCES = ("file", "transcript", "cues")
SUB_MODES = ("burn", "soft")
SUB_STYLES = ("default",)
CLIP_READY_STATUSES = ("ready", "proxy_ready")
# Что можно положить в клип. У картинки своей длительности нет: сколько она висит в кадре,
# решают при добавлении, поэтому верхней границей ей служит не длительность файла, а предел
# из настроек — иначе один кадр растянули бы на весь допустимый ролик.
CLIP_KINDS = ("video", "image")
# Чем можно озвучить: звуковой файл или звук видеозаписи — у последней берётся только дорожка.
SOUND_KINDS = ("audio", "video")
TRANSITION_KINDS = ("fade",)
TIME_DIGITS = 3
MAX_CLIP_ID = 64  # идентификатор клипа хранится в документе: без предела клиент раздует его сотней клипов
MAX_CUE_TEXT = 200  # реплика длиннее не влезет в кадр ни при какой пропорции — это уже не субтитр
MAX_CUE_LINES = 2  # под две строки свёрстан кадр (спека §5.3)


@dataclass(frozen=True)
class AssetInfo:
    kind: str
    status: str
    duration: float | None


class ProjectInvalid(Exception):
    def __init__(self, errors: list[dict]) -> None:
        super().__init__("документ проекта не прошёл проверку")
        self.errors = errors


class _Errors:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, field: str, message: str) -> None:
        self.items.append({"field": field, "message": message})

    def __bool__(self) -> bool:
        return bool(self.items)


def _number(value: object) -> float | None:
    """Числом считаем int и float, но не bool и не строку: «1» в поле времени — ошибка клиента.

    NaN и бесконечность отвергаем: сравнения с ними всегда ложны, поэтому такое значение прошло бы
    все проверки границ и осело бы в хранимом документе токеном, который не разберёт ни один
    строгий разборщик JSON, включая браузерный.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _round(value: float) -> float:
    return round(value, TIME_DIGITS)


def _validate_output(raw: object, errors: _Errors) -> dict:
    out = {"aspect": "16:9", "fit": "pad", "fps": 30}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        errors.add("output", "output должен быть объектом")
        return out
    aspect = raw.get("aspect", out["aspect"])
    if aspect not in ASPECTS:
        errors.add("output.aspect", f"aspect: {', '.join(ASPECTS)}")
    else:
        out["aspect"] = aspect
    fit = raw.get("fit", out["fit"])
    if fit not in FITS:
        errors.add("output.fit", f"fit: {', '.join(FITS)}")
    else:
        out["fit"] = fit
    fps = raw.get("fps", out["fps"])
    if fps not in FPS_VALUES:
        errors.add("output.fps", f"fps: {', '.join(str(v) for v in FPS_VALUES)}")
    else:
        out["fps"] = int(fps)
    return out


def _validate_clip_time(
    where: str, raw: dict, asset: AssetInfo | None, settings: Settings, errors: _Errors,
) -> tuple[float | None, float | None]:
    """Проверяет in и out по отдельности, потом — их разницу.

    in и out проверяются раздельно (число, границы), чтобы отрицательный in не мешал
    заметить, что out тоже вышел за длительность ассета — иначе клиент видел бы ошибки
    по одной за раз, а не все сразу.
    """
    start = _number(raw.get("in"))
    end = _number(raw.get("out"))

    start_ok = start is not None
    if not start_ok:
        errors.add(f"{where}.in", "in должен быть числом секунд")
    elif start < 0:
        errors.add(f"{where}.in", "in не может быть отрицательным")
        start_ok = False

    end_ok = end is not None
    if not end_ok:
        errors.add(f"{where}.out", "out должен быть числом секунд")
    elif asset is not None and asset.kind == "image":
        if end > settings.max_still_sec + 1e-6:
            errors.add(f"{where}.out", f"картинка держится в кадре не дольше {settings.max_still_sec} с")
            end_ok = False
    elif asset is not None and asset.duration is not None and end > asset.duration + 1e-6:
        errors.add(f"{where}.out", "out за пределами длительности ассета")
        end_ok = False

    if start_ok and end_ok and end - start < settings.min_clip_sec:
        errors.add(f"{where}.out", f"клип короче {settings.min_clip_sec} с")
        end_ok = False

    return (start, end) if start_ok and end_ok else (None, None)


def _validate_transition(raw: object, where: str, errors: _Errors) -> tuple[dict | None, bool]:
    """Переход «в» этот клип из предыдущего. Ноль и отсутствие — стык, как раньше.

    Возвращает (значение или None, ок ли разбор). None при ок — поля в документе не будет.
    """
    if raw is None:
        return None, True
    if not isinstance(raw, dict):
        errors.add(f"{where}.transition", "transition должен быть объектом")
        return None, False
    kind = raw.get("kind")
    if kind not in TRANSITION_KINDS:
        errors.add(f"{where}.transition.kind", "kind: fade")
        return None, False
    duration = _number(raw.get("duration"))
    if duration is None or duration < 0:
        errors.add(f"{where}.transition.duration", "duration не может быть отрицательным")
        return None, False
    duration = _round(duration)
    if duration == 0:
        return None, True
    return {"kind": kind, "duration": duration}, True


# Переход обязан быть строго короче соседей; зазор, чтобы не спорить с округлением до миллисекунд.
FADE_GUARD = 0.05


def _fade_of(clips: list[dict], index: int) -> float:
    """Длительность перехода «в» клип с этим номером. У первого клипа перехода нет."""
    if index <= 0:
        return 0.0
    transition = clips[index].get("transition")
    return float(transition["duration"]) if isinstance(transition, dict) else 0.0


def clamp_fades(clips: list[dict]) -> None:
    """Укоротить переходы, которые перестали помещаться, вместо отказа в сохранении.

    Подтяжка резов к паузам идёт уже ПОСЛЕ проверки и может укоротить клип: переход, который был
    короче обоих соседей, вдруг оказывается длиннее. xfade получает отрицательный offset — и это
    не ошибка, на которую ffmpeg пожалуется: он молча выбрасывает клип из вывода, а сохранённый
    документ потом не проходит собственную проверку сервера, то есть следующее сохранение падает
    в 422. Человек просил подтянуть рез, а не потерять кусок ролика, поэтому чиним переход.

    Здесь же разводим два перехода на одном клипе: если они наезжают друг на друга, второй
    начинается, пока идёт первый, и собственных кадров у клипа в ролике не остаётся.
    """
    for index in range(1, len(clips)):
        transition = clips[index].get("transition")
        if not isinstance(transition, dict):
            continue
        prev_len = clips[index - 1]["out"] - clips[index - 1]["in"] - _fade_of(clips, index - 1)
        this_len = clips[index]["out"] - clips[index]["in"]
        limit = _round(max(0.0, min(prev_len, this_len) - FADE_GUARD))
        if transition["duration"] <= limit:
            continue
        if limit <= 0:
            clips[index].pop("transition", None)
        else:
            transition["duration"] = limit


def _reject_overlong_fades(clips: list[dict], errors: _Errors) -> None:
    """xfade требует, чтобы переход был короче обоих соседних клипов: иначе offset уйдёт в минус."""
    for index, clip in enumerate(clips):
        transition = clip.get("transition")
        if not isinstance(transition, dict):
            continue
        if index == 0:
            continue
        prev_len = clips[index - 1]["out"] - clips[index - 1]["in"]
        this_len = clip["out"] - clip["in"]
        cap = min(prev_len, this_len)
        if transition["duration"] >= cap:
            errors.add(
                f"clips[{index}].transition.duration",
                "переход должен быть короче обоих соседних клипов",
            )


def _validate_clip(
    raw: object, index: int, seen_ids: set[str], assets: dict[str, AssetInfo], settings: Settings,
    errors: _Errors,
) -> dict | None:
    where = f"clips[{index}]"
    if not isinstance(raw, dict):
        errors.add(where, "клип должен быть объектом")
        return None
    clip_id = raw.get("id")
    if clip_id is None:
        clip_id = f"c{index + 1}"
    elif not isinstance(clip_id, str) or not clip_id.strip() or len(clip_id) > MAX_CLIP_ID:
        errors.add(f"{where}.id", f"id клипа: непустая строка не длиннее {MAX_CLIP_ID} знаков")
        return None
    if clip_id in seen_ids:
        errors.add(f"{where}.id", "id клипа повторяется")
        return None
    seen_ids.add(clip_id)

    asset_id = raw.get("asset_id")
    asset = assets.get(asset_id) if isinstance(asset_id, str) else None
    if asset is None:
        errors.add(f"{where}.asset_id", "нет такого ассета")
    elif asset.kind not in CLIP_KINDS:
        errors.add(f"{where}.asset_id", "в клип идёт видеоассет или картинка")
    elif asset.status not in CLIP_READY_STATUSES:
        errors.add(f"{where}.asset_id", "ассет ещё не готов")
        asset = None
    elif asset.duration is None and asset.kind != "image":
        errors.add(f"{where}.asset_id", "у ассета неизвестна длительность")
        asset = None

    volume = _number(raw.get("volume", 1))
    if volume is None or not 0.0 <= volume <= 2.0:
        errors.add(f"{where}.volume", "volume от 0 до 2")
        volume = None

    transition, transition_ok = _validate_transition(raw.get("transition"), where, errors)
    # У первого клипа не из чего переходить: поле игнорируем, как присланные флаги подтверждения.
    if index == 0:
        transition = None

    start, end = _validate_clip_time(where, raw, asset, settings, errors)
    if start is None or end is None or asset is None or volume is None or not transition_ok:
        return None
    out = {
        "id": clip_id,
        "asset_id": asset_id,
        "in": _round(start),
        "out": _round(end),
        "volume": round(volume, 3),
        "snap_to_pauses": bool(raw.get("snap_to_pauses", False)),
        # Флаги подтверждения выставляет только сервер: присланные значения игнорируются.
        "in_verified": False,
        "out_verified": False,
    }
    if transition is not None:
        out["transition"] = transition
    return out


def _validate_music(raw: object, assets: dict[str, AssetInfo], errors: _Errors) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.add("music", "music должен быть объектом")
        return None
    asset_id = raw.get("asset_id")
    asset = assets.get(asset_id) if isinstance(asset_id, str) else None
    if asset is None or asset.kind not in ("audio", "video"):
        errors.add("music.asset_id", "музыкой может быть звуковой или видеоассет владельца")
        return None
    volume = _number(raw.get("volume", 0.25))
    if volume is None or not 0.0 <= volume <= 1.0:
        errors.add("music.volume", "volume от 0 до 1")
        return None
    fades = {}
    for key in ("fade_in", "fade_out"):
        value = _number(raw.get(key, 0.0))
        if value is None or value < 0:
            errors.add(f"music.{key}", f"{key} не может быть отрицательным")
            return None
        fades[key] = _round(value)
    duck = raw.get("duck", False)
    if not isinstance(duck, bool):
        errors.add("music.duck", "duck должен быть true или false")
        return None
    speech = _number(raw.get("speech_volume", 1))
    if speech is None or not 0.0 <= speech <= 1.0:
        errors.add("music.speech_volume", "speech_volume от 0 до 1")
        return None
    return {
        "asset_id": asset_id,
        "volume": round(volume, 3),
        "fade_in": fades["fade_in"],
        "fade_out": fades["fade_out"],
        "loop": bool(raw.get("loop", True)),
        "duck": duck,
        "speech_volume": round(speech, 3),
    }


def _validate_sounds(
    raw: object, clip_ids: set[str], assets: dict[str, AssetInfo], settings: Settings, errors: _Errors,
) -> list[dict]:
    """Звуковая дорожка: куски звука, положенные под картинку с точного места шкалы.

    Звук идёт поверх речи клипов, а не встаёт в их очередь: озвучка между кусками дала бы чёрный
    кадр. Поэтому у звука своё время на шкале — at — и клипы он не сдвигает.

    Свисать за конец ролика звуку можно, сборка его обрежет. Отвергать такой документ значило бы
    ломать сохранение всякий раз, когда человек укорачивает видео под уже положенной озвучкой.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.add("sounds", "sounds должен быть списком")
        return []
    if len(raw) > settings.max_clips:
        errors.add("sounds", f"звуков больше {settings.max_clips}")
        return []
    sounds: list[dict] = []
    # Выделение на шкале одно на клипы и звуки: общий id сделал бы его неоднозначным.
    seen = set(clip_ids)
    for index, item in enumerate(raw):
        where = f"sounds[{index}]"
        if not isinstance(item, dict):
            errors.add(where, "звук должен быть объектом")
            continue
        sound_id = item.get("id")
        if sound_id is None:
            sound_id = f"s{index + 1}"
        elif not isinstance(sound_id, str) or not sound_id.strip() or len(sound_id) > MAX_CLIP_ID:
            errors.add(f"{where}.id", f"id звука: непустая строка не длиннее {MAX_CLIP_ID} знаков")
            continue
        if sound_id in seen:
            errors.add(f"{where}.id", "id звука повторяется или совпадает с id клипа")
            continue
        seen.add(sound_id)
        asset_id = item.get("asset_id")
        asset = assets.get(asset_id) if isinstance(asset_id, str) else None
        if asset is None or asset.kind not in SOUND_KINDS:
            errors.add(f"{where}.asset_id", "звуком может быть звуковой или видеоассет владельца")
            continue
        if asset.status not in CLIP_READY_STATUSES or asset.duration is None:
            errors.add(f"{where}.asset_id", "ассет ещё не готов")
            continue
        at = _number(item.get("at"))
        if at is None or at < 0:
            errors.add(f"{where}.at", "at — неотрицательное число секунд")
            continue
        if at > settings.max_total_duration_sec:
            errors.add(f"{where}.at", f"at дальше {settings.max_total_duration_sec} с")
            continue
        volume = _number(item.get("volume", 1))
        if volume is None or not 0.0 <= volume <= 2.0:
            errors.add(f"{where}.volume", "volume от 0 до 2")
            continue
        start, end = _validate_clip_time(where, item, asset, settings, errors)
        if start is None or end is None:
            continue
        sounds.append({
            "id": sound_id,
            "asset_id": asset_id,
            "at": _round(at),
            "in": _round(start),
            "out": _round(end),
            "volume": round(volume, 3),
        })
    return sounds


def _validate_cue_text(raw: object, where: str, errors: _Errors) -> str | None:
    if not isinstance(raw, str):
        errors.add(f"{where}.text", "text реплики должен быть строкой")
        return None
    # Перевод строки внутри реплики значащий — по нему субтитр ложится в кадр двумя строками,
    # поэтому чистим только края, а CRLF из браузерного поля приводим к одному виду.
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        errors.add(f"{where}.text", "текст реплики не может быть пустым")
        return None
    if len(text) > MAX_CUE_TEXT:
        errors.add(f"{where}.text", f"текст реплики не длиннее {MAX_CUE_TEXT} знаков")
        return None
    if text.count("\n") >= MAX_CUE_LINES:
        errors.add(f"{where}.text", f"в реплике не больше {MAX_CUE_LINES} строк")
        return None
    return text


def _validate_cue(raw: object, index: int, errors: _Errors) -> dict | None:
    where = f"subtitles.cues[{index}]"
    if not isinstance(raw, dict):
        errors.add(where, "реплика должна быть объектом")
        return None
    start = _number(raw.get("start"))
    end = _number(raw.get("end"))
    if start is None or start < 0:
        errors.add(f"{where}.start", "start реплики — неотрицательное число секунд")
        start = None
    if end is None:
        errors.add(f"{where}.end", "end реплики должен быть числом секунд")
    elif start is not None and _round(end) <= _round(start):
        # Сравниваем уже округлённые времена: реплика в полмиллисекунды прошла бы проверку,
        # а в документ легла бы нулевой длины и в кадре не показалась вовсе.
        errors.add(f"{where}.end", "end реплики должен быть больше start")
        end = None
    text = _validate_cue_text(raw.get("text"), where, errors)
    if start is None or end is None or text is None:
        return None
    return {"start": _round(start), "end": _round(end), "text": text}


def _validate_cues(raw: object, settings: Settings, errors: _Errors) -> list[dict] | None:
    if not isinstance(raw, list):
        errors.add("subtitles.cues", "cues должен быть списком реплик")
        return None
    if not raw:
        errors.add("subtitles.cues", "в субтитрах должна быть хотя бы одна реплика")
        return None
    if len(raw) > settings.max_cues:
        errors.add("subtitles.cues", f"реплик больше {settings.max_cues}")
        return None
    cues = [cue for index, item in enumerate(raw) if (cue := _validate_cue(item, index, errors))]
    if len(cues) != len(raw):
        return None  # часть реплик уже с ошибками: порядок проверять не по чему
    cues.sort(key=lambda cue: cue["start"])
    for previous, cue in pairwise(cues):
        if cue["start"] < previous["end"]:
            errors.add("subtitles.cues", "реплики накладываются: в кадре был бы сразу второй субтитр")
            return None
    return cues


def _validate_subtitles(
    raw: object, assets: dict[str, AssetInfo], settings: Settings, errors: _Errors
) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.add("subtitles", "subtitles должен быть объектом")
        return None
    source = raw.get("source")
    if source not in SUB_SOURCES:
        errors.add("subtitles.source", f"source: {', '.join(SUB_SOURCES)}")
        return None
    cues = None
    asset_id = None
    if source == "cues":
        # Реплики самодостаточны: расшифровка нужна была, чтобы их собрать, а не чтобы показать,
        # и удалённый ассет расшифровки не должен мешать собрать ролик.
        cues = _validate_cues(raw.get("cues"), settings, errors)
        if cues is None:
            return None
    else:
        asset_id = raw.get("asset_id")
        asset = assets.get(asset_id) if isinstance(asset_id, str) else None
        want_kind = "subtitle" if source == "file" else "video"
        if asset is None or asset.kind != want_kind:
            errors.add("subtitles.asset_id", f"для source={source} нужен ассет вида {want_kind}")
            return None
    mode = raw.get("mode", "burn")
    if mode not in SUB_MODES:
        errors.add("subtitles.mode", f"mode: {', '.join(SUB_MODES)}")
        return None
    style = raw.get("style", "default")
    if style not in SUB_STYLES:
        errors.add("subtitles.style", f"style: {', '.join(SUB_STYLES)}")
        return None
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.add("subtitles.enabled", "enabled должен быть true или false")
        return None
    out = {"source": source, "asset_id": asset_id, "mode": mode, "style": style, "enabled": enabled}
    if cues is not None:
        out["cues"] = cues
    return out


def validate_doc(raw: object, *, assets: dict[str, AssetInfo], settings: Settings) -> dict:
    """Нормализованный документ или ProjectInvalid со списком ошибок.

    Возвращает ровно пять ключей: неизвестные поля отбрасываются, чтобы клиент не мог протащить
    что-то в хранимый документ и получить обратно при чтении.
    """
    errors = _Errors()
    if not isinstance(raw, dict):
        raise ProjectInvalid([{"field": "doc", "message": "документ должен быть объектом"}])

    output = _validate_output(raw.get("output"), errors)
    raw_clips = raw.get("clips")
    clips: list[dict] = []
    if not isinstance(raw_clips, list):
        errors.add("clips", "clips должен быть списком")
    elif not raw_clips:
        errors.add("clips", "в проекте должен быть хотя бы один клип")
    elif len(raw_clips) > settings.max_clips:
        errors.add("clips", f"клипов больше {settings.max_clips}")
    else:
        seen: set[str] = set()
        for index, item in enumerate(raw_clips):
            clip = _validate_clip(item, index, seen, assets, settings, errors)
            if clip is not None:
                clips.append(clip)
        if len(clips) == len(raw_clips):
            _reject_overlong_fades(clips, errors)
            if clips_duration(clips) > settings.max_total_duration_sec:
                errors.add("clips", f"ролик длиннее {settings.max_total_duration_sec} с")

    music = _validate_music(raw.get("music"), assets, errors)
    sounds = _validate_sounds(raw.get("sounds"), {c["id"] for c in clips}, assets, settings, errors)
    subtitles = _validate_subtitles(raw.get("subtitles"), assets, settings, errors)
    if errors:
        raise ProjectInvalid(errors.items)
    return {"output": output, "clips": clips, "sounds": sounds, "music": music, "subtitles": subtitles}
