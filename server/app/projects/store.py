"""Проекты в базе: создание, чтение, сохранение целиком с версией, удаление, завершение.

Документ хранится одной строкой JSON: он всегда читается и пишется целиком, точечных операций
«добавь клип» нет по решению из раздела 2 спеки.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3

from server.app.config import Settings
from server.app.projects.doc import AssetInfo, ProjectInvalid, validate_doc
from server.app.projects.snap import snap_clips
from server.app.storage import asset_dir
from server.app.util import new_id, now_iso
from server.db.core import transaction

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

    Рендеры появятся в M3 и будут удаляться здесь же.
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
