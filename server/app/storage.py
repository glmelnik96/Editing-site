"""Раскладка файлов на диске и публичные ссылки. Пути выводятся только из идентификаторов,
имена исходных файлов в путях не участвуют (раздел 6.2 спеки).
"""
from __future__ import annotations

import re
from pathlib import Path

from server.app.config import Settings

KINDS = ("video", "audio", "image", "subtitle")
TRANSCRIPT_NAME = "transcript.json"
# Расширения, по которым вид записи угадывается до анализа. Настоящий вид ставит ffprobe: он
# один знает, что внутри, — поэтому список здесь широкий и ошибка в нём не смертельна. Смертельно
# отсутствие в нём: незнакомое расширение загрузку не начинает (см. resolve_kind в uploads/store).
VIDEO_EXTS = {
    "mp4", "mov", "m4v", "mkv", "webm", "avi", "mts", "m2ts", "mxf", "ts", "wmv", "flv", "3gp",
    "mpg", "mpeg", "m2v", "ogv", "asf", "vob", "divx", "f4v", "3g2",
}
AUDIO_EXTS = {
    "mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "aiff", "aif", "wma",
    "mka", "m4b", "oga", "amr", "caf", "ac3", "mp2", "wv",
}
# heic и avif сюда не входят намеренно: их читает не всякая сборка ffmpeg, а обещать формат,
# который потом не откроется, хуже, чем не обещать его вовсе.
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "gif"}
SUBTITLE_EXTS = {"srt", "vtt"}
# Файлы ассета, которые отдаются наружу. source.* сюда не входит намеренно (раздел 11 спеки).
PUBLIC_FILES = (
    "proxy.mp4", "proxy.m4a", "thumbs.jpg", "thumbs.json", "peaks.json", "analysis.json", "subs.vtt",
)

ID_RE = re.compile(r"^[a-z]{3}_[0-9a-f]{12}$")
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")
_ASSET_URL_RE = re.compile(r"^/files/([^/]+)/assets/([^/]+)/([^/]+)$")
_RENDER_URL_RE = re.compile(r"^/files/([^/]+)/projects/([^/]+)/renders/([^/]+)$")
_CONVERSION_URL_RE = re.compile(r"^/files/([^/]+)/assets/([^/]+)/conversions/([^/]+)$")
CONVERT_EXTS = {"mp3", "m4a", "aac", "wav", "flac", "ogg", "mp4", "webm"}


def safe_ext(filename: str) -> str:
    """Расширение в нижнем регистре из букв и цифр (до 8 знаков), иначе bin."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext if _EXT_RE.match(ext) else "bin"


def kind_from_ext(ext: str) -> str | None:
    """Тип по расширению без точки (регистр не важен): video, audio, image, subtitle или None."""
    ext = ext.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in SUBTITLE_EXTS:
        return "subtitle"
    return None


def known_exts() -> dict[str, list[str]]:
    """Что можно загрузить, по видам. Отсюда и проверка, и подпись на экране записей: разойдясь,
    они обещали бы человеку не то, что принимают."""
    return {
        "video": sorted(VIDEO_EXTS),
        "audio": sorted(AUDIO_EXTS),
        "image": sorted(IMAGE_EXTS),
        "subtitle": sorted(SUBTITLE_EXTS),
    }


def _check_id(value: str) -> str:
    """Форма идентификатора (см. ID_RE); иначе путь мог бы выйти за пределы data_dir."""
    if not ID_RE.match(value):
        raise ValueError(f"некорректный id: {value!r}")
    return value


def asset_dir(settings: Settings, user_id: str, asset_id: str) -> Path:
    return settings.data_dir / _check_id(user_id) / "assets" / _check_id(asset_id)


def transcript_path(settings: Settings, user_id: str, asset_id: str) -> Path:
    """Расшифровка ассета: её пишет воркер, а читают экспорт и сборка субтитров проекта."""
    return asset_dir(settings, user_id, asset_id) / TRANSCRIPT_NAME


def project_dir(settings: Settings, user_id: str, project_id: str) -> Path:
    _check_id(user_id)
    _check_id(project_id)
    return settings.data_dir / user_id / "projects" / project_id


def render_dir(settings: Settings, user_id: str, project_id: str) -> Path:
    return project_dir(settings, user_id, project_id) / "renders"


def conversion_dir(settings: Settings, user_id: str, asset_id: str) -> Path:
    return asset_dir(settings, user_id, asset_id) / "conversions"


def conversion_url(user_id: str, asset_id: str, conversion_id: str, fmt: str) -> str:
    return f"/files/{user_id}/assets/{asset_id}/conversions/{conversion_id}.{fmt}"


def subs_dir(settings: Settings, user_id: str, project_id: str) -> Path:
    """Кэш субтитров проекта: имя файла — версия проекта, поэтому правка документа не может
    отдать старые реплики. Наружу этот каталог не отдаётся (см. parse_file_url)."""
    return project_dir(settings, user_id, project_id) / "subs"


# Форматы готового ролика. Имя файла — «{id}.{формат}», и по нему же отдаётся тип содержимого.
RENDER_FORMATS = ("mp4", "webm", "m4a")
RENDER_MEDIA_TYPES = {"mp4": "video/mp4", "webm": "video/webm", "m4a": "audio/mp4"}


def render_url(user_id: str, project_id: str, render_id: str, fmt: str = "mp4") -> str:
    return f"/files/{user_id}/projects/{project_id}/renders/{render_id}.{fmt}"


def upload_path(settings: Settings, upload_id: str) -> Path:
    return settings.uploads_tmp_path / _check_id(upload_id)


def file_url(user_id: str, asset_id: str, name: str) -> str:
    return f"/files/{user_id}/assets/{asset_id}/{name}"


def parse_file_url(path: str) -> tuple[str, str, str, str] | None:
    """(user_id, owner_id, name, kind) из пути /files/…; идентификаторы проверяются по форме.

    Две формы ассета: файлы (`/assets/{id}/{имя}`) и конверсии
    (`/assets/{id}/conversions/{id}.{ext}`). Готовые ролики —
    `/projects/{id}/renders/{id}.{mp4|webm|m4a}`. Вид возвращается четвёртым элементом, чтобы вызывающий
    не разбирал путь второй раз.
    """
    m = _CONVERSION_URL_RE.match(path)
    if m:
        user_id, asset_id, name = m.groups()
        if not (ID_RE.match(user_id) and ID_RE.match(asset_id)):
            return None
        stem, sep, ext = name.rpartition(".")
        if not sep or ext not in CONVERT_EXTS or not ID_RE.match(stem):
            return None
        return user_id, asset_id, name, "conversion"

    m = _ASSET_URL_RE.match(path)
    if m:
        user_id, asset_id, name = m.groups()
        if not (ID_RE.match(user_id) and ID_RE.match(asset_id)):
            return None
        if name in (".", ".."):
            return None
        return user_id, asset_id, name, "asset"

    m = _RENDER_URL_RE.match(path)
    if m:
        user_id, project_id, name = m.groups()
        if not (ID_RE.match(user_id) and ID_RE.match(project_id)):
            return None
        # Имя ролика — «{id}.{формат}»: ничего другого в этом каталоге наружу не отдаётся.
        stem, sep, ext = name.rpartition(".")
        if not sep or ext not in RENDER_FORMATS or not ID_RE.match(stem):
            return None
        return user_id, project_id, name, "render"
    return None
