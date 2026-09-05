"""Обработчики заданий воркера.

analyze доводит ассет до состояния «можно монтировать»: параметры файла, пики, карты пауз, полоска
кадров. proxy делает лёгкое видео для плеера. Оба пишут во временный файл и переименовывают: половина
результата на диске не должна выглядеть готовой.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.projects.store import assets_of, get_project
from server.app.storage import asset_dir, render_dir
from server.app.util import iso, new_id, now_iso, utcnow
from server.db.core import transaction
from server.media.audio import analyze_audio, wav_args
from server.media.probe import probe_file
from server.media.proxy import parse_progress, proxy_args, proxy_name
from server.media.render import RenderInvalid, SourceInfo, build_render_command, total_duration
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


def _stop_if_canceled(conn: sqlite3.Connection, job: sqlite3.Row) -> None:
    """Между шагами анализа: задание отменили или воркер останавливают — дальше не работаем.

    Внутри одного вызова ffmpeg прерваться нельзя (шаги анализа идут через run_tool без проверки),
    но между шагами это отсекает лишние минуты работы после удаления ассета или рестарта сервиса.
    """
    from server.worker.queue import is_canceled

    if is_canceled(conn, job["id"]):
        raise MediaError("canceled", "Отменено")


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
    _stop_if_canceled(conn, job)

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
    _stop_if_canceled(conn, job)

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
    # analyze может выполниться повторно (janitor вернул задание в очередь) — тогда для того же
    # ассета уже есть незавершённое задание proxy, и второе ставить не нужно
    pending = conn.execute(
        "SELECT count(*) FROM jobs WHERE target_id = ? AND type = 'proxy' "
        "AND status IN ('queued', 'running')",
        (asset_id,),
    ).fetchone()[0]
    if not pending:
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


RENDER_READY_STATUSES = ("ready", "proxy_ready")
# Грубая оценка веса результата: точно не посчитать, но порядок величины отсекает
# заведомо безнадёжный запуск до того, как ffmpeg заполнит диск.
BITRATE_BY_QUALITY = {"draft": 2_000_000, "final": 5_000_000}
SIZE_SAFETY = 2


def disk_free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _sources_for(
    conn: sqlite3.Connection, settings: Settings, project: dict, owner_id: str
) -> dict[str, SourceInfo]:
    """Пути к исходникам проекта. Ассет мог исчезнуть или откатиться в обработку с момента сохранения."""
    sources: dict[str, SourceInfo] = {}
    for asset_id in sorted(assets_of(project["doc"])):
        # Фильтр по владельцу тут избыточен (документ проверялся при сохранении), но стоит одного
        # условия и снимает вопрос: собрать чужой файл нельзя даже при испорченном документе.
        row = conn.execute(
            "SELECT * FROM assets WHERE id = ? AND user_id = ?", (asset_id, owner_id)
        ).fetchone()
        if row is None:
            raise MediaError("asset_gone", f"файл {asset_id} удалён, пересоберите проект")
        if row["status"] not in RENDER_READY_STATUSES:
            raise MediaError("asset_not_ready", f"файл {row['original_name']} ещё обрабатывается")
        folder = asset_dir(settings, row["user_id"], row["id"])
        name = "subs.vtt" if row["kind"] == "subtitle" else f"source.{row['ext']}"
        sources[asset_id] = SourceInfo(
            path=str(folder / name),
            duration=float(row["duration"] or 0),
            has_audio=bool(row["has_audio"]),
        )
    return sources


def handle_render(conn: sqlite3.Connection, settings: Settings, job: sqlite3.Row) -> None:
    """Собирает проект в один файл. Строка рендера появляется только после успеха."""
    from server.worker.queue import is_canceled, set_progress

    project = get_project(conn, job["user_id"], job["target_id"])
    if project is None:
        log.info("render: проект %s уже удалён, пропускаем", job["target_id"])
        return

    params = json.loads(job["params"] or "{}")
    quality = params.get("quality", "draft")
    duration = total_duration(project["doc"])
    if duration <= 0:
        raise MediaError("empty_project", "в проекте нет клипов")

    sources = _sources_for(conn, settings, project, job["user_id"])
    estimate = int(BITRATE_BY_QUALITY.get(quality, 5_000_000) / 8 * duration * SIZE_SAFETY)
    if disk_free_bytes(settings.data_dir) < estimate:
        raise MediaError("disk_low", "на диске мало места для сборки, освободите его и повторите")

    render_id = new_id("rnd")
    folder = render_dir(settings, job["user_id"], project["id"])
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{render_id}.mp4"
    tmp = dst.with_suffix(dst.suffix + ".part")

    try:
        args = build_render_command(
            project["doc"], sources=sources, quality=quality, settings=settings, out_path=str(tmp)
        )
    except RenderInvalid as exc:
        raise MediaError("bad_project", str(exc)) from exc

    def on_line(line: str) -> None:
        value = parse_progress(line, total=duration)
        if value is not None:
            set_progress(conn, job["id"], value)

    try:
        run_streaming(
            args,
            timeout=settings.render_timeout_sec,
            on_line=on_line,
            should_stop=lambda: is_canceled(conn, job["id"]),
        )
    except MediaError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dst)

    now = utcnow()
    with transaction(conn):
        conn.execute(
            "INSERT INTO renders (id, project_id, user_id, job_id, quality, path, size, duration, "
            "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                render_id, project["id"], job["user_id"], job["id"], quality, str(dst),
                dst.stat().st_size, duration, iso(now),
                iso(now + timedelta(hours=settings.render_ttl_hours)),
            ),
        )
    log.info("render: %s готов (%s, %.1f с)", render_id, quality, duration)


HANDLERS = {"analyze": handle_analyze, "proxy": handle_proxy, "render": handle_render}
