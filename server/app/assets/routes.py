"""Ассеты: список, карточка, удаление, одноразовая загрузка мелких файлов (SRT, музыка до 64 МБ),
транскрипт ассета (раздел 10 спеки).

Про транскрипт: строка в `transcripts` — указатель, `transcript.json` рядом с исходником — само
содержимое. Разъехаться они могут только после сбоя между двумя записями, поэтому удаление убирает
и то, и другое, а чтение отвечает по файлу.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from server.app.assets.views import AssetView, asset_view
from server.app.auth.deps import CurrentUser, current_user
from server.app.config import Settings
from server.app.errors import ApiError
from server.app.files import touch_last_access
from server.app.jobs import cancel_jobs_for_target, enqueue_job
from server.app.projects.store import projects_using_asset
from server.app.storage import asset_dir, upload_path
from server.app.uploads.routes import api_error
from server.app.uploads.store import UploadError, check_capacity, clean_filename, finalize_file, resolve_kind
from server.app.util import new_id, now_iso
from server.db.core import get_db, transaction
from server.media.subs import to_srt, to_vtt
from server.media.transcribe import clamp_segments

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])

READ_PIECE = 1024 * 1024
# Те же значения, что у обработчика воркера (server/worker/handlers.py): приложение о воркере не
# знает и знать не должно, а имя файла и «готов к расшифровке» обязаны совпадать.
TRANSCRIPT_NAME = "transcript.json"
TRANSCRIBE_READY_STATUSES = ("ready", "proxy_ready")
UPLOADED = "uploaded"  # провайдер и источник чужого транскрипта


class AssetList(BaseModel):
    assets: list[AssetView]


def get_asset(conn: sqlite3.Connection, user_id: str, asset_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM assets WHERE id = ? AND user_id = ?", (asset_id, user_id)).fetchone()


def _owned(conn: sqlite3.Connection, user: CurrentUser, asset_id: str) -> sqlite3.Row:
    row = get_asset(conn, user.id, asset_id)
    if row is None:
        raise ApiError(404, "not_found", "Ассет не найден")
    return row


def has_transcript(conn: sqlite3.Connection, user_id: str, asset_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM transcripts WHERE asset_id = ? AND user_id = ?", (asset_id, user_id)
    ).fetchone()
    return row is not None


def transcribed_assets(conn: sqlite3.Connection, user_id: str) -> set[str]:
    """Ассеты с транскриптом — одним запросом на весь список, а не по запросу на карточку."""
    rows = conn.execute("SELECT asset_id FROM transcripts WHERE user_id = ?", (user_id,))
    return {row["asset_id"] for row in rows}


@router.get("", response_model=AssetList)
def list_(
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AssetList:
    rows = conn.execute("SELECT * FROM assets WHERE user_id = ? ORDER BY created_at DESC, id", (user.id,))
    transcribed = transcribed_assets(conn, user.id)
    return AssetList(assets=[asset_view(r, has_transcript=r["id"] in transcribed) for r in rows])


@router.post("/upload", status_code=201, response_model=AssetView)
# Тело разбирает Starlette до входа сюда, поэтому наш лимит отсекает файл уже после приёма:
# настоящий предел стоит на Caddy (request_body max_size 68MB на этом маршруте).
async def upload_small(
    request: Request,
    file: UploadFile,
    kind: Annotated[str | None, Form()] = None,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AssetView:
    """Файл целиком одним запросом. Пишется во временный файл рядом с загрузками,
    дальше тот же finalize_file."""
    settings = request.app.state.settings
    if not request.app.state.upload_limiter.allow(user.id):
        raise ApiError(429, "rate_limited", "Слишком много новых загрузок, подождите час")
    tmp = upload_path(settings, new_id("tmp"))
    tmp.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        filename = clean_filename(file.filename or "")
        resolved = resolve_kind(filename, kind)
        # open/write в потоке: блокирующий файловый ввод-вывод не должен стопорить event loop.
        out = await run_in_threadpool(open, tmp, "wb")
        try:
            while piece := await file.read(READ_PIECE):
                size += len(piece)
                if size > settings.small_upload_max_bytes:
                    raise UploadError(
                        413,
                        "too_large",
                        "Файл больше допустимого для одноразовой загрузки",
                        {"limit_bytes": settings.small_upload_max_bytes},
                    )
                await run_in_threadpool(out.write, piece)
        finally:
            await run_in_threadpool(out.close)
        check_capacity(conn, settings, user.id, size)
        row = finalize_file(
            conn, settings, user_id=user.id, src=tmp, filename=filename, size=size, kind=resolved,
            check_quota=True,
        )
    except UploadError as exc:
        tmp.unlink(missing_ok=True)
        raise api_error(exc) from exc
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return asset_view(_owned(conn, user, row["id"]))


@router.get("/{asset_id}", response_model=AssetView)
def get_(
    asset_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AssetView:
    row = _owned(conn, user, asset_id)
    return asset_view(row, has_transcript=has_transcript(conn, user.id, asset_id))


@router.delete("/{asset_id}", status_code=204)
def delete(
    asset_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    """Сначала запись, потом файлы: упавший процесс не оставит запись без файлов, папку подберёт janitor.
    Ассет, занятый в незавершённом проекте, не удаляется."""
    _owned(conn, user, asset_id)
    with transaction(conn):
        # Проверка занятости внутри транзакции: BEGIN IMMEDIATE сериализует нас с сохранением проекта,
        # иначе между проверкой и удалением кто-то успел бы сослаться на этот ассет.
        used_by = projects_using_asset(conn, user.id, asset_id)
        if used_by:
            raise ApiError(409, "asset_in_use", "Файл стоит в проекте", {"projects": used_by})
        cur = conn.execute("DELETE FROM assets WHERE id = ? AND user_id = ?", (asset_id, user.id))
        if cur.rowcount == 0:
            raise ApiError(404, "not_found", "Ассет не найден")
        cancel_jobs_for_target(conn, asset_id)
    shutil.rmtree(asset_dir(request.app.state.settings, user.id, asset_id), ignore_errors=True)
    return Response(status_code=204)


# ── Транскрипт (раздел 10 спеки) ───────────────────────────────────────────────────────────────


class TranscribeRequest(BaseModel):
    # Язык необязателен: по умолчанию берётся язык из настроек сервиса.
    language: str | None = Field(default=None, max_length=16)


class TranscribeQueued(BaseModel):
    job_id: str
    language: str


class TranscriptSegment(BaseModel):
    """Сегмент чужого транскрипта. Лишние поля отбрасываются: id, флаги верификации и suspect
    ставим сами, а верить чужому «граница подтверждена» не с чего — мы её не проверяли."""

    start: float
    end: float
    text: str
    words: list[dict] | None = None


class TranscriptUpload(BaseModel):
    segments: list[TranscriptSegment]
    language: str | None = Field(default=None, max_length=16)


def _transcript_path(settings: Settings, asset: sqlite3.Row) -> Path:
    return asset_dir(settings, asset["user_id"], asset["id"]) / TRANSCRIPT_NAME


def _write_json(path: Path, data: dict) -> None:
    """Через временный файл: читатель никогда не увидит половину JSON."""
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _pause_maps(folder: Path) -> dict[str, list]:
    """Карты пауз от анализа. Транскрипт носит их в себе (спека §10.8), чтобы агент забирал текст и
    паузы одним запросом; нет файла — пустые списки, чужой транскрипт от этого не портится."""
    maps: dict[str, list] = {"silences": [], "silences_dense": []}
    try:
        data = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return maps
    if not isinstance(data, dict):
        return maps
    for name in ("silences", "silences_dense"):
        value = data.get(name)
        maps[name] = value if isinstance(value, list) else []
    return maps


def _uploaded_segments(items: list[TranscriptSegment]) -> list[dict]:
    """Сегменты запроса в наш формат, по возрастанию времени.

    Слова переносятся как есть и без пометки `interpolated`: у чужого транскрипта времена
    настоящие, по ним можно резать, и путать их с нашими интерполированными нельзя.
    """
    out: list[dict] = []
    for number, item in enumerate(items, start=1):
        text = item.text.strip()
        if not text or item.end <= item.start:
            raise ApiError(
                422,
                "invalid_transcript",
                f"Сегмент {number}: нужен непустой text и end больше start",
            )
        segment: dict = {
            "start": round(item.start, 3), "end": round(item.end, 3), "text": text,
            "start_verified": False, "end_verified": False, "suspect": False,
        }
        if item.words is not None:
            segment["words"] = item.words
        out.append(segment)
    out.sort(key=lambda one: (one["start"], one["end"]))
    return out


@router.post("/{asset_id}/transcribe", status_code=202, response_model=TranscribeQueued)
def transcribe(
    asset_id: str,
    body: TranscribeRequest,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> TranscribeQueued:
    """Ставит расшифровку в очередь; ход виден в задании, результат — в ручке транскрипта.

    Порядок проверок повторяет обработчик воркера: один и тот же запрос обязан отказывать
    одинаково и здесь, и там.
    """
    asset = _owned(conn, user, asset_id)
    settings = request.app.state.settings
    if asset["status"] not in TRANSCRIBE_READY_STATUSES:
        raise ApiError(422, "asset_not_ready", "Ассет ещё обрабатывается, карты пауз пока нет")
    if not settings.transcribe_api_key:
        # Пустой ключ выключает функцию, а не роняет её в сеть: без ключа запрос уйдёт без
        # авторизации и вернётся отказом, потратив нарезку и трафик.
        raise ApiError(503, "transcription_unavailable", "Транскрипция не настроена")
    if not asset["has_audio"]:
        raise ApiError(422, "no_audio", "В ассете нет звука, расшифровывать нечего")
    language = body.language or settings.transcribe_language
    with transaction(conn):
        # BEGIN IMMEDIATE сериализует нас со вторым таким же запросом: иначе два клика подряд
        # поставили бы два задания и дважды заплатили провайдеру за один и тот же звук.
        if has_transcript(conn, user.id, asset_id):
            raise ApiError(
                409, "transcript_exists", "Транскрипт уже есть, удалите его перед новой расшифровкой"
            )
        _refuse_while_transcribing(conn, asset_id)
        job_id = enqueue_job(
            conn, user_id=user.id, type_="transcribe", target_id=asset_id,
            params={"language": language},
        )
    return TranscribeQueued(job_id=job_id, language=language)


@router.get("/{asset_id}/transcript")
def transcript(
    asset_id: str,
    request: Request,
    fmt: Annotated[Literal["json", "srt", "vtt"], Query(alias="format")] = "json",
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    """Транскрипт во времени исходника: json как есть, srt и vtt собираются из него на лету."""
    asset = _owned(conn, user, asset_id)
    try:
        raw = _transcript_path(request.app.state.settings, asset).read_text(encoding="utf-8")
    except OSError as exc:
        raise ApiError(404, "not_found", "Транскрипта нет") from exc
    touch_last_access(conn, asset_id)
    if fmt == "json":
        # Файлом как есть: разбирать и собирать обратно многомегабайтный JSON незачем.
        return Response(content=raw, media_type="application/json")
    data = json.loads(raw)
    return Response(
        content=to_srt(data) if fmt == "srt" else to_vtt(data),
        media_type="text/plain; charset=utf-8",
    )


def _refuse_while_transcribing(conn: sqlite3.Connection, asset_id: str) -> None:
    """Пока задание в очереди или выполняется, транскрипт трогать нельзя.

    Второй запуск — это лишний счёт провайдеру, а загрузка своего транскрипта поверх идущего
    задания молча пропала бы: доехавший воркер перезаписал бы файл, и человек об этом не узнал.
    """
    queued = conn.execute(
        "SELECT 1 FROM jobs WHERE target_id = ? AND type = 'transcribe' AND status IN ('queued', 'running')",
        (asset_id,),
    ).fetchone()
    if queued is not None:
        raise ApiError(409, "already_queued", "Расшифровка этого ассета уже идёт")


@router.put("/{asset_id}/transcript")
def put_transcript(
    asset_id: str,
    body: TranscriptUpload,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> dict:
    """Свой транскрипт в нашем формате (спека §10.7).

    Ни швов, ни верификации границ: времена пришли настоящие, двигать их нечем и незачем. Остаётся
    проверка формата и клэмп к длительности ассета — субтитр не должен пережить сам клип.
    """
    asset = _owned(conn, user, asset_id)
    _refuse_while_transcribing(conn, asset_id)
    settings = request.app.state.settings
    duration = float(asset["duration"] or 0)
    if duration <= 0:
        raise ApiError(422, "asset_not_ready", "У ассета ещё нет длительности, дождитесь анализа")
    if not body.segments:
        raise ApiError(422, "invalid_transcript", "В транскрипте нет ни одного сегмента")
    segments = clamp_segments(_uploaded_segments(body.segments), duration=duration)
    if not segments:
        raise ApiError(422, "invalid_transcript", "Ни один сегмент не попал в длительность ассета")
    for number, segment in enumerate(segments, start=1):
        segment["id"] = number
    language = body.language or settings.transcribe_language
    folder = asset_dir(settings, asset["user_id"], asset_id)
    stats = {"source": UPLOADED}
    data = {
        "asset_id": asset_id,
        "provider": UPLOADED,
        "model": "",  # модели не было: транскрипт принесли готовым, чем — нам неизвестно
        "language": language,
        "duration": round(duration, 3),
        "segments": segments,
        **_pause_maps(folder),
        "stats": stats,
    }
    # Сначала файл, потом строка — как в воркере: строка без файла врёт, файл без строки переживается.
    folder.mkdir(parents=True, exist_ok=True)
    _write_json(folder / TRANSCRIPT_NAME, data)
    conn.execute(
        "INSERT INTO transcripts (asset_id, user_id, provider, model, language, duration, segments, "
        "stats, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(asset_id) DO UPDATE SET provider = excluded.provider, model = excluded.model, "
        "language = excluded.language, duration = excluded.duration, segments = excluded.segments, "
        "stats = excluded.stats, created_at = excluded.created_at",
        (
            asset_id, user.id, UPLOADED, "", language, round(duration, 3), len(segments),
            json.dumps(stats, ensure_ascii=False), now_iso(),
        ),
    )
    return data


@router.delete("/{asset_id}/transcript", status_code=204)
def delete_transcript(
    asset_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    """Убирает и строку, и файл. 404 только когда нет ни того, ни другого: после сбоя они могли
    разъехаться, и тогда удаление обязано вычистить остаток, а не сказать «удалять нечего»."""
    asset = _owned(conn, user, asset_id)
    cur = conn.execute(
        "DELETE FROM transcripts WHERE asset_id = ? AND user_id = ?", (asset_id, user.id)
    )
    path = _transcript_path(request.app.state.settings, asset)
    had_file = path.is_file()
    path.unlink(missing_ok=True)
    if cur.rowcount == 0 and not had_file:
        raise ApiError(404, "not_found", "Транскрипта нет")
    return Response(status_code=204)
