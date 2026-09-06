"""Карточка ассета для API: метаданные и относительные ссылки на производные файлы.

Ссылки выводятся из статуса, а не из наличия файлов на диске: список ассетов не должен ходить на диск.
Транскрипт из статуса не выводится (его может и не быть), поэтому его наличие передаётся отдельно —
из базы, тем же одним запросом на весь список.
"""
from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from server.app.storage import file_url


class AssetFiles(BaseModel):
    proxy: str | None = None
    thumbs: str | None = None
    thumbs_meta: str | None = None
    peaks: str | None = None
    analysis: str | None = None
    vtt: str | None = None
    transcript: str | None = None


class AssetView(BaseModel):
    id: str
    kind: str
    original_name: str
    size: int
    status: str
    duration: float | None
    width: int | None
    height: int | None
    fps: float | None
    has_audio: bool | None
    video_codec: str | None
    audio_codec: str | None
    error: str | None
    created_at: str
    last_access_at: str
    files: AssetFiles


def asset_files(row: dict | sqlite3.Row, *, has_transcript: bool = False) -> AssetFiles:
    user_id, asset_id, kind, status = row["user_id"], row["id"], row["kind"], row["status"]
    files = AssetFiles()
    if kind == "subtitle":
        files.vtt = file_url(user_id, asset_id, "subs.vtt")
    elif status in ("ready", "proxy_ready"):
        files.peaks = file_url(user_id, asset_id, "peaks.json")
        files.analysis = file_url(user_id, asset_id, "analysis.json")
        if kind == "video":
            files.thumbs = file_url(user_id, asset_id, "thumbs.jpg")
            files.thumbs_meta = file_url(user_id, asset_id, "thumbs.json")
        if status == "proxy_ready":
            files.proxy = file_url(user_id, asset_id, "proxy.mp4" if kind == "video" else "proxy.m4a")
    if has_transcript:
        # Ссылка на API, а не на /files/: transcript.json наружу файлом не отдаётся (PUBLIC_FILES),
        # а ручка тем же адресом выдаёт ещё и SRT с VTT. Наличие приходит извне: оно живёт в базе,
        # а не выводится из статуса, и ради него список ассетов не должен ходить на диск.
        files.transcript = f"/api/v1/assets/{asset_id}/transcript"
    return files


def asset_view(row: dict | sqlite3.Row, *, has_transcript: bool = False) -> AssetView:
    has_audio = row["has_audio"]
    return AssetView(
        id=row["id"],
        kind=row["kind"],
        original_name=row["original_name"],
        size=row["size"],
        status=row["status"],
        duration=row["duration"],
        width=row["width"],
        height=row["height"],
        fps=row["fps"],
        has_audio=None if has_audio is None else bool(has_audio),
        video_codec=row["video_codec"],
        audio_codec=row["audio_codec"],
        error=row["error"],
        created_at=row["created_at"],
        last_access_at=row["last_access_at"],
        files=asset_files(row, has_transcript=has_transcript),
    )
