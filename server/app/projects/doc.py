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

ASPECTS = ("16:9", "9:16", "1:1")
FITS = ("pad", "crop")
FPS_VALUES = (25, 30, 50, 60)
SUB_SOURCES = ("file", "transcript", "cues")
SUB_MODES = ("burn", "soft")
SUB_STYLES = ("default",)
CLIP_READY_STATUSES = ("ready", "proxy_ready")
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
    elif asset is not None and asset.duration is not None and end > asset.duration + 1e-6:
        errors.add(f"{where}.out", "out за пределами длительности ассета")
        end_ok = False

    if start_ok and end_ok and end - start < settings.min_clip_sec:
        errors.add(f"{where}.out", f"клип короче {settings.min_clip_sec} с")
        end_ok = False

    return (start, end) if start_ok and end_ok else (None, None)


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
    elif asset.kind != "video":
        errors.add(f"{where}.asset_id", "в клип идёт только видеоассет")
    elif asset.status not in CLIP_READY_STATUSES:
        errors.add(f"{where}.asset_id", "ассет ещё не готов")
        asset = None
    elif asset.duration is None:
        errors.add(f"{where}.asset_id", "у ассета неизвестна длительность")
        asset = None

    start, end = _validate_clip_time(where, raw, asset, settings, errors)
    if start is None or end is None or asset is None:
        return None
    return {
        "id": clip_id,
        "asset_id": asset_id,
        "in": _round(start),
        "out": _round(end),
        "snap_to_pauses": bool(raw.get("snap_to_pauses", False)),
        # Флаги подтверждения выставляет только сервер: присланные значения игнорируются.
        "in_verified": False,
        "out_verified": False,
    }


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
    return {
        "asset_id": asset_id,
        "volume": round(volume, 3),
        "fade_in": fades["fade_in"],
        "fade_out": fades["fade_out"],
        "loop": bool(raw.get("loop", True)),
    }


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
    out = {"source": source, "asset_id": asset_id, "mode": mode, "style": style}
    if cues is not None:
        out["cues"] = cues
    return out


def validate_doc(raw: object, *, assets: dict[str, AssetInfo], settings: Settings) -> dict:
    """Нормализованный документ или ProjectInvalid со списком ошибок.

    Возвращает ровно четыре ключа: неизвестные поля отбрасываются, чтобы клиент не мог протащить
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
        total = sum(c["out"] - c["in"] for c in clips)
        if total > settings.max_total_duration_sec:
            errors.add("clips", f"ролик длиннее {settings.max_total_duration_sec} с")

    music = _validate_music(raw.get("music"), assets, errors)
    subtitles = _validate_subtitles(raw.get("subtitles"), assets, settings, errors)
    if errors:
        raise ProjectInvalid(errors.items)
    return {"output": output, "clips": clips, "music": music, "subtitles": subtitles}
