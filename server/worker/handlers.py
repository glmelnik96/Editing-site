"""Обработчики заданий воркера.

analyze доводит ассет до состояния «можно монтировать»: параметры файла, пики, карты пауз, полоска
кадров. proxy делает лёгкое видео для плеера. Оба пишут во временный файл и переименовывают: половина
результата на диске не должна выглядеть готовой.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.storage import asset_dir
from server.app.util import now_iso
from server.media.audio import analyze_audio, wav_args
from server.media.probe import probe_file
from server.media.proxy import parse_progress, proxy_args, proxy_name
from server.media.run import MediaError, run_streaming, run_tool
from server.media.thumbs import grid_layout, thumbs_args, thumbs_meta

log = logging.getLogger("video.worker")

PROXY_PRIORITY = 5  # ниже analyze (10) и выше рендера (0): раздел 9.1 спеки
WAV_NAME = "audio16k.wav"
PROGRESS_AFTER_PROBE = 0.2
PROGRESS_AFTER_AUDIO = 0.5
PROGRESS_AFTER_THUMBS = 0.8


def _asset(conn: sqlite3.Connection, job: sqlite3.Row) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM assets WHERE id = ? AND user_id = ?", (job["target_id"], job["user_id"])
    ).fetchone()


def _source(settings: Settings, asset: sqlite3.Row) -> Path:
    return asset_dir(settings, asset["user_id"], asset["id"]) / f"source.{asset['ext']}"


def _write_json(path: Path, data: dict) -> None:
    """Через временный файл: читатель никогда не увидит половину JSON."""
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def extract_wav(settings: Settings, src: Path, dst: Path) -> None:
    run_tool(wav_args(settings, str(src), str(dst)), timeout=settings.analyze_timeout_sec)


def build_thumbs(settings: Settings, src: Path, dst: Path, *, duration: float, width, height) -> dict:
    layout = grid_layout(settings, duration=duration, width=width, height=height)
    run_tool(thumbs_args(settings, str(src), str(dst), layout), timeout=settings.analyze_timeout_sec)
    return thumbs_meta(layout)


def _set_asset_failed(conn: sqlite3.Connection, asset_id: str, message: str) -> None:
    conn.execute(
        "UPDATE assets SET status = 'failed', error = ? WHERE id = ?", (message[:1000], asset_id)
    )


def handle_analyze(conn: sqlite3.Connection, settings: Settings, job: sqlite3.Row) -> None:
    from server.worker.queue import set_progress  # локально: очередь не должна зависеть от обработчиков

    asset = _asset(conn, job)
    if asset is None:
        log.info("analyze: ассет %s уже удалён, пропускаем", job["target_id"])
        return
    asset_id = asset["id"]
    folder = asset_dir(settings, asset["user_id"], asset_id)
    conn.execute("UPDATE assets SET status = 'analyzing', error = NULL WHERE id = ?", (asset_id,))
    try:
        info = probe_file(settings, str(_source(settings, asset)))
    except MediaError as exc:
        _set_asset_failed(conn, asset_id, exc.message)
        raise
    conn.execute(
        "UPDATE assets SET kind = ?, duration = ?, width = ?, height = ?, fps = ?, has_audio = ?, "
        "video_codec = ?, audio_codec = ? WHERE id = ?",
        (
            info.kind, info.duration, info.width, info.height, info.fps, int(info.has_audio),
            info.video_codec, info.audio_codec, asset_id,
        ),
    )
    set_progress(conn, job["id"], PROGRESS_AFTER_PROBE)

    peaks = {"rate": settings.peaks_per_sec, "peaks": []}
    analysis = {
        "duration": info.duration, "speech_level_db": None, "threshold_db": None,
        "silences": [], "silences_dense": [],
    }
    if info.has_audio:
        wav = folder / WAV_NAME
        try:
            extract_wav(settings, _source(settings, asset), wav)
            result = analyze_audio(settings, str(wav), duration=info.duration)
            peaks, analysis = result["peaks"], result["analysis"]
        except MediaError as exc:
            _set_asset_failed(conn, asset_id, exc.message)
            raise
        finally:
            wav.unlink(missing_ok=True)  # звук для транскрипции пересоберём в M4, диск дороже
    _write_json(folder / "peaks.json", peaks)
    _write_json(folder / "analysis.json", analysis)
    set_progress(conn, job["id"], PROGRESS_AFTER_AUDIO)

    if info.kind == "video":
        try:
            meta = build_thumbs(
                settings, _source(settings, asset), folder / "thumbs.jpg",
                duration=info.duration, width=info.width, height=info.height,
            )
        except MediaError as exc:
            _set_asset_failed(conn, asset_id, exc.message)
            raise
        _write_json(folder / "thumbs.json", meta)
    set_progress(conn, job["id"], PROGRESS_AFTER_THUMBS)

    conn.execute(
        "UPDATE assets SET status = 'ready', last_access_at = ? WHERE id = ?", (now_iso(), asset_id)
    )
    enqueue_job(
        conn, user_id=job["user_id"], type_="proxy", target_id=asset_id, priority=PROXY_PRIORITY
    )
    log.info("analyze: %s готов (%s, %.1f с)", asset_id, info.kind, info.duration)


def handle_proxy(conn: sqlite3.Connection, settings: Settings, job: sqlite3.Row) -> None:
    from server.worker.queue import is_canceled, set_progress

    asset = _asset(conn, job)
    if asset is None:
        log.info("proxy: ассет %s уже удалён, пропускаем", job["target_id"])
        return
    if asset["status"] not in ("ready", "proxy_ready"):
        log.info("proxy: ассет %s в статусе %s, кодировать нечего", asset["id"], asset["status"])
        return
    folder = asset_dir(settings, asset["user_id"], asset["id"])
    dst = folder / proxy_name(asset["kind"])
    tmp = dst.with_suffix(dst.suffix + ".part")
    total = float(asset["duration"] or 0)

    def on_line(line: str) -> None:
        value = parse_progress(line, total=total)
        if value is not None:
            set_progress(conn, job["id"], value)

    try:
        run_streaming(
            proxy_args(settings, str(_source(settings, asset)), str(tmp), kind=asset["kind"]),
            timeout=settings.proxy_timeout_sec,
            on_line=on_line,
            should_stop=lambda: is_canceled(conn, job["id"]),
        )
    except MediaError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dst)
    conn.execute(
        "UPDATE assets SET status = 'proxy_ready', last_access_at = ? WHERE id = ?",
        (now_iso(), asset["id"]),
    )
    log.info("proxy: %s готов", asset["id"])


HANDLERS = {"analyze": handle_analyze, "proxy": handle_proxy}
