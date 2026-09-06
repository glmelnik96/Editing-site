"""Проекты в базе: создание, чтение, сохранение целиком с версией, удаление, завершение.

Документ хранится одной строкой JSON: он всегда читается и пишется целиком, точечных операций
«добавь клип» нет по решению из раздела 2 спеки.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path

from server.app.config import Settings
from server.app.projects.doc import AssetInfo, ProjectInvalid, validate_doc
from server.app.projects.snap import snap_clips
from server.app.storage import asset_dir, render_dir, render_url, subs_dir, transcript_path
from server.app.util import new_id, now_iso
from server.db.core import transaction
from server.media.cues import build_cues
from server.media.subs import cues_to_srt, cues_to_vtt
from server.media.timeline import words_through_clips

log = logging.getLogger("video.projects")

MAX_NAME = 200
EMPTY_DOC = {
    "output": {"aspect": "16:9", "fit": "pad", "fps": 30},
    "clips": [],
    "music": None,
    "subtitles": None,
}


class ProjectConflict(Exception):
    """Сохранение поверх чужой правки: у клиента устаревшая версия."""

    def __init__(self, project: dict) -> None:
        super().__init__("версия проекта устарела")
        self.project = project


class ProjectLimit(Exception):
    pass


def _row_to_project(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
        "doc": json.loads(row["doc"]),
    }


def _clean_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned or len(cleaned) > MAX_NAME:
        raise ProjectInvalid([{"field": "name", "message": f"имя проекта от 1 до {MAX_NAME} знаков"}])
    return cleaned


def _assets_index(conn: sqlite3.Connection, user_id: str) -> dict[str, AssetInfo]:
    rows = conn.execute("SELECT id, kind, status, duration FROM assets WHERE user_id = ?", (user_id,))
    return {r["id"]: AssetInfo(kind=r["kind"], status=r["status"], duration=r["duration"]) for r in rows}


def assets_of(doc: dict) -> set[str]:
    """Все ассеты, на которые ссылается документ: клипы, музыка, субтитры."""
    used = {c["asset_id"] for c in doc.get("clips") or []}
    for key in ("music", "subtitles"):
        block = doc.get(key)
        if isinstance(block, dict) and block.get("asset_id"):
            used.add(block["asset_id"])
    return used


def _prepare(conn: sqlite3.Connection, settings: Settings, user_id: str, raw_doc: object) -> dict:
    """Проверка документа плюс подтяжка резов к паузам."""
    if raw_doc is None:
        return json.loads(json.dumps(EMPTY_DOC))
    doc = validate_doc(raw_doc, assets=_assets_index(conn, user_id), settings=settings)
    snap_clips(doc["clips"], settings=settings, user_id=user_id)
    return doc


def _touch_assets(conn: sqlite3.Connection, doc: dict) -> None:
    """Проект держит ассеты живыми: janitor чистит по последнему обращению (раздел 3 спеки)."""
    used = assets_of(doc)
    if used:
        marks = ",".join("?" * len(used))
        conn.execute(
            f"UPDATE assets SET last_access_at = ? WHERE id IN ({marks})",
            (now_iso(), *used),
        )


def create_project(
    conn: sqlite3.Connection, settings: Settings, user_id: str, *, name: str, raw_doc: object
) -> dict:
    name = _clean_name(name)
    doc = _prepare(conn, settings, user_id, raw_doc)
    now = now_iso()
    project_id = new_id("prj")
    with transaction(conn):
        count = conn.execute(
            "SELECT count(*) FROM projects WHERE user_id = ? AND status = 'draft'", (user_id,)
        ).fetchone()[0]
        if count >= settings.max_projects_per_user:
            raise ProjectLimit(f"больше {settings.max_projects_per_user} проектов в работе")
        conn.execute(
            "INSERT INTO projects (id, user_id, name, version, doc, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, 'draft', ?, ?)",
            (project_id, user_id, name, json.dumps(doc, ensure_ascii=False), now, now),
        )
        _touch_assets(conn, doc)
    return {
        "id": project_id, "name": name, "version": 1, "status": "draft",
        "created_at": now, "updated_at": now, "finished_at": None, "doc": doc,
    }


def get_project(conn: sqlite3.Connection, user_id: str, project_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id)
    ).fetchone()
    return _row_to_project(row) if row else None


def list_projects(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """Карточки без документа: список проектов не должен тащить сотни клипов."""
    rows = conn.execute(
        "SELECT id, name, version, status, created_at, updated_at, finished_at, doc FROM projects "
        "WHERE user_id = ? ORDER BY updated_at DESC, id",
        (user_id,),
    )
    out = []
    for row in rows:
        doc = json.loads(row["doc"])
        clips = doc.get("clips") or []
        out.append({
            "id": row["id"], "name": row["name"], "version": row["version"], "status": row["status"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "finished_at": row["finished_at"],
            "clips_count": len(clips),
            "duration": round(sum(c["out"] - c["in"] for c in clips), 3),
        })
    return out


def save_project(
    conn: sqlite3.Connection, settings: Settings, user_id: str, project_id: str, *,
    name: str, raw_doc: object, version: int,
) -> dict:
    current = get_project(conn, user_id, project_id)
    if current is None:
        raise KeyError(project_id)
    if current["status"] != "draft":
        raise ProjectInvalid([{"field": "status", "message": "завершённый проект не редактируется"}])
    if current["version"] != version:
        raise ProjectConflict(current)
    name = _clean_name(name)
    doc = _prepare(conn, settings, user_id, raw_doc)
    now = now_iso()
    with transaction(conn):
        cur = conn.execute(
            "UPDATE projects SET name = ?, doc = ?, version = version + 1, updated_at = ? "
            "WHERE id = ? AND user_id = ? AND version = ? AND status = 'draft'",
            (name, json.dumps(doc, ensure_ascii=False), now, project_id, user_id, version),
        )
        if cur.rowcount == 0:
            # Кто-то тронул проект между нашей проверкой и записью: либо сохранил (тогда версия
            # другая), либо завершил (тогда причина не в версии, и клиенту надо сказать именно это).
            fresh = get_project(conn, user_id, project_id) or current
            if fresh["status"] != "draft":
                raise ProjectInvalid([{"field": "status", "message": "завершённый проект не редактируется"}])
            raise ProjectConflict(fresh)
        _touch_assets(conn, doc)
    return {
        "id": project_id, "name": name, "version": version + 1, "status": "draft",
        "created_at": current["created_at"], "updated_at": now, "finished_at": None, "doc": doc,
    }


def delete_project(conn: sqlite3.Connection, user_id: str, project_id: str) -> bool:
    with transaction(conn):
        cur = conn.execute(
            "DELETE FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id)
        )
    return cur.rowcount > 0


def assets_in_drafts(conn: sqlite3.Connection) -> set[str]:
    """Все ассеты, на которые ссылаются незавершённые проекты любого владельца.

    Нужен janitor: файл, стоящий в черновике, не должен исчезнуть по сроку последнего обращения.
    Пользователь мог неделю не открывать проект, но монтаж от этого не перестал существовать.
    """
    used: set[str] = set()
    for row in conn.execute("SELECT doc FROM projects WHERE status = 'draft'"):
        try:
            used |= assets_of(json.loads(row["doc"]))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return used


def projects_using_asset(conn: sqlite3.Connection, user_id: str, asset_id: str) -> list[dict]:
    """Незавершённые проекты владельца, где встречается ассет. Документов мало, ищем перебором."""
    rows = conn.execute(
        "SELECT id, name, doc FROM projects WHERE user_id = ? AND status = 'draft'", (user_id,)
    )
    return [
        {"id": r["id"], "name": r["name"]}
        for r in rows
        if asset_id in assets_of(json.loads(r["doc"]))
    ]


def finish_project(conn: sqlite3.Connection, settings: Settings, user_id: str, project_id: str) -> dict:
    """Завершение: проект остаётся историей, а его ассеты удаляются, если больше нигде не нужны.

    Рендеры проекта удаляются вместе с файлами: документ сохраняется, ролик пересобирается.
    """
    project = get_project(conn, user_id, project_id)
    if project is None:
        raise KeyError(project_id)
    now = now_iso()
    if project["status"] == "draft":
        with transaction(conn):
            conn.execute(
                "UPDATE projects SET status = 'finished', finished_at = ?, updated_at = ? WHERE id = ?",
                (now, now, project_id),
            )
        project = {**project, "status": "finished", "finished_at": now, "updated_at": now}
    # Рендеры завершённого проекта не нужны никому: они собираются заново из документа.
    delete_project_renders(conn, settings, user_id, project_id)
    for asset_id in sorted(assets_of(project["doc"])):
        with transaction(conn):
            # Проверяем занятость и удаляем в одной транзакции: иначе ассет успеет попасть
            # в чужой проект между проверкой и удалением.
            if projects_using_asset(conn, user_id, asset_id):
                continue
            conn.execute("DELETE FROM assets WHERE id = ? AND user_id = ?", (asset_id, user_id))
        folder = asset_dir(settings, user_id, asset_id)
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():
            # Записи уже нет, а каталог остался: место займёт janitor, но знать об этом надо.
            log.warning("не удалось удалить каталог ассета %s", folder)
    return project


MAX_LABEL = 200


def _version_row(row: sqlite3.Row) -> dict:
    doc = json.loads(row["doc"])
    clips = doc.get("clips") or []
    return {
        "id": row["id"],
        "version": row["version"],
        "label": row["label"],
        "name": row["name"],
        "created_at": row["created_at"],
        "clips_count": len(clips),
        "duration": round(sum(c["out"] - c["in"] for c in clips), 3),
    }


def create_checkpoint(
    conn: sqlite3.Connection, settings: Settings, user_id: str, project_id: str, *, label: str
) -> dict:
    """Снимок текущего состояния проекта. Старые снимки сверх пула вытесняются."""
    label = (label or "").strip()
    if len(label) > MAX_LABEL:
        raise ProjectInvalid([{"field": "label", "message": f"имя точки не длиннее {MAX_LABEL} знаков"}])
    project = get_project(conn, user_id, project_id)
    if project is None:
        raise KeyError(project_id)
    if project["status"] != "draft":
        raise ProjectInvalid([{"field": "status", "message": "завершённый проект не сохраняется"}])
    row_id = new_id("pvr")
    now = now_iso()
    with transaction(conn):
        conn.execute(
            "INSERT INTO project_versions (id, project_id, user_id, version, label, name, doc, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id, project_id, user_id, project["version"], label, project["name"],
                json.dumps(project["doc"], ensure_ascii=False), now,
            ),
        )
        # Пул маленький: держим только самые свежие точки этого проекта.
        conn.execute(
            "DELETE FROM project_versions WHERE project_id = ? AND id NOT IN "
            "(SELECT id FROM project_versions WHERE project_id = ? ORDER BY rowid DESC LIMIT ?)",
            (project_id, project_id, settings.versions_kept),
        )
    return {
        "id": row_id, "version": project["version"], "label": label, "name": project["name"],
        "created_at": now, "clips_count": len(project["doc"].get("clips") or []),
        "duration": round(sum(c["out"] - c["in"] for c in project["doc"].get("clips") or []), 3),
    }


def list_versions(conn: sqlite3.Connection, user_id: str, project_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM project_versions WHERE project_id = ? AND user_id = ? ORDER BY rowid DESC",
        (project_id, user_id),
    )
    return [_version_row(r) for r in rows]


def restore_version(
    conn: sqlite3.Connection, settings: Settings, user_id: str, project_id: str, version_id: str
) -> dict:
    """Возврат к точке: снимок применяется как обычное сохранение, поэтому версия растёт,
    а сама точка остаётся в пуле — откатить откат тоже можно."""
    row = conn.execute(
        "SELECT * FROM project_versions WHERE id = ? AND project_id = ? AND user_id = ?",
        (version_id, project_id, user_id),
    ).fetchone()
    if row is None:
        raise KeyError(version_id)
    current = get_project(conn, user_id, project_id)
    if current is None:
        raise KeyError(project_id)
    return save_project(
        conn, settings, user_id, project_id,
        name=row["name"], raw_doc=json.loads(row["doc"]), version=current["version"],
    )


# ── Субтитры проекта из расшифровки (спека §10.9) ──────────────────────────────────────────────

# Ширина строки по пропорции кадра: в вертикальном кадре длинная строка уезжает за край.
SUB_CHARS_BY_ASPECT = {"16:9": 42, "9:16": 24, "1:1": 32}
SUB_LINES = 2
SUB_MAX_DUR = 4.0


class SubtitlesUnavailable(Exception):
    """Субтитры проекта нечем собрать: у исходника нет расшифровки.

    Свой тип, а не MediaError: ту же сборку зовёт HTTP-ручка, и ей нужен свой код ответа, а не
    ошибка запуска ffmpeg.
    """


def _write_atomic(path: Path, text: str) -> None:
    """Через временный файл: ffmpeg не должен открыть половину субтитров.

    В имени временного файла стоит pid: черновик и финал одной версии собираются одновременно,
    и общий «.part» они отобрали бы друг у друга.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.part")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _owner_of(conn: sqlite3.Connection, project_id: str) -> str:
    """Владелец проекта: от него строятся пути к файлам, а в карточке проекта его нет."""
    row = conn.execute("SELECT user_id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise KeyError(project_id)
    return row["user_id"]


def _newer(source: Path, cached: Path) -> bool:
    """Расшифровку могли переделать при той же версии проекта: тогда кэш устарел."""
    try:
        return source.stat().st_mtime > cached.stat().st_mtime
    except OSError:
        return True  # исходника не видно — пусть сборка разберётся и скажет внятно


def build_project_subtitles(
    conn: sqlite3.Connection, settings: Settings, project: dict
) -> Path | None:
    """Субтитры проекта в кэш `subs/{version}.srt` и `.vtt`; возвращает путь к `.srt`.

    None — субтитров в документе нет или они из загруженного файла: тот уже лежит рядом с ассетом,
    собирать нечего.

    Реплики режутся из слов, пересчитанных через клипы: транскрипт живёт во времени исходника, а в
    ролике от исходника остались только выбранные куски и стоят они в другом порядке. Возьми слова
    как есть — и субтитры разъедутся с картинкой.
    """
    doc = project.get("doc") or {}
    subtitles = doc.get("subtitles")
    if not subtitles or subtitles.get("source") != "transcript":
        return None
    owner = _owner_of(conn, project["id"])
    folder = subs_dir(settings, owner, project["id"])
    version = project["version"]
    srt, vtt = folder / f"{version}.srt", folder / f"{version}.vtt"
    # Ассет субтитров принадлежит владельцу проекта: документ проверялся по его же ассетам.
    asset_id = subtitles["asset_id"]
    source = transcript_path(settings, owner, asset_id)
    if srt.exists() and vtt.exists() and not _newer(source, srt):
        # Версия растёт с каждым сохранением, поэтому файл этой версии собран из этого же
        # документа. Но версия следит за документом, а не за расшифровкой: её могли заказать
        # заново при той же версии, и тогда кэш пришлось бы отдавать устаревшим.
        return srt


    try:
        transcript = json.loads(
            transcript_path(settings, owner, asset_id).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        # Пустой путь до ffmpeg доводить нельзя: он упал бы на открытии файла, и в карточке
        # задания оказалась бы ругань кодека вместо понятного «закажите расшифровку».
        raise SubtitlesUnavailable(
            "у файла нет расшифровки: закажите её и соберите проект заново"
        ) from exc

    aspect = (doc.get("output") or {}).get("aspect")
    cues = build_cues(
        words_through_clips(transcript, doc.get("clips") or [], asset_id=asset_id),
        # Пропорция проверена при сохранении; запасное значение — на испорченный документ.
        max_chars=SUB_CHARS_BY_ASPECT.get(aspect, SUB_CHARS_BY_ASPECT["16:9"]),
        max_lines=SUB_LINES,
        max_dur=SUB_MAX_DUR,
    )
    folder.mkdir(parents=True, exist_ok=True)
    _write_atomic(srt, cues_to_srt(cues))
    _write_atomic(vtt, cues_to_vtt(cues))
    return srt


def _render_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "quality": row["quality"],
        "size": row["size"],
        "duration": row["duration"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "download": render_url(row["user_id"], row["project_id"], row["id"]),
    }


def list_renders(conn: sqlite3.Connection, user_id: str, project_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM renders WHERE project_id = ? AND user_id = ? ORDER BY created_at DESC, id",
        (project_id, user_id),
    )
    return [_render_row(r) for r in rows]


def get_render(conn: sqlite3.Connection, user_id: str, render_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM renders WHERE id = ? AND user_id = ?", (render_id, user_id)
    ).fetchone()
    return _render_row(row) if row else None


def delete_render(conn: sqlite3.Connection, user_id: str, render_id: str) -> bool:
    """Сначала запись, потом файл: упавший процесс не оставит запись без файла."""
    row = conn.execute(
        "SELECT path FROM renders WHERE id = ? AND user_id = ?", (render_id, user_id)
    ).fetchone()
    if row is None:
        return False
    with transaction(conn):
        conn.execute("DELETE FROM renders WHERE id = ? AND user_id = ?", (render_id, user_id))
    Path(row["path"]).unlink(missing_ok=True)
    return True


def active_renders(conn: sqlite3.Connection, user_id: str) -> int:
    """Сколько сборок человек уже запустил: очередь плюс выполняющаяся."""
    return conn.execute(
        "SELECT count(*) FROM jobs WHERE user_id = ? AND type = 'render' "
        "AND status IN ('queued', 'running')",
        (user_id,),
    ).fetchone()[0]


def delete_project_renders(
    conn: sqlite3.Connection, settings: Settings, user_id: str, project_id: str
) -> int:
    """Удаляет все готовые ролики проекта вместе с файлами и каталогом."""
    rows = conn.execute(
        "SELECT id FROM renders WHERE project_id = ? AND user_id = ?", (project_id, user_id)
    ).fetchall()
    with transaction(conn):
        conn.execute(
            "DELETE FROM renders WHERE project_id = ? AND user_id = ?", (project_id, user_id)
        )
    folder = render_dir(settings, user_id, project_id)
    shutil.rmtree(folder, ignore_errors=True)
    if folder.exists():
        log.warning("не удалось удалить каталог рендеров %s", folder)
    return len(rows)
