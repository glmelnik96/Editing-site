"""Обработчики заданий воркера.

analyze доводит ассет до состояния «можно монтировать»: параметры файла, пики, карты пауз, полоска
кадров. proxy делает лёгкое видео для плеера. Оба пишут во временный файл и переименовывают: половина
результата на диске не должна выглядеть готовой. render собирает проект в один файл, transcribe
расшифровывает речь ассета.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.projects.store import (
    SubtitlesUnavailable,
    assets_of,
    build_project_subtitles,
    get_project,
)
from server.app.storage import TRANSCRIPT_NAME, asset_dir, render_dir
from server.app.transcribe.provider import ProviderError, TranscribeProvider, build_client
from server.app.util import iso, new_id, now_iso, utcnow
from server.db.core import transaction
from server.media.audio import analyze_audio, wav_args
from server.media.probe import probe_file
from server.media.proxy import parse_progress, proxy_args, proxy_name
from server.media.render import RenderInvalid, SourceInfo, build_render_command, total_duration
from server.media.run import MediaError, run_streaming, run_tool
from server.media.thumbs import grid_layout, thumbs_args, thumbs_meta
from server.media.transcribe import (
    CHUNK_CODEC,
    CHUNK_RATE,
    check_chunk_size,
    chunk_args,
    chunk_plan,
    clamp_segments,
    fix_seams,
    interpolate_words,
    mark_suspect,
    normalize_chunk,
)
from server.media.verify import verify_segment_boundaries

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

    # Субтитры из расшифровки собираются до команды: сборщик команды на диск не ходит и получает
    # готовый файл аргументом.
    try:
        subtitles_path = build_project_subtitles(conn, settings, project)
    except SubtitlesUnavailable as exc:
        raise MediaError("no_transcript", str(exc)) from exc

    render_id = new_id("rnd")
    folder = render_dir(settings, job["user_id"], project["id"])
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{render_id}.mp4"
    tmp = dst.with_suffix(dst.suffix + ".part")

    try:
        args = build_render_command(
            project["doc"], sources=sources, quality=quality, settings=settings, out_path=str(tmp),
            subtitles_path=subtitles_path,
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


# ── Транскрипция (раздел 10 спеки) ─────────────────────────────────────────────────────────────

# Транскрибировать можно то же, что и монтировать: анализ прошёл, карты пауз лежат на диске.
TRANSCRIBE_READY_STATUSES = RENDER_READY_STATUSES
CHUNK_PREFIX = "chunk-"
PROGRESS_SENT = 0.9  # доля прогресса на отправку чанков, остальное — сборка и запись
# Звук и чанки лежат на диске одновременно: WAV 16 кГц моно даёт 32 КБ/с, а чанки в худшем случае
# (сборка ffmpeg без libmp3lame) весят столько же.
TRANSCRIBE_BYTES_PER_SEC = 2 * 32_000


@dataclass(frozen=True)
class _Chunk:
    """Кусок звука, готовый к отправке.

    splittable=False у половинок: если и половина не влезла в предел загрузки, дело не в длине
    куска, и второе деление только оттянет тот же отказ.
    """

    path: Path
    start: float
    end: float
    splittable: bool = True


@contextmanager
def transcribe_provider(settings: Settings) -> Iterator[TranscribeProvider]:
    """Провайдер на время задания. Отдельной функцией — её целиком подменяет тест, чтобы не ходить
    в сеть; клиент закрывается здесь, иначе воркер копил бы соединения от задания к заданию."""
    with build_client(settings) as client:
        yield TranscribeProvider(settings, client)


def _provider_name(settings: Settings) -> str:
    """Кто расшифровывал — по хосту адреса: отдельной настройки нет, а имя модели у разных
    провайдеров одно и то же, и по нему потом не понять, чей это транскрипт."""
    return urlsplit(settings.transcribe_base_url).hostname or settings.transcribe_base_url


def _read_analysis(folder: Path) -> dict:
    """Карты пауз от задания analyze: по обычной режем, по плотной проверяем границы."""
    try:
        data = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MediaError(
            "no_analysis", "нет карты пауз (analysis.json), проанализируйте ассет заново"
        ) from exc
    if not isinstance(data, dict):
        raise MediaError("no_analysis", "карта пауз (analysis.json) испорчена, проанализируйте заново")
    return data


def mp3_encoder_available(settings: Settings) -> bool:
    """Есть ли libmp3lame в этой сборке ffmpeg. Спрашиваем один раз на задание: список кодеков у
    процесса не меняется, а падать на каждом чанке невнятным «unknown encoder» нельзя.

    Запасной путь без кодека — WAV 16 кГц моно: он вчетверо тяжелее, но десять минут (около 19 МБ)
    в предел загрузки 20 МБ ещё влезают.
    """
    try:
        return CHUNK_CODEC in run_tool([settings.ffmpeg_path, "-v", "error", "-encoders"], timeout=60)
    except MediaError:
        # ffmpeg вовсе не запустился — пусть об этом скажет первая настоящая нарезка, а не проба.
        return False


def _cut_chunk(
    conn: sqlite3.Connection,
    settings: Settings,
    job: sqlite3.Row,
    *,
    src: Path,
    dst: Path,
    start: float,
    end: float,
) -> None:
    """Один кусок звука на диск. Отмена доходит до ffmpeg: нарезка длинной записи не должна
    продолжаться после удаления ассета или остановки сервиса."""
    from server.worker.queue import is_canceled

    if dst.suffix == ".mp3":
        args = chunk_args(settings, src=str(src), dst=str(dst), start=start, end=end)
    else:
        # Тот же кусок без libmp3lame. Порядок ключей повторяет chunk_args: -ss и -to стоят после
        # -i, потому что времена чанка идут в транскрипт как есть и точность важнее скорости.
        args = [
            settings.ffmpeg_path, "-v", "error", "-y", "-i", str(src),
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
            "-vn", "-ac", "1", "-ar", CHUNK_RATE, "-c:a", "pcm_s16le", str(dst),
        ]
    run_streaming(
        args,
        timeout=settings.analyze_timeout_sec,
        on_line=lambda _line: None,
        should_stop=lambda: is_canceled(conn, job["id"]),
    )
    check_chunk_size(dst)


def _ask_provider(provider: TranscribeProvider, chunk: _Chunk, language: str) -> dict:
    """Всё, что делает рабочий поток: читает файл и ходит по сети.

    До базы отсюда не дотянуться намеренно — sqlite3.Connection не потокобезопасен. Прогресс и
    отмена пишутся в главном потоке между пачками.
    """
    return provider.transcribe(chunk.path.read_bytes(), chunk.path.name, language=language)


def _send_batch(
    provider: TranscribeProvider, batch: list[_Chunk], language: str
) -> list[tuple[_Chunk, dict | None, ProviderError | None]]:
    """Пачка чанков параллельно; отказ не поднимается сразу.

    Пул дожидаемся целиком: иначе поток пережил бы уборку и читал уже удалённый чанк. А решать,
    делить кусок или валить задание, всё равно предстоит главному потоку — здесь только сеть.
    """
    outcomes: list[tuple[_Chunk, dict | None, ProviderError | None]] = []
    with ThreadPoolExecutor(max_workers=len(batch)) as pool:
        futures = [(chunk, pool.submit(_ask_provider, provider, chunk, language)) for chunk in batch]
        for chunk, future in futures:
            try:
                outcomes.append((chunk, future.result(), None))
            except ProviderError as exc:
                outcomes.append((chunk, None, exc))
    return outcomes


def handle_transcribe(conn: sqlite3.Connection, settings: Settings, job: sqlite3.Row) -> None:
    """Речь ассета в транскрипт: чанки по паузам, параллельная отправка, сверка границ по картам.

    Полутранскрипта не бывает: если хоть один чанк не расшифрован, задание падает целиком. По
    неполному тексту всё равно будут монтировать, не зная, что середина потеряна.
    """
    from server.worker.queue import set_progress

    asset = _asset(conn, job)
    if asset is None:
        raise MediaError("gone", "ассет удалён, расшифровывать нечего")
    if asset["status"] not in TRANSCRIBE_READY_STATUSES:
        raise MediaError(
            "asset_not_ready",
            f"ассет в статусе {asset['status']}: анализ не закончен, карты пауз ещё нет",
        )
    if not settings.transcribe_api_key:
        # Пустой ключ выключает функцию, а не роняет её в сеть: без ключа запрос уйдёт без
        # авторизации и вернётся отказом, потратив нарезку и трафик.
        raise MediaError(
            "transcription_unavailable", "транскрипция не настроена: ключ провайдера не задан"
        )
    if not asset["has_audio"]:
        raise MediaError("no_audio", "в ассете нет звука, расшифровывать нечего")

    folder = asset_dir(settings, asset["user_id"], asset["id"])
    analysis = _read_analysis(folder)
    silences = analysis.get("silences") or []
    dense = analysis.get("silences_dense") or []
    # Длительность берём у ассета: по ней живёт таймлайн, и транскрипт не должен кончаться позже
    # клипа. У ассета её нет только у испорченной строки — тогда верим карте пауз.
    duration = float(asset["duration"] or analysis.get("duration") or 0)
    if duration <= 0:
        raise MediaError("empty_asset", "у ассета нулевая длительность, расшифровывать нечего")
    if disk_free_bytes(settings.data_dir) < int(duration * TRANSCRIBE_BYTES_PER_SEC):
        raise MediaError("disk_low", "на диске мало места для расшифровки, освободите его и повторите")

    params = json.loads(job["params"] or "{}")
    language = params.get("language") or settings.transcribe_language
    provider_name = _provider_name(settings)
    wav = folder / WAV_NAME
    try:
        # Звук после анализа удалён (диск дороже), поэтому собираем заново из исходника.
        extract_wav(settings, _source(settings, asset), wav)
        suffix = ".mp3" if mp3_encoder_available(settings) else ".wav"
        plan = chunk_plan(
            duration=duration,
            silences=silences,
            target=settings.transcribe_chunk_sec,
            window=settings.transcribe_chunk_window_sec,
        )
        queue: list[_Chunk] = []
        for number, (start, end) in enumerate(plan):
            _stop_if_canceled(conn, job)
            path = folder / f"{CHUNK_PREFIX}{number:03d}{suffix}"
            _cut_chunk(conn, settings, job, src=wav, dst=path, start=start, end=end)
            queue.append(_Chunk(path, start, end))

        boundaries = [start for start, _ in plan[1:]]
        parts: list[list[dict]] = []
        at_once = settings.transcribe_concurrency
        total = len(queue)
        done = 0
        with transcribe_provider(settings) as provider:
            while queue:
                _stop_if_canceled(conn, job)
                batch, queue = queue[:at_once], queue[at_once:]
                oversized: list[_Chunk] = []
                for chunk, result, error in _send_batch(provider, batch, language):
                    if error is None:
                        parts.append(normalize_chunk(result, offset=chunk.start))
                        done += 1
                    elif error.kind == "too_large" and chunk.splittable:
                        oversized.append(chunk)
                    else:
                        raise MediaError(
                            "transcribe_failed", f"кусок {chunk.path.name} не расшифрован: {error}"
                        )
                set_progress(conn, job["id"], PROGRESS_SENT * done / total)
                for chunk in oversized:
                    # 413: режем пополам и отправляем обе половины. Середина становится ещё одним
                    # швом, поэтому попадает в boundaries — там тоже задваивается фраза.
                    middle = round((chunk.start + chunk.end) / 2, 3)
                    boundaries.append(middle)
                    halves = ((chunk.start, middle), (middle, chunk.end))
                    for mark, (begin, finish) in zip("ab", halves, strict=True):
                        half = chunk.path.with_name(f"{chunk.path.stem}{mark}{chunk.path.suffix}")
                        _cut_chunk(conn, settings, job, src=wav, dst=half, start=begin, end=finish)
                        queue.append(_Chunk(half, begin, finish, splittable=False))
                    chunk.path.unlink(missing_ok=True)  # он больше не нужен, а диск не резиновый
                    total += 1

        segments = [segment for part in parts for segment in part]
        segments, seams = fix_seams(segments, boundaries=boundaries)
        segments = clamp_segments(segments, duration=duration)
        segments = mark_suspect(segments)
        segments, verified = verify_segment_boundaries(segments, dense)
        for number, segment in enumerate(segments, start=1):
            segment["id"] = number
            # Слова размечаются после сверки: интерполяция делит уже уточнённый отрезок.
            segment["words"] = interpolate_words(segment, silences=dense)

        stats = {
            **verified,
            "seams_fixed": seams["fixed"],
            "seams_dropped": seams["dropped"],
            "chunks": total,
        }
        _write_json(folder / TRANSCRIPT_NAME, {
            "asset_id": asset["id"],
            "provider": provider_name,
            "model": settings.transcribe_model,
            "language": language,
            "duration": round(duration, 3),
            "segments": segments,
            "silences": silences,
            "silences_dense": dense,
            "stats": stats,
        })
    finally:
        # Убираем и после успеха, и после отказа: звук с чанками весит больше самого исходника.
        wav.unlink(missing_ok=True)
        for leftover in folder.glob(f"{CHUNK_PREFIX}*"):
            leftover.unlink(missing_ok=True)

    with transaction(conn):
        conn.execute(
            "INSERT INTO transcripts (asset_id, user_id, provider, model, language, duration, "
            "segments, stats, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            # Повторная расшифровка заменяет строку: транскрипт у ассета один, и это тот, что
            # лежит в transcript.json.
            "ON CONFLICT(asset_id) DO UPDATE SET provider = excluded.provider, "
            "model = excluded.model, language = excluded.language, duration = excluded.duration, "
            "segments = excluded.segments, stats = excluded.stats, created_at = excluded.created_at",
            (
                asset["id"], asset["user_id"], provider_name, settings.transcribe_model,
                language, round(duration, 3), len(segments),
                json.dumps(stats, ensure_ascii=False), now_iso(),
            ),
        )
    set_progress(conn, job["id"], 1.0)
    log.info(
        "transcribe: %s готов (%d сегментов, %d кусков, границ подтверждено %d%%)",
        asset["id"], len(segments), total, stats["verified_pct"],
    )


HANDLERS = {
    "analyze": handle_analyze,
    "proxy": handle_proxy,
    "render": handle_render,
    "transcribe": handle_transcribe,
}
