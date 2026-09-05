"""Раскладка файлов на диске и публичные ссылки. Пути выводятся только из идентификаторов,
имена исходных файлов в путях не участвуют (раздел 6.2 спеки).
"""
from __future__ import annotations

import re
from pathlib import Path

from server.app.config import Settings

KINDS = ("video", "audio", "subtitle")
VIDEO_EXTS = {"mp4", "mov", "m4v", "mkv", "webm", "avi", "mts", "m2ts", "mxf", "ts", "wmv", "flv", "3gp"}
AUDIO_EXTS = {"mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "aiff", "aif", "wma"}
SUBTITLE_EXTS = {"srt", "vtt"}
# Файлы ассета, которые отдаются наружу. source.* сюда не входит намеренно (раздел 11 спеки).
PUBLIC_FILES = (
    "proxy.mp4", "proxy.m4a", "thumbs.jpg", "thumbs.json", "peaks.json", "analysis.json", "subs.vtt",
)

ID_RE = re.compile(r"^[a-z]{3}_[0-9a-f]{12}$")
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")
_ASSET_URL_RE = re.compile(r"^/files/([^/]+)/assets/([^/]+)/([^/]+)$")
_RENDER_URL_RE = re.compile(r"^/files/([^/]+)/projects/([^/]+)/renders/([^/]+)$")


def safe_ext(filename: str) -> str:
    """Расширение в нижнем регистре из букв и цифр (до 8 знаков), иначе bin."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext if _EXT_RE.match(ext) else "bin"


def kind_from_ext(ext: str) -> str | None:
    """Тип по расширению без точки (регистр не важен): video, audio, subtitle или None."""
    ext = ext.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in SUBTITLE_EXTS:
        return "subtitle"
    return None


def _check_id(value: str) -> str:
    """Форма идентификатора (см. ID_RE); иначе путь мог бы выйти за пределы data_dir."""
    if not ID_RE.match(value):
        raise ValueError(f"некорректный id: {value!r}")
    return value


def asset_dir(settings: Settings, user_id: str, asset_id: str) -> Path:
    return settings.data_dir / _check_id(user_id) / "assets" / _check_id(asset_id)


def render_dir(settings: Settings, user_id: str, project_id: str) -> Path:
    _check_id(user_id)
    _check_id(project_id)
    return settings.data_dir / user_id / "projects" / project_id / "renders"


def render_url(user_id: str, project_id: str, render_id: str) -> str:
    return f"/files/{user_id}/projects/{project_id}/renders/{render_id}.mp4"


def upload_path(settings: Settings, upload_id: str) -> Path:
    return settings.uploads_tmp_path / _check_id(upload_id)


def file_url(user_id: str, asset_id: str, name: str) -> str:
    return f"/files/{user_id}/assets/{asset_id}/{name}"


def parse_file_url(path: str) -> tuple[str, str, str, str] | None:
    """(user_id, owner_id, name, kind) из пути /files/…; идентификаторы проверяются по форме.

    Две формы: файлы ассета (`/assets/{id}/{имя}`) и готовые ролики
    (`/projects/{id}/renders/{id}.mp4`). Вид возвращается четвёртым элементом, чтобы вызывающий
    не разбирал путь второй раз.
    """
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
        # Имя ролика всегда «{id}.mp4»: ничего другого в этом каталоге наружу не отдаётся.
        if not name.endswith(".mp4") or not ID_RE.match(name[: -len(".mp4")]):
            return None
        return user_id, project_id, name, "render"
    return None
