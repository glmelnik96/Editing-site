# Общее рабочее пространство — план

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Все записи и все проекты видны и доступны всем пользователям сервиса; запись любого автора кладётся в любой проект; удаляет любой; запись вне проектов живёт 7 дней.

**Architecture:** Снимаем проверки «владелец или админ» на месте: автор (`user_id`) остаётся в строках и путях, файлы не переносятся, миграций нет. Файлы записи (паузы, расшифровка, исходник) везде берутся из папки её автора. Спека: `docs/superpowers/specs/2026-09-30-shared-workspace-design.md`.

**Tech Stack:** FastAPI + SQLite, воркер, pytest (`uv run python -m pytest -q` из корня), ruff (`uv run ruff check server tests tools`); клиент vanilla TS + vitest без jsdom (`npx vitest run`, `npx tsc --noEmit`, `npm run build` в `web/`).

**Правила:** коммитить только после явной отмашки владельца — шаги коммита выполняются одним заходом в конце. Клиентские тесты проверяет и `tsc` при сборке на проде, поэтому тест правится в той же задаче, что и код.

---

## Файлы

| Файл | Что меняется |
|---|---|
| `server/app/audit.py` | новый: строка журнала «кто, что, чьё» |
| `server/app/projects/routes.py` | `_owned` → `_project` (любой вошедший), автор в карточке, `?mine=1`, журнал удаления, записи проекта — общий список |
| `server/app/projects/store.py` | общий список с автором, продление записей при удалении и сохранении, индекс всех записей, `asset_owners`, паузы и расшифровки из папки автора, `projects_using_asset` по всем проектам, `get_render` удалён |
| `server/app/projects/doc.py` | тексты ошибок без «владельца» |
| `server/app/assets/views.py` | автор в карточке, `ASSET_SELECT`, `list_asset_views` |
| `server/app/assets/routes.py` | `_owned` → `_record` (любой), общий список, защита «стоит в проекте» по всем, журнал |
| `server/app/jobs.py` | `list_jobs`: общая очередь, идущие не вытесняются |
| `server/app/renders/routes.py` | ролики и задания общие, `?mine=1`, журнал |
| `server/app/conversions/routes.py` | чужая запись — 403 |
| `server/app/files.py` | файлы записей и роликов — любому, конверсии — автору; превью не продлевают |
| `server/app/admin/routes.py` | удалены `/admin/projects`, `/admin/assets` |
| `server/worker/handlers.py` | запись при сборке ищется по id |
| `server/app/config.py`, `.env.example`, `server/janitor/rules.py` | срок 168 ч |
| `web/src/assets.ts`, `project.ts`, `overview.ts` | автор в типах, `authorLabel`, `listAssets(mine)`, `saver.unsaved()` |
| `web/src/projects.ts`, `files.ts`, `home.ts` | общие списки с автором, подтверждения |
| `web/src/source.ts`, `editor.ts`, `sync.ts`, `main.ts` | автор в «Исходниках», подтягивание чужих правок |
| `web/src/convert.ts`, `settings.ts`, `work.ts` | конвертер — свои; тексты |
| `README.md`, `tools/agent_smoke.py` | описание; смоук удаляет запись |

---

### Task 1: Журнал удалений

**Files:**
- Create: `server/app/audit.py`
- Test: `tests/test_audit.py`

- [ ] **Step 1: Падающий тест** — `tests/test_audit.py`:

```python
"""Журнал удалений: кто, что и чьё — одной строкой."""
import logging

from server.app.audit import audit
from server.app.auth.deps import CurrentUser
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate


def test_audit_names_who_what_and_whose(tmp_path, caplog):
    conn = connect(tmp_path / "video.db")
    migrate(conn)
    conn.execute(
        "INSERT INTO users (id, email, name, created_at) "
        "VALUES ('usr_00000000000a', 'liza@ya.ru', 'Лиза', ?)",
        (now_iso(),),
    )
    user = CurrentUser(id="usr_00000000000b", email="gleb@ya.ru", name="Глеб", role="user", auth="token")
    with caplog.at_level(logging.INFO, logger="video.audit"):
        audit(conn, user, "удалил проект", "prj_000000000001 «Планёрка»", "usr_00000000000a")
        audit(conn, user, "удалил ролик", "rnd_000000000001", None)
    conn.close()
    assert caplog.messages == [
        "gleb@ya.ru (token) удалил проект prj_000000000001 «Планёрка», автор liza@ya.ru",
        "gleb@ya.ru (token) удалил ролик rnd_000000000001, автор неизвестен",
    ]
```

- [ ] **Step 2:** `uv run python -m pytest tests/test_audit.py -q` → ошибка импорта `server.app.audit`.

- [ ] **Step 3: Реализация** — `server/app/audit.py`:

```python
"""Журнал необратимых действий: кто что удалил или отменил и чьё это было.

Проекты, записи, ролики и задания общие: удалить и отменить может любой вошедший, а в базе после
удаления не остаётся ничего. Строка журнала сервера отвечает на вопрос «куда делся проект» —
раньше его приходилось восстанавливать по адресам в журнале uvicorn. Почта внутри команды не
секрет: ею же подписаны задания и карточки.
"""
from __future__ import annotations

import logging
import sqlite3

from server.app.auth.deps import CurrentUser

log = logging.getLogger("video.audit")


def audit(
    conn: sqlite3.Connection, user: CurrentUser, action: str, target: str, author_id: str | None
) -> None:
    """action — «удалил проект»; target — что именно, с id; author_id — чьё это было."""
    row = None
    if author_id:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (author_id,)).fetchone()
    author = row["email"] if row else "неизвестен"
    log.info("%s (%s) %s %s, автор %s", user.email, user.auth, action, target, author)
```

- [ ] **Step 4:** `uv run python -m pytest tests/test_audit.py -q` → PASS.

---

### Task 2: Проекты — общий доступ и общий список

**Files:**
- Modify: `server/app/projects/routes.py` (`ProjectCard`, `_owned`, все вызовы `_owned`, `list_`, `delete`, комментарии в `save`, `render`, `generate_subtitles`)
- Modify: `server/app/projects/store.py` (`_touch_assets`, `project_owner`, `list_projects`, `save_project`, `delete_project`)
- Test: `tests/test_projects_api.py`, `tests/test_project_store.py`, `tests/test_admin_api.py`, `tests/test_janitor.py`

- [ ] **Step 1: Падающие тесты.**

`tests/test_projects_api.py` — в `test_create_read_list_and_save` после строки `assert len(listing) == 1 and listing[0]["clips_count"] == 1 and "doc" not in listing[0]` добавить:

```python
    assert listing[0]["owner_email"] == me["email"] and listing[0]["owner_name"] == me["name"]
```

`test_foreign_project_is_404` заменить:

```python
def test_a_colleague_opens_saves_and_deletes_a_project(client, login_as, settings):
    """Проекты общие: коллега открывает, правит и удаляет чужой проект, правка ложится автору."""
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(f"/api/v1/projects/{p['id']}").status_code == 200
    r = client.put(f"/api/v1/projects/{p['id']}", json={"name": "Поправил коллега", "version": 1, "doc": doc()})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    listing = client.get("/api/v1/projects").json()["projects"]
    assert [(x["name"], x["owner_email"]) for x in listing] == [("Поправил коллега", me["email"])]
    assert client.delete(f"/api/v1/projects/{p['id']}").status_code == 204
    assert client.get("/api/v1/projects").json()["projects"] == []
```

`test_versions_of_a_foreign_project_are_404` заменить:

```python
def test_a_colleague_uses_versions_of_a_project(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    made = client.post(f"/api/v1/projects/{p['id']}/checkpoint", json={"label": "до правки"})
    assert made.status_code == 201, made.text
    labels = [v["label"] for v in client.get(f"/api/v1/projects/{p['id']}/versions").json()["versions"]]
    assert labels == ["до правки"]
    back = client.post(f"/api/v1/projects/{p['id']}/restore", json={"version_id": made.json()["id"]})
    assert back.status_code == 200, back.text
```

Добавить в конец файла:

```python
def test_list_is_the_whole_team_and_mine_keeps_only_mine(client, login_as, settings):
    login_as()
    first = client.post("/api/v1/projects", json={"name": "Админа"}).json()
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    second = client.post("/api/v1/projects", json={"name": "Коллеги"}).json()
    listing = client.get("/api/v1/projects").json()["projects"]
    assert [(x["id"], x["owner_email"], x["owner_name"]) for x in listing] == [
        (second["id"], "other@ya.ru", "Other"),
        (first["id"], "admin@ya.ru", "Admin"),
    ]
    assert [x["id"] for x in client.get("/api/v1/projects?mine=1").json()["projects"]] == [second["id"]]
```

`tests/test_project_store.py` — `test_get_and_list_are_scoped_to_the_owner` заменить:

```python
def test_get_is_keyed_by_author_and_list_shows_everyone(conn, settings):
    """Строка проекта ищется по автору (от него пути к файлам), а список — вся команда с автором."""
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    q = create_project(conn, settings, OTHER, name="Чужой", raw_doc=None)
    assert get_project(conn, USER, p["id"])["name"] == "Мой"
    assert get_project(conn, OTHER, p["id"]) is None
    assert {x["id"]: x["owner_email"] for x in list_projects(conn)} == {
        p["id"]: f"{USER}@ya.ru",
        q["id"]: f"{OTHER}@ya.ru",
    }
    assert [x["id"] for x in list_projects(conn, owner=OTHER)] == [q["id"]]
```

В `test_list_does_not_carry_the_whole_document` строку `row = list_projects(conn, USER)[0]` заменить на `row = list_projects(conn)[0]`. Добавить в конец файла:

```python
def test_deleting_a_project_frees_its_records_for_a_full_term(conn, settings):
    """Иначе записи давно не открытого проекта пропали бы при ближайшем проходе уборщика."""
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc("ast_000000000001"))
    conn.execute("UPDATE assets SET last_access_at = '2026-01-01T00:00:00.000Z'")
    delete_project(conn, settings, USER, p["id"])
    touched = conn.execute("SELECT last_access_at FROM assets WHERE id = 'ast_000000000001'").fetchone()[0]
    assert touched > "2026-01-01T00:00:00.000Z"


def test_a_record_removed_from_the_timeline_is_freed_for_a_full_term(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc("ast_000000000001"))
    conn.execute("UPDATE assets SET last_access_at = '2026-01-01T00:00:00.000Z'")
    save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=doc("ast_000000000002"), version=1)
    touched = conn.execute("SELECT last_access_at FROM assets WHERE id = 'ast_000000000001'").fetchone()[0]
    assert touched > "2026-01-01T00:00:00.000Z"
```

`tests/test_admin_api.py` — `test_admin_opens_and_edits_a_foreign_project` заменить:

```python
def test_a_colleague_opens_and_edits_a_project(login_as, settings):
    """Открыть чужой проект и поправить его может любой, не только админ.

    Работает он при этом от имени автора: файлы лежат в каталоге автора, и сохранение ищет строку
    по его id, а не по id вошедшего.
    """
    admin = login_as("admin@ya.ru")
    for email in ("user@ya.ru", "colleague@ya.ru"):
        admin.post("/api/v1/admin/whitelist", json={"email": email})
    user = login_as("user@ya.ru", "Пользователь")
    me = user.get("/api/v1/me").json()
    asset = _seed_asset(settings, me["id"])
    project = user.post(
        "/api/v1/projects",
        json={"name": "Планёрка", "doc": {"clips": [{"asset_id": asset, "in": 0, "out": 5}]}},
    ).json()

    colleague = login_as("colleague@ya.ru", "Коллега")
    assert colleague.get(f"/api/v1/projects/{project['id']}").status_code == 200
    seen = colleague.get(f"/api/v1/projects/{project['id']}/assets").json()["assets"]
    assert [a["id"] for a in seen] == [asset]
    saved = colleague.put(
        f"/api/v1/projects/{project['id']}",
        json={
            "name": "Планёрка (поправил коллега)",
            "version": project["version"],
            "doc": {"clips": [{"asset_id": asset, "in": 1, "out": 4}]},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == project["version"] + 1
    # Правка ушла автору, а не завелась вторым проектом у коллеги.
    listing = colleague.get("/api/v1/projects").json()["projects"]
    assert [(p["name"], p["owner_email"]) for p in listing] == [("Планёрка (поправил коллега)", "user@ya.ru")]
```

`test_a_foreign_project_is_still_invisible_to_everyone_else` заменить:

```python
def test_a_colleagues_project_is_open_to_everyone(login_as, settings):
    admin = login_as("admin@ya.ru")
    for email in ("one@ya.ru", "two@ya.ru"):
        admin.post("/api/v1/admin/whitelist", json={"email": email})
    one = login_as("one@ya.ru", "Первый")
    project = one.post("/api/v1/projects", json={"name": "Своё"}).json()

    two = login_as("two@ya.ru", "Второй")
    assert two.get(f"/api/v1/projects/{project['id']}").status_code == 200
    assert two.get(f"/api/v1/projects/{project['id']}/assets").status_code == 200
    assert two.delete(f"/api/v1/projects/{project['id']}").status_code == 204
```

`tests/test_janitor.py` — в `test_asset_used_by_a_project_survives_its_ttl` последние три строки (`delete_project(...)`, `assert ... == 1`, `assert not ...exists()`) заменить:

```python
    delete_project(conn, settings, USER, project["id"])
    # Удаление проекта освобождает запись на полный срок: она не пропадает при ближайшем проходе
    # только оттого, что проект давно не открывали.
    assert rules.delete_expired_assets(conn, settings, NOW) == 0
    later = NOW + timedelta(hours=settings.asset_ttl_hours + 1)
    assert rules.delete_expired_assets(conn, settings, later) == 1
    assert not asset_dir(settings, USER, asset).exists()
```

- [ ] **Step 2:** `uv run python -m pytest tests/test_projects_api.py tests/test_project_store.py tests/test_admin_api.py tests/test_janitor.py -q` → новые тесты падают (404 у коллеги, `list_projects` без `owner`, нет `owner_email`, запись не продлена).

- [ ] **Step 3: Реализация `store.py`.** `_touch_assets` заменить:

```python
def _touch_assets(conn: sqlite3.Connection, *docs: dict) -> None:
    """Документы держат записи живыми: janitor чистит по последнему обращению (раздел 3 спеки).

    Кроме нового документа сюда передают прежний: запись, которую убрали со шкалы или чей проект
    удалили, освобождается сегодня и живёт полный срок хранения, а не пропадает при ближайшем
    проходе уборщика оттого, что проект давно не открывали."""
    used: set[str] = set()
    for doc in docs:
        used |= assets_of(doc)
    if used:
        marks = ",".join("?" * len(used))
        conn.execute(
            f"UPDATE assets SET last_access_at = ? WHERE id IN ({marks})",
            (now_iso(), *used),
        )
```

Докстринг `project_owner`:

```python
    """Автор проекта. Проект открывает и правит любой из команды, но работает от имени автора:
    файлы лежат в его каталоге, и строки ищутся по его id."""
```

`list_projects` заменить:

```python
def list_projects(conn: sqlite3.Connection, *, owner: str | None = None) -> list[dict]:
    """Карточки без документа, свежие правки сверху; с owner — только его проекты.

    Проекты видит вся команда, поэтому в карточке автор: два «Ролика для сайта» иначе не различить.
    """
    where = "WHERE p.user_id = ? " if owner else ""
    rows = conn.execute(
        "SELECT p.id, p.name, p.version, p.created_at, p.updated_at, p.doc, "
        "u.email AS owner_email, u.name AS owner_name "
        f"FROM projects AS p JOIN users AS u ON u.id = p.user_id {where}"
        "ORDER BY p.updated_at DESC, p.id",
        (owner,) if owner else (),
    )
    out = []
    for row in rows:
        doc = json.loads(row["doc"])
        clips = doc.get("clips") or []
        out.append({
            "id": row["id"], "name": row["name"], "version": row["version"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "clips_count": len(clips),
            "duration": clips_duration(clips),
            "owner_email": row["owner_email"], "owner_name": row["owner_name"],
        })
    return out
```

В `save_project` строку `_touch_assets(conn, doc)` заменить на `_touch_assets(conn, doc, current["doc"])`. `delete_project` заменить:

```python
def delete_project(
    conn: sqlite3.Connection, settings: Settings, user_id: str, project_id: str
) -> bool:
    """Удаляет проект вместе с его каталогом на диске.

    Сначала запись, потом файлы, как везде: упавший процесс не оставит запись без файлов.
    В каталоге лежат готовые ролики и кэш субтитров — по файлу на каждую версию, для которой
    собирали ролик, так что без уборки он растёт с каждой правкой документа.

    Задания проекта отменяются в той же транзакции: внешнего ключа на проект у jobs нет, и без
    отмены идущая сборка кодирует ещё час впустую, а её INSERT в renders падает по ключу уже
    после того, как файл записан на диск.

    Записи проекта в той же транзакции продлеваются: освободившись, они живут полный срок
    хранения. Записи в проекте бывают и других авторов — их это касается так же.
    """
    with transaction(conn):
        row = conn.execute(
            "SELECT doc FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id)
        ).fetchone()
        if row is None:
            return False
        _touch_assets(conn, json.loads(row["doc"]))
        conn.execute("DELETE FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id))
        cancel_jobs_for_target(conn, project_id)
    shutil.rmtree(project_dir(settings, user_id, project_id), ignore_errors=True)
    return True
```

- [ ] **Step 4: Реализация `routes.py`.** Импорт `from server.app.audit import audit`. В `ProjectCard` после `duration: float`:

```python
    # Автор: проекты видит вся команда. Имена полей — как у заданий в /jobs.
    owner_email: str
    owner_name: str
```

`_owned` заменить на:

```python
def _project(conn: sqlite3.Connection, project_id: str) -> tuple[dict, str]:
    """Проект и его автор. Проект общий: открывает, правит, собирает и удаляет любой вошедший.

    Автора возвращаем отдельно, потому что дальше маршрут действует от его имени, а не от имени
    вызывающего: файлы проекта лежат в каталоге автора, а сохранение ищет строку по его id.
    """
    owner = project_owner(conn, project_id)
    project = get_project(conn, owner, project_id) if owner else None
    if owner is None or project is None:
        raise ApiError(404, "not_found", "Проект не найден")
    return project, owner
```

Все вызовы `_owned(conn, user, project_id)` в файле заменить на `_project(conn, project_id)` (`sed -i 's/_owned(conn, user, project_id)/_project(conn, project_id)/g'`). `list_` заменить:

```python
@router.get("", response_model=ProjectList)
def list_(
    mine: bool = False,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectList:
    """Все проекты команды, свежие правки сверху; ?mine=1 — только свои (для скриптов агента)."""
    owner = user.id if mine else None
    return ProjectList(projects=[ProjectCard(**p) for p in list_projects(conn, owner=owner)])
```

В `delete`: `_, owner = _project(...)` → `project, owner = _project(conn, project_id)`, перед `return Response(status_code=204)` строка:

```python
    audit(conn, user, "удалил проект", f"{project_id} «{project['name']}»", owner)
```

Комментарии «Проект удалили между проверкой владения и записью» (в `save` и `generate_subtitles`) → «Проект удалили между проверкой и записью». Комментарий в `render` над `conn, user_id=owner, …`:

```python
        # Задание и готовый ролик числятся за автором проекта, кто бы ни нажал «Собрать»: файл
        # ложится в его каталог, и путь /files/{автор}/projects/… иначе не сошёлся бы.
```

- [ ] **Step 5:** тесты шага 2 → PASS; `uv run python -m pytest -q` → всё зелёное.

---

### Task 3: Запись любого автора в проекте; её файлы — из папки автора

**Files:**
- Modify: `server/app/projects/store.py` (импорты, `_assets_index`, новые `asset_owners`, `_silences_by_asset`, `_prepare`, вызовы `_prepare`, `_read_transcript`, `cues_from_transcript`, `cues_from_timeline`, `generate_project_cues`, `build_project_subtitles`)
- Modify: `server/app/projects/doc.py:308,387,481` (тексты)
- Modify: `server/worker/handlers.py` (`_sources_for` и его вызов)
- Test: `tests/test_project_store.py`, `tests/test_project_subtitles.py`, `tests/test_project_subtitles_api.py`, `tests/test_worker_render.py`

- [ ] **Step 1: Падающие тесты.** `tests/test_project_store.py` — `test_save_rejects_a_foreign_asset` заменить двумя:

```python
def test_save_accepts_a_record_of_another_author(conn, settings):
    """Записи общие: в проект кладут запись любого автора."""
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, created_at, "
        "last_access_at) VALUES ('ast_00000000000f', ?, 'video', 'a', 'mp4', 1, 'ready', 9, ?, ?)",
        (OTHER, now_iso(), now_iso()),
    )
    p = create_project(conn, settings, USER, name="Мой", raw_doc=None)
    raw = doc("ast_00000000000f")
    saved = save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=raw, version=1)
    assert saved["doc"]["clips"][0]["asset_id"] == "ast_00000000000f"


def test_snapping_reads_pauses_from_the_record_authors_folder(conn, settings):
    """Паузы лежат в папке автора записи, а не автора проекта: иначе подтяжка молча не сработает."""
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, created_at, "
        "last_access_at) VALUES ('ast_00000000000f', ?, 'video', 'a', 'mp4', 1, 'ready', 9, ?, ?)",
        (OTHER, now_iso(), now_iso()),
    )
    folder = asset_dir(settings, OTHER, "ast_00000000000f")
    folder.mkdir(parents=True)
    (folder / "analysis.json").write_text(
        json.dumps({"silences_dense": [{"start": 4.0, "end": 5.0}]}), encoding="utf-8"
    )
    p = create_project(conn, settings, USER, name="Мой", raw_doc=None)
    raw = {"clips": [{"asset_id": "ast_00000000000f", "in": 1.0, "out": 4.1, "snap_to_pauses": True}]}
    saved = save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=raw, version=1)
    clip = saved["doc"]["clips"][0]
    assert clip["out"] == 4.3 and clip["out_verified"] is True
```

`tests/test_project_subtitles.py` — в конец файла:

```python
AUTHOR = "usr_00000000000b"


def test_cues_come_from_the_record_authors_folder(conn, settings):
    """Проект одного человека, запись другого: расшифровка лежит в папке автора записи."""
    conn.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES (?, 'b@b.c', 'B', ?)",
        (AUTHOR, now_iso()),
    )
    conn.execute("UPDATE assets SET user_id = ? WHERE id = ?", (AUTHOR, ASSET))
    asset_dir(settings, AUTHOR, ASSET).mkdir(parents=True, exist_ok=True)
    transcript_path(settings, AUTHOR, ASSET).write_text(
        json.dumps({"asset_id": ASSET, "duration": 120.0, "segments": [
            {"id": 1, "start": 0.0, "end": 3.3, "text": PHRASE, "words": words()},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    p = project(conn, settings)
    assert cue_lines(build_project_subtitles(conn, settings, p))
    saved = generate_project_cues(conn, settings, USER, p, mode="burn", version=p["version"])
    assert saved["doc"]["subtitles"]["cues"]


def test_a_deleted_record_is_a_clear_refusal_not_a_crash(conn, settings):
    p = project(conn, settings)
    conn.execute("DELETE FROM assets WHERE id = ?", (ASSET,))
    with pytest.raises(SubtitlesUnavailable, match="удалён"):
        build_project_subtitles(conn, settings, p)
```

`tests/test_project_subtitles_api.py` — `test_generate_on_a_foreign_project_is_404` и `test_foreign_project_is_404` заменить:

```python
def test_a_colleague_generates_cues_in_a_project(client, login_as, settings):
    """Проект общий: реплики собирает любой, расшифровка берётся у автора записи."""
    login_as()
    project = with_transcript(client, settings)
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert cues_of(generate(client, project))


def test_a_colleague_downloads_the_subtitles(client, login_as, settings):
    login_as()
    project = ready_project(client, settings)
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    r = subtitles(client, project)
    assert r.status_code == 200, r.text
    assert "-->" in r.text
```

`tests/test_worker_render.py` — в конец файла:

```python
AUTHOR = "usr_00000000000b"


def test_проект_собирается_из_записи_другого_автора(conn, settings, monkeypatch):
    """Записи общие: проект одного человека собирается из записи другого. Исходник — из папки
    автора записи, ролик ложится автору проекта."""
    conn.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES (?, 'b@b.c', 'B', ?)",
        (AUTHOR, now_iso()),
    )
    project = create_project(
        conn, settings, AUTHOR, name="Чужой",
        raw_doc={"clips": [{"asset_id": ASSET, "in": 1.0, "out": 6.0}]},
    )
    seen: dict = {}

    def run(args, *, timeout, on_line, should_stop=None, stop_check_sec=2.0):
        seen["args"] = [str(a) for a in args]
        on_line("out_time_us=2500000")
        with open(args[-1], "wb") as f:
            f.write(b"video")

    monkeypatch.setattr(handlers, "run_streaming", run)
    enqueue_job(conn, user_id=AUTHOR, type_="render", target_id=project["id"], params={"quality": "draft"})
    handlers.handle_render(conn, settings, claim_job(conn, lane="cpu", pid=1))
    assert str(asset_dir(settings, USER, ASSET) / "source.mp4") in seen["args"]
    row = conn.execute("SELECT user_id FROM renders WHERE project_id = ?", (project["id"],)).fetchone()
    assert row["user_id"] == AUTHOR
```

- [ ] **Step 2:** `uv run python -m pytest tests/test_project_store.py tests/test_project_subtitles.py tests/test_project_subtitles_api.py tests/test_worker_render.py -q` → новые падают (422 «ассет владельца», паузы не найдены, расшифровка не найдена, `asset_gone`).

- [ ] **Step 3: Реализация `store.py`.** Импорты: `from collections.abc import Callable, Iterable, Mapping` и `from server.app.projects.snap import load_silences, snap_clips`. `_assets_index` заменить и добавить две функции после него:

```python
def _assets_index(conn: sqlite3.Connection) -> dict[str, AssetInfo]:
    """Все записи команды: в проект кладут запись любого автора."""
    rows = conn.execute("SELECT id, kind, status, duration FROM assets")
    return {r["id"]: AssetInfo(kind=r["kind"], status=r["status"], duration=r["duration"]) for r in rows}


def asset_owners(conn: sqlite3.Connection, asset_ids: Iterable[str]) -> dict[str, str]:
    """Автор каждой записи: её файлы — паузы, расшифровка, исходник — лежат в его папке, а не у
    автора проекта. Записи, которой нет в базе, нет и в ответе: отказывает вызывающий."""
    ids = sorted(set(asset_ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id, user_id FROM assets WHERE id IN ({marks})", ids)
    return {r["id"]: r["user_id"] for r in rows}


def _silences_by_asset(
    conn: sqlite3.Connection, settings: Settings, clips: list[dict]
) -> dict[str, list[dict]]:
    """Карты пауз записей, к которым просят подтянуть резы, — из папки автора каждой записи."""
    wanted = {clip["asset_id"] for clip in clips if clip.get("snap_to_pauses")}
    owners = asset_owners(conn, wanted)
    return {
        asset_id: load_silences(settings, owners[asset_id], asset_id) if asset_id in owners else []
        for asset_id in wanted
    }
```

В `_prepare`: сигнатура `def _prepare(conn: sqlite3.Connection, settings: Settings, raw_doc: object) -> dict:`, `assets = _assets_index(conn)`, строку `snap_clips(doc["clips"], settings=settings, user_id=user_id)` заменить:

```python
    silences = _silences_by_asset(conn, settings, doc["clips"])
    snap_clips(doc["clips"], settings=settings, silences_by_asset=silences)
```

В `create_project` и `save_project`: `doc = _prepare(conn, settings, raw_doc)`. `_read_transcript` заменить:

```python
def _read_transcript(settings: Settings, author: str | None, asset_id: str) -> dict:
    """Расшифровка записи из папки её автора — или отказ.

    Пустой путь до ffmpeg доводить нельзя: он упал бы на открытии файла, и в карточке задания
    оказалась бы ругань кодека вместо понятного «закажите расшифровку». Неизвестный ассет — тот же
    отказ: путь к нему не строится, и расшифровки по нему всё равно нет. Записи нет в базе (author
    None) — её удалили, и заказывать расшифровку уже не у чего: отказ говорит об этом.
    """
    if author is None:
        raise SubtitlesUnavailable("файл удалён: уберите его со шкалы и соберите проект заново")
    try:
        return json.loads(transcript_path(settings, author, asset_id).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SubtitlesUnavailable(
            "у файла нет расшифровки: закажите её и соберите проект заново"
        ) from exc
```

`cues_from_transcript`: сигнатура `(settings: Settings, doc: dict, *, author: str | None, asset_id: str) -> list[dict]`, внутри `_read_transcript(settings, author, asset_id)`. `cues_from_timeline`: сигнатура `(settings: Settings, doc: dict, *, authors: Mapping[str, str]) -> list[dict]`, в докстринг строку «authors — автор каждой записи шкалы: расшифровка лежит в его папке.», внутри `_read_transcript(settings, authors.get(asset_id), asset_id)`. В `generate_project_cues`:

```python
    doc = project["doc"]
    authors = asset_owners(conn, clip_asset_ids(doc.get("clips") or []))
    cues = _cues_for_document(cues_from_timeline(settings, doc, authors=authors))
```

В `build_project_subtitles` ветку `else:` заменить:

```python
    else:
        # Запись субтитров бывает любого автора: расшифровка лежит в его папке, кэш — в папке
        # проекта у автора проекта.
        asset_id = subtitles["asset_id"]
        author = asset_owners(conn, [asset_id]).get(asset_id)
        if cached and author and not _newer(transcript_path(settings, author, asset_id), srt):
            # Версия растёт с каждым сохранением, поэтому файл этой версии собран из этого же
            # документа. Но версия следит за документом, а не за расшифровкой: её могли заказать
            # заново при той же версии, и тогда кэш пришлось бы отдавать устаревшим.
            return srt
        cues = cues_from_transcript(settings, doc, author=author, asset_id=asset_id)
```

- [ ] **Step 4: `doc.py`** — в трёх текстах убрать слово « владельца»: «музыкой может быть звуковой или видеоассет», «звуком может быть звуковой или видеоассет», «наложением может быть видеоассет или картинка».

- [ ] **Step 5: воркер** — `_sources_for` заменить:

```python
def _sources_for(conn: sqlite3.Connection, settings: Settings, project: dict) -> dict[str, SourceInfo]:
    """Пути к исходникам проекта. Записи в проекте бывают разных авторов: путь строится от автора
    каждой записи (row["user_id"]), а не от автора проекта. Ассет мог исчезнуть или откатиться в
    обработку с момента сохранения."""
    sources: dict[str, SourceInfo] = {}
    for asset_id in sorted(assets_of(project["doc"])):
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
```

(остаток тела без изменений); в `handle_render` вызов `sources = _sources_for(conn, settings, project)`.

- [ ] **Step 6:** тесты шага 2 → PASS; `uv run python -m pytest -q` → всё зелёное.

---

### Task 4: Записи — общий список, доступ и защита от удаления

**Files:**
- Modify: `server/app/assets/views.py` (`AssetView`, `asset_view`, новые `ASSET_SELECT`, `list_asset_views`)
- Modify: `server/app/assets/routes.py` (импорты, `_owned` → `_record`, `transcribed_assets` удалить, `list_`, `delete`)
- Modify: `server/app/projects/store.py` (`projects_using_asset`)
- Modify: `server/app/projects/routes.py` (импорт, `project_assets`)
- Test: `tests/test_assets_api.py`, `tests/test_projects_api.py`, `tests/test_project_store.py`, `tests/test_transcript_api.py`, `tests/test_admin_api.py`

- [ ] **Step 1: Падающие тесты.** `tests/test_assets_api.py` — в `_row` в `base` после `"last_access_at": …` добавить `"owner_email": "a@b.c", "owner_name": "A",`. `test_foreign_asset_is_404` заменить:

```python
def test_a_colleague_sees_and_deletes_a_free_record(client, login_as, settings):
    """Записи общие: коллега видит чужую запись с автором и может удалить свободную."""
    login_as()
    asset_id = _upload_small(client).json()["id"]
    me = client.get("/api/v1/me").json()
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    card = client.get(f"/api/v1/assets/{asset_id}").json()
    assert card["owner_email"] == me["email"] and card["owner_name"] == me["name"]
    assert [a["id"] for a in client.get("/api/v1/assets").json()["assets"]] == [asset_id]
    assert client.get("/api/v1/assets?mine=1").json()["assets"] == []
    assert client.delete(f"/api/v1/assets/{asset_id}").status_code == 204
    assert not (settings.data_dir / me["id"] / "assets" / asset_id).exists()
```

`tests/test_projects_api.py` — в `test_asset_in_use_cannot_be_deleted` проверку деталей заменить:

```python
    assert err["details"]["projects"] == [
        {"id": p["id"], "name": "Мой", "owner_email": me["email"], "owner_name": me["name"]}
    ]
```

и добавить в конец файла:

```python
def test_a_record_in_a_colleagues_project_cannot_be_deleted(client, login_as, settings):
    """Защита смотрит проекты всех авторов, а отказ называет проект и его автора."""
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    p = client.post("/api/v1/projects", json={"name": "Коллеги", "doc": doc()}).json()
    login_as()
    r = client.delete(f"/api/v1/assets/{VIDEO}")
    assert r.status_code == 409
    assert r.json()["error"]["details"]["projects"] == [
        {"id": p["id"], "name": "Коллеги", "owner_email": "other@ya.ru", "owner_name": "Other"}
    ]
```

`tests/test_project_store.py` — в `test_every_project_holds_its_assets_until_it_is_deleted` вызовы `projects_using_asset(conn, USER, "ast_000000000001")` заменить на `projects_using_asset(conn, "ast_000000000001")` и в конец теста добавить:

```python
    c = create_project(conn, settings, OTHER, name="Чужой", raw_doc=doc("ast_000000000002"))
    using = projects_using_asset(conn, "ast_000000000002")
    assert {x["id"] for x in using} == {b["id"], c["id"]}
    assert {x["owner_email"] for x in using} == {f"{USER}@ya.ru", f"{OTHER}@ya.ru"}
```

`tests/test_transcript_api.py` — `test_foreign_asset_is_404_everywhere` заменить:

```python
def test_a_colleague_works_with_the_transcript(client, login_as, settings):
    """Записи общие — и их расшифровка тоже; файл и строка остаются за автором записи."""
    login_as()
    ready_asset(client, settings)
    put(client)
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(f"/api/v1/assets/{ASSET}/transcript").status_code == 200
    assert put(client).status_code == 200
    assert transcript_rows(settings) == 1
    assert client.delete(f"/api/v1/assets/{ASSET}/transcript").status_code == 204
    assert transcript_rows(settings) == 0
```

`tests/test_admin_api.py` — `test_admin_deletes_a_foreign_project_and_asset` заменить:

```python
def test_a_colleague_deletes_a_project_and_its_freed_record(login_as, settings):
    admin = login_as("admin@ya.ru")
    for email in ("user@ya.ru", "colleague@ya.ru"):
        admin.post("/api/v1/admin/whitelist", json={"email": email})
    user = login_as("user@ya.ru", "Пользователь")
    me = user.get("/api/v1/me").json()
    asset = _seed_asset(settings, me["id"])
    project = user.post("/api/v1/projects", json={"name": "Планёрка"}).json()

    colleague = login_as("colleague@ya.ru", "Коллега")
    assert colleague.delete(f"/api/v1/projects/{project['id']}").status_code == 204
    assert colleague.delete(f"/api/v1/assets/{asset}").status_code == 204
    assert colleague.get("/api/v1/projects").json()["projects"] == []
    assert colleague.get("/api/v1/assets").json()["assets"] == []
    assert not (settings.data_dir / me["id"] / "assets" / asset).exists()
```

- [ ] **Step 2:** `uv run python -m pytest tests/test_assets_api.py tests/test_projects_api.py tests/test_project_store.py tests/test_transcript_api.py tests/test_admin_api.py -q` → новые падают.

- [ ] **Step 3: `views.py`.** В `AssetView` после `last_access_at: str`:

```python
    # Автор: записи видит вся команда. Имена полей — как у заданий в /jobs и у карточек проектов.
    owner_email: str
    owner_name: str
```

В `asset_view` после `last_access_at=row["last_access_at"],` — `owner_email=row["owner_email"], owner_name=row["owner_name"],`. В конец файла:

```python
# Строка записи вместе с автором: всё, что отдаёт карточку записи, выбирает через это.
ASSET_SELECT = (
    "SELECT a.*, u.email AS owner_email, u.name AS owner_name "
    "FROM assets AS a JOIN users AS u ON u.id = a.user_id"
)


def list_asset_views(conn: sqlite3.Connection, *, owner: str | None = None) -> list[AssetView]:
    """Записи команды, свежие загрузки сверху; с owner — только его.

    Отметки расшифровки — одним запросом на весь список, а не по запросу на карточку."""
    where = " WHERE a.user_id = ?" if owner else ""
    rows = conn.execute(
        f"{ASSET_SELECT}{where} ORDER BY a.created_at DESC, a.id", (owner,) if owner else ()
    ).fetchall()
    transcribed = {r["asset_id"] for r in conn.execute("SELECT asset_id FROM transcripts")}
    return [asset_view(r, has_transcript=r["id"] in transcribed) for r in rows]
```

- [ ] **Step 4: `assets/routes.py`.** Импорты: `from server.app.assets.views import ASSET_SELECT, AssetView, asset_view, list_asset_views` и `from server.app.audit import audit`. Функцию `transcribed_assets` удалить. `_owned` заменить:

```python
def _record(conn: sqlite3.Connection, asset_id: str) -> sqlite3.Row:
    """Запись с автором. Записи общие: карточку, расшифровку и удаление получает любой вошедший.

    Автор берётся из самой строки (`row["user_id"]`), а не из вызывающего: файлы лежат в его
    каталоге, и всё, что дальше трогает диск или считает занятое место, обязано считать его.
    """
    row = conn.execute(f"{ASSET_SELECT} WHERE a.id = ?", (asset_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Ассет не найден")
    return row
```

Вызовы: `_owned(conn, user, row["id"])` → `_record(conn, row["id"])`, `_owned(conn, user, asset_id)` → `_record(conn, asset_id)` (`sed -i 's/_owned(conn, user, /_record(conn, /g'`). `list_` заменить:

```python
@router.get("", response_model=AssetList)
def list_(
    mine: bool = False,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AssetList:
    """Все записи команды, свежие сверху; ?mine=1 — только свои (конвертер, скрипты агента)."""
    return AssetList(assets=list_asset_views(conn, owner=user.id if mine else None))
```

`delete` заменить:

```python
@router.delete("/{asset_id}", status_code=204)
def delete(
    asset_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    """Сначала запись, потом файлы: упавший процесс не оставит запись без файлов, папку подберёт janitor.
    Удалить может любой вошедший; запись, которая стоит в чьём-либо проекте, не удаляется."""
    asset = _record(conn, asset_id)
    owner = asset["user_id"]
    with transaction(conn):
        # Проверка занятости внутри транзакции: BEGIN IMMEDIATE сериализует нас с сохранением проекта,
        # иначе между проверкой и удалением кто-то успел бы сослаться на этот ассет.
        used_by = projects_using_asset(conn, asset_id)
        if used_by:
            raise ApiError(409, "asset_in_use", "Файл стоит в проекте", {"projects": used_by})
        cur = conn.execute("DELETE FROM assets WHERE id = ? AND user_id = ?", (asset_id, owner))
        if cur.rowcount == 0:
            raise ApiError(404, "not_found", "Ассет не найден")
        cancel_jobs_for_target(conn, asset_id)
    shutil.rmtree(asset_dir(request.app.state.settings, owner, asset_id), ignore_errors=True)
    audit(conn, user, "удалил запись", f"{asset_id} «{asset['original_name']}»", owner)
    return Response(status_code=204)
```

- [ ] **Step 5: `store.projects_using_asset`** заменить:

```python
def projects_using_asset(conn: sqlite3.Connection, asset_id: str) -> list[dict]:
    """Проекты любого автора, где стоит запись. Документов мало, ищем перебором.

    Автор каждого проекта — в ответе: отказ «запись стоит в проекте» говорит, к кому идти."""
    rows = conn.execute(
        "SELECT p.id, p.name, p.doc, u.email AS owner_email, u.name AS owner_name "
        "FROM projects AS p JOIN users AS u ON u.id = p.user_id ORDER BY p.updated_at DESC, p.id"
    )
    return [
        {"id": r["id"], "name": r["name"], "owner_email": r["owner_email"], "owner_name": r["owner_name"]}
        for r in rows
        if asset_id in assets_of(json.loads(r["doc"]))
    ]
```

- [ ] **Step 6: `projects/routes.py`.** Импорт `from server.app.assets.views import AssetView, list_asset_views`. `project_assets` заменить:

```python
@router.get("/{project_id}/assets", response_model=AssetList)
def project_assets(
    project_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AssetList:
    """Записи, из которых можно собрать проект, — все записи команды: документ может ссылаться на
    запись любого автора. Тот же список, что GET /api/v1/assets; ручка осталась ради редактора."""
    _project(conn, project_id)
    return AssetList(assets=list_asset_views(conn))
```

- [ ] **Step 7:** тесты шага 2 → PASS; `uv run python -m pytest -q` → всё зелёное.

---

### Task 5: Ролики и задания — общие

**Files:**
- Modify: `server/app/jobs.py` (`list_jobs_for_user` → `list_jobs`, `_JOB_SELECT`, `_job_item`)
- Modify: `server/app/renders/routes.py` (докстринг, импорты, `JobListItem`, `_owned_job` → `_job`, `get_`, `delete`, `list_jobs`, `job`, `cancel`)
- Modify: `server/app/projects/store.py` (`get_render` удалить, докстринги `render_owner`, `get_render_any`)
- Test: `tests/test_renders_api.py`, `tests/test_jobs_api.py`, `tests/test_jobs.py`

- [ ] **Step 1: Падающие тесты.** `tests/test_renders_api.py` — `test_foreign_things_are_404` заменить:

```python
def test_a_colleague_works_with_renders_and_jobs(client, login_as, settings):
    """Ролики и задания общие: коллега смотрит, собирает, отменяет и удаляет."""
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    render_id = seed_render(settings, project["id"], me["id"])
    job_id = client.post(f"/api/v1/projects/{project['id']}/render", json={}).json()["job_id"]
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(f"/api/v1/renders/{render_id}").status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/v1/projects/{project['id']}/renders").status_code == 200
    assert client.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 204
    again = client.post(f"/api/v1/projects/{project['id']}/render", json={})
    assert again.status_code == 202, again.text
    # Сборка числится за автором проекта: ролик ляжет в его каталог.
    jobs = {j["id"]: j for j in client.get("/api/v1/jobs").json()["jobs"]}
    assert jobs[again.json()["job_id"]]["owner_email"] == me["email"]
    assert client.delete(f"/api/v1/renders/{render_id}").status_code == 204
```

`tests/test_jobs_api.py` — `test_list_hides_other_users_jobs_from_a_plain_user` заменить (тело до `conn.close()` прежнее):

```python
def test_a_plain_user_sees_the_whole_team_and_mine_filters(client, login_as, settings):
    login_as()
    assert client.post("/api/v1/admin/whitelist", json={"email": "u@ya.ru"}).status_code == 201
    login_as("u@ya.ru", "U")
    me = client.get("/api/v1/me").json()
    assert me["role"] == "user"
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES ('usr_stranger01', 'x@y.z', 'X', ?)",
        (now_iso(),),
    )
    stranger = enqueue_job(conn, user_id="usr_stranger01", type_="analyze", target_id="ast_nope")
    mine = enqueue_job(conn, user_id=me["id"], type_="proxy", target_id="ast_mine")
    conn.commit()
    conn.close()
    rows = {j["id"]: j for j in client.get("/api/v1/jobs").json()["jobs"]}
    assert mine in rows and stranger in rows
    assert rows[stranger]["owner_email"] == "x@y.z" and rows[stranger]["owner_name"] == "X"
    assert rows[mine]["owner_email"] == "u@ya.ru" and rows[mine]["owner_name"] == "U"
    assert [j["id"] for j in client.get("/api/v1/jobs?mine=1").json()["jobs"]] == [mine]
```

В `test_admin_sees_the_whole_team_and_who_owns_what` докстринг заменить на `"""Очередь общая: каждая строка называет автора, иначе не понять, чья это «Сборка»."""`.

`tests/test_jobs.py` — в импорте `list_jobs_for_user,` → `list_jobs,`; в `test_list_includes_open_jobs_and_recent_finished_only` оба вызова `list_jobs_for_user(conn, uid, now=now)` → `list_jobs(conn, now=now, owner=uid)`; в `test_list_for_everyone_names_owners` докстринг → `"""Очередь общая: задания всей команды, у каждого — автор."""`, `list_jobs_for_user(conn, uid, now=now, everyone=True)` → `list_jobs(conn, now=now)`, `list_jobs_for_user(conn, uid, now=now)` → `list_jobs(conn, now=now, owner=uid)`. В конец файла:

```python
def test_live_jobs_are_never_pushed_out_by_the_limit(conn):
    """Чужая пачка загрузок не вытесняет из списка идущую сборку: предел — только законченным."""
    now = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
    uid = "usr_000000000001"
    live = enqueue_job(conn, user_id=uid, type_="render", target_id="prj_x")
    for n in range(LIST_LIMIT + 5):
        done = enqueue_job(conn, user_id=uid, type_="proxy", target_id=f"ast_{n}")
        _stamp(conn, done, status="done", finished_at=iso(now - timedelta(seconds=1)), progress=1)
    ids = [r["id"] for r in list_jobs(conn, now=now)]
    assert live in ids
    assert len(ids) == LIST_LIMIT + 1
```

- [ ] **Step 2:** `uv run python -m pytest tests/test_renders_api.py tests/test_jobs_api.py tests/test_jobs.py -q` → падают (импорт `list_jobs`, 404 у коллеги, чужие задания не видны).

- [ ] **Step 3: `jobs.py`** — функцию `list_jobs_for_user` целиком заменить:

```python
_JOB_SELECT = """
    SELECT jobs.id, jobs.type, jobs.status, jobs.progress, jobs.error, jobs.created_at,
           jobs.finished_at, jobs.params, jobs.target_id,
           assets.original_name AS asset_name, projects.name AS project_name,
           users.email AS owner_email, users.name AS owner_name
    FROM jobs
    LEFT JOIN users ON users.id = jobs.user_id
    LEFT JOIN assets
      ON assets.id = jobs.target_id AND jobs.type IN ('analyze', 'proxy', 'transcribe', 'convert')
    LEFT JOIN projects
      ON projects.id = jobs.target_id AND jobs.type = 'render'
"""


def list_jobs(conn: sqlite3.Connection, *, now: datetime, owner: str | None = None) -> list[dict]:
    """Задания команды: идущие и только что законченные; с owner — только этого человека.

    Очередь общая, как проекты и записи: сборку в чужом проекте мог поставить любой, и её ход видят
    все. Каждая строка называет автора (owner_email, owner_name): в общем списке иначе не понять,
    чья это «Сборка «Ролик»».

    Идущие отдаются все, предел LIST_LIMIT — только законченным. Иначе пачка чужих загрузок
    (анализ и прокси на каждый файл) вытеснила бы из списка идущую сборку, и панель «Рендер»,
    не найдя её, дала бы поставить вторую.
    """
    cutoff = iso(now - timedelta(seconds=RECENT_SEC))
    mine = " AND jobs.user_id = ?" if owner else ""
    extra: tuple = (owner,) if owner else ()
    live = conn.execute(f"{_JOB_SELECT} WHERE jobs.status IN ('queued', 'running'){mine}", extra).fetchall()
    finished = conn.execute(
        f"{_JOB_SELECT} WHERE jobs.status IN ('done', 'canceled', 'failed') "
        f"AND jobs.finished_at IS NOT NULL AND jobs.finished_at >= ?{mine} "
        "ORDER BY jobs.created_at DESC LIMIT ?",
        (cutoff, *extra, LIST_LIMIT),
    ).fetchall()
    rows = sorted([*live, *finished], key=lambda row: row["created_at"], reverse=True)
    return [_job_item(row) for row in rows]


def _job_item(row: sqlite3.Row) -> dict:
    params = json.loads(row["params"] or "{}")
    quality = params.get("quality") if row["type"] == "render" else None
    return {
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "progress": row["progress"],
        "error": row["error"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "label": job_label(row["type"], row["asset_name"], row["project_name"]),
        "cancelable": job_cancelable(row["type"], row["status"]),
        "quality": quality if quality in RENDER_QUALITIES else None,
        "target_id": row["target_id"],
        "owner_email": row["owner_email"] or "",
        "owner_name": row["owner_name"] or "",
    }
```

- [ ] **Step 4: `renders/routes.py`.** Докстринг модуля:

```python
"""Готовые ролики и ход заданий: /api/v1/renders, /api/v1/jobs.

Всё общее: ролики и задания видит, отменяет и удаляет любой вошедший — как проекты и записи.
Несуществующий идентификатор даёт 404.
"""
```

Импорты: `from server.app.audit import audit`, `from server.app.jobs import list_jobs as list_team_jobs`, `from server.app.projects.store import delete_render, get_render_any, render_owner`. Комментарий у `owner_email` в `JobListItem` → `# Чьё задание: у сборки — автор проекта, у анализа, прокси и расшифровки — автор записи.` `_owned_job` заменить:

```python
def _job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Задание не найдено")
    return row
```

`get_`: тело — `render = get_render_any(conn, render_id)`, дальше как было (без комментария про админа). `delete` заменить:

```python
@router.delete("/renders/{render_id}", status_code=204)
def delete(
    render_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    owner = render_owner(conn, render_id)
    if owner is None or not delete_render(conn, owner, render_id):
        raise ApiError(404, "not_found", "Ролик не найден")
    audit(conn, user, "удалил ролик", render_id, owner)
    return Response(status_code=204)
```

`list_jobs` (маршрут) заменить:

```python
@router.get("/jobs", response_model=JobList)
def list_jobs(
    mine: bool = False,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> JobList:
    """Задания всей команды; ?mine=1 — только свои (конвертер, скрипты агента)."""
    rows = list_team_jobs(conn, now=utcnow(), owner=user.id if mine else None)
    return JobList(jobs=[JobListItem(**row) for row in rows])
```

В `job`: `row = _job(conn, job_id)`. `cancel` — `row = _job(conn, job_id)`, отказ для анализа прежний, дальше:

```python
    cur = conn.execute(
        "UPDATE jobs SET status = 'canceled', finished_at = ? "
        "WHERE id = ? AND status IN ('queued', 'running')",
        (now_iso(), job_id),
    )
    if cur.rowcount:
        audit(conn, user, "отменил задание", f"{job_id} ({row['type']})", row["user_id"])
    return Response(status_code=204)
```

- [ ] **Step 5: `store.py`** — функцию `get_render` удалить; докстринги: `render_owner` — `"""Автор готового ролика — он же автор проекта: файл лежит в его каталоге."""`, `get_render_any` — `"""Ролик по id: ролики общие, доступ проверять нечего."""`.

- [ ] **Step 6:** тесты шага 2 → PASS; `uv run python -m pytest -q` → всё зелёное.

---

### Task 6: Конвертер остаётся личным

**Files:**
- Modify: `server/app/conversions/routes.py:56-60` (`_owned_asset`)
- Test: `tests/test_conversions_api.py`

- [ ] **Step 1: Падающий тест** — `test_foreign_conversion_is_404` заменить:

```python
def test_a_colleagues_conversions_stay_personal(client, login_as, settings):
    """Конвертер личный: чужие конверсии не видны, чужую запись не сконвертировать (403).
    Задание конвертации при этом видно всем — очередь общая."""
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    cid = seed_conversion(settings, me["id"], "cnv_000000000001")
    job_id = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "mp3"}).json()["job_id"]
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(f"/api/v1/conversions/{cid}").status_code == 404
    assert client.delete(f"/api/v1/conversions/{cid}").status_code == 404
    assert client.get(f"/api/v1/assets/{ASSET}/conversions").status_code == 403
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "mp3"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_yours"
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 200
```

- [ ] **Step 2:** `uv run python -m pytest tests/test_conversions_api.py -q` → падает (404 вместо 403).

- [ ] **Step 3: Реализация** — `_owned_asset` заменить:

```python
def _owned_asset(conn: sqlite3.Connection, user: CurrentUser, asset_id: str) -> sqlite3.Row:
    """Своя запись. Конвертер — личный инструмент: записи общие, а конвертировать и смотреть
    историю можно только своего. Скрыть чужую запись за 404 уже нельзя — её карточка открывается
    всем, — поэтому честный отказ 403."""
    row = get_asset(conn, user.id, asset_id)
    if row is not None:
        return row
    if conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
        raise ApiError(404, "not_found", "Ассет не найден")
    raise ApiError(403, "not_yours", "Конвертировать можно только свою запись")
```

- [ ] **Step 4:** `uv run python -m pytest tests/test_conversions_api.py -q` → PASS.

---

### Task 7: Файлы — записи и ролики всем, превью не продлевают

**Files:**
- Modify: `server/app/files.py` (`PREVIEW_FILES`, `authorize_file`, `serve_file`, `authz`)
- Test: `tests/test_files.py`

- [ ] **Step 1: Падающие тесты.** `test_serves_public_file_and_touches_last_access` переименовать в `test_serves_a_public_file_without_touching_the_record`, последнюю строку заменить на `assert client.get(f"/api/v1/assets/{asset_id}").json()["last_access_at"] == OLD`. В `test_authz_for_caddy` последнюю строку — так же `== OLD`. `test_missing_foreign_and_unknown_are_404` заменить:

```python
def test_missing_and_unknown_are_404_and_a_colleague_gets_the_file(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    asset_id = _ready_video_asset(client, settings, me["id"])
    assert client.get(_url(me["id"], asset_id, "thumbs.jpg")).status_code == 404
    assert client.get(_url(me["id"], "ast_000000000000", "peaks.json")).status_code == 404
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(_url(me["id"], asset_id, "peaks.json")).status_code == 200
```

`test_foreign_render_is_404` заменить:

```python
def test_a_colleague_downloads_a_render_but_not_under_a_wrong_prefix(client, login_as, settings):
    login_as()
    owner = client.get("/api/v1/me").json()
    project_id, render_id = _render_on_disk(client, settings, owner["id"])
    good = f"/files/{owner['id']}/projects/{project_id}/renders/{render_id}.mp4"
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    thief = client.get("/api/v1/me").json()
    assert client.get(good).status_code == 200
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": good}).status_code == 204
    wrong = f"/files/{thief['id']}/projects/{project_id}/renders/{render_id}.mp4"
    assert client.get(wrong).status_code == 404
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": wrong}).status_code == 404
```

Добавить:

```python
def test_previews_do_not_touch_the_record_but_use_does(client, login_as, settings):
    """Кадры и волну рисуют общие списки: продлевай они запись, открытый кем-то экран «Записи»
    продлевал бы все видео команды, и срок хранения не наступил бы никогда."""
    login_as()
    uid = client.get("/api/v1/me").json()["id"]
    asset_id = _ready_video_asset(client, settings, uid)

    def last_access() -> str:
        return client.get(f"/api/v1/assets/{asset_id}").json()["last_access_at"]

    assert client.get(_url(uid, asset_id, "peaks.json")).status_code == 200
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": _url(uid, asset_id, "thumbs.jpg")}).status_code == 204
    assert last_access() == OLD
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": _url(uid, asset_id, "proxy.mp4")}).status_code == 204
    assert last_access() > OLD
```

(две длинные строки при реализации разбить на `url = …` и `assert …`, чтобы уложиться в 110 знаков.)

- [ ] **Step 2:** `uv run python -m pytest tests/test_files.py -q` → падают (коллеге 404, превью продлевают).

- [ ] **Step 3: Реализация.** После `FILE_CACHE`:

```python
# Превью — кадры и волна — срок жизни записи не продлевают: их рисуют общие списки, и открытый
# кем-то экран «Записи» продлевал бы все видео команды. Продлевает использование — прокси,
# анализ, расшифровка, конверсии, сохранение проекта.
PREVIEW_FILES = frozenset({"thumbs.jpg", "thumbs.json", "peaks.json"})
```

В `authorize_file` докстринг:

```python
    """Путь к файлу или ApiError: 403 для непубличных имён (source.*), 404 для несуществующего.

    Файлы записей и ролики общие: их получает любой вошедший — записи и проекты видит вся команда.
    Конверсии личные, как и сам конвертер: их видят только автор и админ.
    owner_id — это ассет для kind="asset"/"conversion" и проект для kind="render".
    """
```

Общую проверку `if user_id != user.id and user.role != "admin": raise ApiError(404, …)` (вместе с комментарием над ней) перенести первой строкой внутрь ветки `if kind == "conversion":`. В `serve_file` строку `touch_last_access(conn, asset_id)` заменить:

```python
    if name not in PREVIEW_FILES:
        touch_last_access(conn, asset_id)
```

В `authz` условие продления заменить:

```python
    if kind == "conversion" or (kind == "asset" and name not in PREVIEW_FILES):
        touch_last_access(conn, owner_id)
```

- [ ] **Step 4:** `uv run python -m pytest tests/test_files.py -q` → PASS; `uv run python -m pytest -q` → всё зелёное.

---

### Task 8: Админские списки команды уходят

**Files:**
- Modify: `server/app/admin/routes.py` (докстринг, импорты `json` и `clips_duration`, классы `OwnedProject*`, `OwnedAsset*`, маршруты `all_projects`, `all_assets`)
- Test: `tests/test_admin_api.py`

- [ ] **Step 1: Тесты.** `test_admin_sees_everyones_projects_and_assets` заменить:

```python
def test_a_plain_user_sees_the_team_in_the_shared_lists(login_as, settings):
    admin = login_as("admin@ya.ru")
    for email in ("user@ya.ru", "colleague@ya.ru"):
        admin.post("/api/v1/admin/whitelist", json={"email": email})
    user = login_as("user@ya.ru", "Пользователь")
    me = user.get("/api/v1/me").json()
    asset = _seed_asset(settings, me["id"])
    project = user.post(
        "/api/v1/projects",
        json={"name": "Планёрка", "doc": {"clips": [{"asset_id": asset, "in": 0, "out": 5}]}},
    ).json()

    colleague = login_as("colleague@ya.ru", "Коллега")
    projects = colleague.get("/api/v1/projects").json()["projects"]
    assert [p["id"] for p in projects] == [project["id"]]
    assert projects[0]["owner_email"] == "user@ya.ru" and projects[0]["owner_name"] == "Пользователь"
    assert projects[0]["clips_count"] == 1 and projects[0]["duration"] == 5.0
    assets = colleague.get("/api/v1/assets").json()["assets"]
    assert [a["id"] for a in assets] == [asset]
    assert assets[0]["owner_email"] == "user@ya.ru" and assets[0]["size"] == 1234
```

`test_these_lists_are_for_the_admin_only` заменить:

```python
def test_admin_team_lists_are_gone(login_as):
    """Их заменили общие списки: /api/v1/projects и /api/v1/assets."""
    admin = login_as("admin@ya.ru")
    assert admin.get("/api/v1/admin/projects").status_code == 404
    assert admin.get("/api/v1/admin/assets").status_code == 404
```

- [ ] **Step 2:** `uv run python -m pytest tests/test_admin_api.py -q` → `test_admin_team_lists_are_gone` падает (200).

- [ ] **Step 3: Реализация.** Докстринг модуля: `"""Администратор: белый список почт, кабинет доступа, место по людям и общая статистика.` (вторая часть докстринга прежняя). Удалить `import json`, `from server.media.timeline import clips_duration`, классы `OwnedProject`, `OwnedProjectList`, `OwnedAsset`, `OwnedAssetList` и маршруты `all_projects`, `all_assets`.

- [ ] **Step 4:** `uv run python -m pytest tests/test_admin_api.py -q` и `uv run ruff check server/app/admin/routes.py` → чисто.

---

### Task 9: Срок хранения — 7 дней

**Files:**
- Modify: `server/app/config.py:39`, `.env.example:38`, `server/janitor/rules.py` (докстринг `free_stuck_assets`)
- Test: `tests/test_config.py`, `tests/test_janitor.py`

- [ ] **Step 1: Тесты.** `tests/test_config.py`: `assert s.upload_ttl_hours == 24 and s.asset_ttl_hours == 168`. `tests/test_janitor.py`:
  - `test_expired_assets_are_deleted_and_jobs_canceled`: `hours=25` → `hours=settings.asset_ttl_hours + 1`, `hours=23` → `hours=settings.asset_ttl_hours - 1`;
  - `test_asset_touched_during_pass_survives`: `hours=25` → `hours=settings.asset_ttl_hours + 1`;
  - `test_asset_used_by_a_project_survives_its_ttl`: оба `hours=30` → `hours=settings.asset_ttl_hours + 6`.

- [ ] **Step 2:** `uv run python -m pytest tests/test_config.py tests/test_janitor.py -q` → `test_config` падает (24).

- [ ] **Step 3: Реализация.** `config.py`:

```python
    # Запись вне проектов живёт неделю с последнего обращения: это общая библиотека команды,
    # и коллега должен успеть взять вчерашнюю загрузку.
    asset_ttl_hours: int = Field(default=168, ge=1)
```

`.env.example`: `VIDEO_ASSET_TTL_HOURS=168`. В докстринге `free_stuck_assets` «до суточного срока жизни» → «до конца срока хранения».

- [ ] **Step 4:** `uv run python -m pytest -q` → всё зелёное; `uv run ruff check server tests tools` → чисто.

---

### Task 10: Клиент — автор в типах, подпись автора, «есть несохранённое»

**Files:**
- Modify: `web/src/assets.ts` (`Asset`, `listAssets`, комментарий `listProjectAssets`)
- Modify: `web/src/project.ts` (`ProjectCard`, комментарий `JobListItem`, `createSaver` → `unsaved()`)
- Modify: `web/src/overview.ts` (`authorLabel`)
- Test: `web/src/overview.test.ts`, `web/src/project.test.ts`

- [ ] **Step 1: Падающие тесты.** `overview.test.ts`: в импорт добавить `authorLabel`, в конец:

```ts
describe('подпись автора', () => {
  it('у своего — «вы», у чужого — имя или почта', () => {
    expect(authorLabel({ owner_email: 'Me@Ya.ru', owner_name: 'Я' }, ' me@ya.ru ')).toBe('вы')
    expect(authorLabel({ owner_email: 'two@ya.ru', owner_name: 'Второй' }, 'me@ya.ru')).toBe('Второй')
    expect(authorLabel({ owner_email: 'two@ya.ru', owner_name: '' }, 'me@ya.ru')).toBe('two@ya.ru')
  })
})
```

`project.test.ts` — в `describe('автосохранение', …)` после «знает, есть ли несохранённые правки»:

```ts
  it('помнит несохранённую правку, пока она ждёт повтора после сбоя', async () => {
    vi.useFakeTimers()
    const request = async () => {
      throw new ApiError(500, 'internal_error', 'ой')
    }
    const saver = createSaver({ request, delay: 0 })
    await flushFailing(saver, project(1))
    // pending() уже false — запрос не летит и очереди нет, — но на сервере правки нет.
    expect(saver.pending()).toBe(false)
    expect(saver.unsaved()).toBe(true)
    saver.cancel()
    vi.useRealTimers()
  })

  it('после записи несохранённого нет', async () => {
    const saver = createSaver({ request: async () => project(2), delay: 0 })
    await saver.flush(project(1))
    expect(saver.unsaved()).toBe(false)
  })
```

- [ ] **Step 2:** `cd web && npx vitest run src/overview.test.ts src/project.test.ts` → падают (`authorLabel`, `unsaved` не функции).

- [ ] **Step 3: Реализация.** `assets.ts` — в `Asset` после `error: string | null`:

```ts
  /** Автор: записи видит вся команда. От него зависят папка на диске и лимит. */
  owner_email: string
  owner_name: string
```

`listAssets`:

```ts
/** Все записи команды, свежие сверху; mine — только свои (конвертер). */
export function listAssets(mine = false): Promise<{ assets: Asset[] }> {
  return api<{ assets: Asset[] }>(mine ? '/api/v1/assets?mine=1' : '/api/v1/assets')
}
```

Комментарий над `listProjectAssets`:

```ts
/**
 * Записи, из которых можно собрать проект, — все записи команды: документ может ссылаться на
 * запись любого автора. Сервер отдаёт тот же общий список, что и /assets.
 */
```

`project.ts` — `ProjectCard`:

```ts
export type ProjectCard = Omit<Project, 'doc'> & {
  clips_count: number
  duration: number
  /** Автор проекта: проекты видит вся команда. */
  owner_email: string
  owner_name: string
}
```

Комментарий в `JobListItem` → `/** Чьё задание: у сборки — автор проекта, у записи — её автор. Видят все. */`. В объект, который возвращает `createSaver`, после `pending()`:

```ts
    /**
     * Есть ли правка, которой нет на сервере: в очереди, в полёте, ждёт повтора после сбоя или
     * отвергнута. По ней опрос решает, можно ли подставить свежий документ коллеги, не стерев
     * правку человека: ждущую повтора правку pending() уже не видит.
     */
    unsaved(): boolean {
      return queued !== null || saving || retryProject !== null || failed
    },
```

`overview.ts` — после `ownedBy`:

```ts
/** Подпись автора на карточке: у своего «вы», у чужого — имя, а без имени почта. */
export function authorLabel(item: { owner_email: string; owner_name: string }, myEmail: string): string {
  return ownedBy(item, myEmail) ? 'вы' : ownerLabel(item)
}
```

- [ ] **Step 4:** `npx vitest run` и `npx tsc --noEmit` → зелёное.

---

### Task 11: «Проекты» — один общий список

**Files:**
- Modify: `web/src/projects.ts` (целиком), `web/src/overview.ts` (удалить `TeamProject`, `loadTeamProjects`)
- Create: `web/src/projects.test.ts`

- [ ] **Step 1: Падающий тест** — `web/src/projects.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import type { ProjectCard } from './project'
import { dropProjectQuestion, projectCardHtml } from './projects'

const card = (over: Partial<ProjectCard> = {}): ProjectCard => ({
  id: 'prj_1',
  name: 'Планёрка',
  version: 3,
  created_at: '2026-09-01T10:00:00.000Z',
  updated_at: '2026-09-02T10:00:00.000Z',
  clips_count: 4,
  duration: 95,
  owner_email: 'liza@ya.ru',
  owner_name: 'Лиза',
  ...over,
})

describe('карточка проекта', () => {
  it('называет автора, а у своего пишет «вы»', () => {
    expect(projectCardHtml(card(), 0, 'gleb@ya.ru')).toContain('Лиза · 4 кл.')
    expect(projectCardHtml(card({ owner_email: 'gleb@ya.ru' }), 0, 'gleb@ya.ru')).toContain('вы · 4 кл.')
  })

  it('удалить может любой: кнопка есть и у чужого', () => {
    expect(projectCardHtml(card(), 0, 'gleb@ya.ru')).toContain('data-drop="prj_1"')
  })
})

describe('вопрос перед удалением', () => {
  it('у чужого проекта называет автора', () => {
    expect(dropProjectQuestion(card(), 'gleb@ya.ru')).toContain('«Планёрка» (автор — Лиза)')
  })

  it('у своего — без автора', () => {
    expect(dropProjectQuestion(card({ owner_email: 'gleb@ya.ru' }), 'gleb@ya.ru')).not.toContain('автор')
  })
})
```

- [ ] **Step 2:** `npx vitest run src/projects.test.ts` → падает (нет экспорта).

- [ ] **Step 3: Реализация** — `web/src/projects.ts` целиком:

```ts
/**
 * Экран проектов: один общий список проектов команды, свежие правки сверху, на карточке автор.
 *
 * Создание живёт на экране нового проекта. Проект открывает, правит и удаляет любой: работа
 * общая. Удаление необратимо, поэтому подтверждение у чужого проекта называет автора.
 */
import { api, ApiError } from './api'
import { fmtDuration, fmtWhen } from './assets'
import { escapeHtml } from './html'
import { authorLabel, ownedBy, ownerLabel } from './overview'
import { listProjects, type ProjectCard } from './project'
import type { Me } from './shell'

/** Карточка проекта: имя, автор («вы» у своего), клипы, длительность, последняя правка. */
export function projectCardHtml(p: ProjectCard, index: number, myEmail: string): string {
  return `<a class="card project-card appear" style="--delay:${index * 40}ms"
      href="#/p/${encodeURIComponent(p.id)}">
      <span class="display-m project-title">${escapeHtml(p.name)}</span>
      <span class="meta">${escapeHtml(authorLabel(p, myEmail))} · ${p.clips_count} кл. · ${fmtDuration(p.duration)} · ${fmtWhen(p.updated_at)}</span>
      <span class="row">
        <button class="btn btn-ghost" data-drop="${escapeHtml(p.id)}">Удалить</button>
      </span>
    </a>`
}

/** Вопрос перед удалением. Удаление необратимо: у чужого проекта называем автора. */
export function dropProjectQuestion(p: ProjectCard, myEmail: string): string {
  const whose = ownedBy(p, myEmail) ? '' : ` (автор — ${ownerLabel(p)})`
  return (
    `Удалить проект «${p.name}»${whose}? Удаление необратимо: пропадут его точки сохранения ` +
    'и готовые ролики. Записи освободятся и уйдут по сроку хранения.'
  )
}

export function mountProjects(el: HTMLElement, me: Me) {
  el.innerHTML = `
    <div class="screen stack">
      <div class="row space-between">
        <h1 class="display-l" style="margin:0">Проекты</h1>
        <a class="btn btn-key" href="#/new">Новый</a>
      </div>
      <pre id="prj-error" hidden></pre>
      <div id="prj-list" class="tiles"></div>
    </div>`
  const list = el.querySelector('#prj-list') as HTMLElement
  // Ошибка — над списком: под длинным общим списком её не видно.
  const errorBox = el.querySelector('#prj-error') as HTMLPreElement
  let stopped = false
  let shown: ProjectCard[] = []

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  async function refresh(): Promise<void> {
    if (stopped) return
    const { projects } = await listProjects()
    if (stopped) return
    shown = projects
    // Порядок задаёт сервер: свежие правки выше.
    list.innerHTML = projects.length
      ? projects.map((p, i) => projectCardHtml(p, i, me.email)).join('')
      : '<p class="lead" style="margin:0">Проектов пока нет. Начните с записи</p>'
    list.querySelectorAll<HTMLButtonElement>('button[data-drop]').forEach(wireDrop)
  }

  function wireDrop(button: HTMLButtonElement): void {
    button.addEventListener('click', async event => {
      // Карточка целиком — ссылка в редактор: без этого кнопка внутри неё уводила бы со страницы.
      event.preventDefault()
      event.stopPropagation()
      const p = shown.find(x => x.id === button.dataset.drop)
      if (!p || !window.confirm(dropProjectQuestion(p, me.email))) return
      button.disabled = true
      try {
        await api(`/api/v1/projects/${encodeURIComponent(p.id)}`, { method: 'DELETE' })
        errorBox.hidden = true
        await refresh()
      } catch (e) {
        button.disabled = false
        showError(e)
      }
    })
  }

  void refresh().catch(showError)

  return {
    stop(): void {
      stopped = true
    },
  }
}
```

`overview.ts` — удалить тип `TeamProject` и функцию `loadTeamProjects`.

- [ ] **Step 4:** `npx vitest run` и `npx tsc --noEmit` → зелёное.

---

### Task 12: «Записи» — один общий список

**Files:**
- Modify: `web/src/files.ts` (импорты, новые `assetMetaText`, `dropAssetQuestion`, `inUseText`, разметка, `showError`, `card`, `refresh`, `wire`; удалить `teamCard`, `refreshTeam`)
- Modify: `web/src/overview.ts` (шапка модуля; удалить `TeamAsset`, `othersOnly`, `loadTeamAssets`; текст в `mountOverview`)
- Test: `web/src/files.test.ts`, `web/src/overview.test.ts`

- [ ] **Step 1: Падающие тесты.** `files.test.ts`: импорт `import { acceptAttr, assetMetaText, dropAssetQuestion, formatRows, inUseText } from './files'` и `import type { Asset } from './assets'`; в конец:

```ts
const rec = (over: Partial<Asset> = {}): Asset =>
  ({
    id: 'ast_1',
    kind: 'video',
    original_name: 'встреча.mp4',
    size: 1_048_576,
    status: 'proxy_ready',
    duration: 65,
    error: null,
    owner_email: 'liza@ya.ru',
    owner_name: 'Лиза',
    files: { proxy: null, thumbs: null, thumbs_meta: null, peaks: null, analysis: null, vtt: null, transcript: null },
    ...over,
  }) as Asset

describe('запись в общем списке', () => {
  it('называет автора, а у своей пишет «вы»', () => {
    expect(assetMetaText(rec(), 'gleb@ya.ru')).toMatch(/^Лиза · /)
    expect(assetMetaText(rec({ owner_email: 'gleb@ya.ru' }), 'gleb@ya.ru')).toMatch(/^вы · /)
    expect(assetMetaText(rec({ kind: 'image', duration: null }), 'gleb@ya.ru')).toContain('картинка')
  })

  it('перед удалением чужой называет автора и что уйдёт вместе с ней', () => {
    const ask = dropAssetQuestion(rec(), 'gleb@ya.ru')
    expect(ask).toContain('(автор — Лиза)')
    expect(ask).toContain('расшифровка')
    expect(dropAssetQuestion(rec({ owner_email: 'gleb@ya.ru' }), 'gleb@ya.ru')).not.toContain('автор')
  })

  it('отказ «стоит в проекте» называет проекты и их авторов', () => {
    const projects = [{ id: 'prj_1', name: 'Планёрка', owner_email: 'liza@ya.ru', owner_name: 'Лиза' }]
    expect(inUseText(projects, 'gleb@ya.ru')).toContain('«Планёрка» (Лиза)')
  })
})
```

`overview.test.ts`: удалить помощника `asset`, `type TeamAsset` и `othersOnly` из импорта и весь `describe('только чужое', …)`.

- [ ] **Step 2:** `npx vitest run src/files.test.ts` → падает.

- [ ] **Step 3: Реализация `files.ts`.** Импорт из `./overview` заменить на `import { authorLabel, ownedBy, ownerLabel } from './overview'`. Перед `mountFiles`:

```ts
/** Подпись под именем записи: автор («вы» у своей), длительность или «картинка», вес. */
export function assetMetaText(a: Asset, myEmail: string): string {
  return `${authorLabel(a, myEmail)} · ${a.kind === 'image' ? 'картинка' : fmtDuration(a.duration)} · ${fmtSize(a.size)}`
}

/** Вопрос перед удалением. Удаление необратимо: у чужой записи называем автора и что уйдёт с ней. */
export function dropAssetQuestion(a: Asset, myEmail: string): string {
  if (ownedBy(a, myEmail)) return `Удалить «${a.original_name}» без возможности восстановления?`
  return (
    `Удалить чужую запись «${a.original_name}» (автор — ${ownerLabel(a)}) без возможности ` +
    'восстановления? Вместе с ней пропадут её расшифровка и конвертации.'
  )
}

export type InUse = { id: string; name: string; owner_email: string; owner_name: string }

/** Отказ 409: запись стоит в проектах — называем их и авторов, чтобы было ясно, к кому идти. */
export function inUseText(projects: InUse[], myEmail: string): string {
  const names = projects.map(p => `«${p.name}» (${authorLabel(p, myEmail)})`).join(', ')
  return `Запись стоит в проекте: ${names}. Сначала уберите её оттуда.`
}
```

В `mountFiles`: убрать `const admin = …`, первой строкой `const myEmail = me?.email ?? ''`; в разметке после `</label>` dropzone добавить `<p class="meta" style="margin:0">Записи общие: загруженное видит вся команда</p>`, `<pre id="f-error" hidden></pre>` перенести перед `<div id="f-list" …>`, блок `${admin ? … : ''}` удалить; `teamBox` удалить; добавить `let shown: Asset[] = []`. `showError`:

```ts
  const showError = (e: unknown) => {
    errorBox.hidden = false
    if (e instanceof ApiError && e.code === 'asset_in_use') {
      const projects = (e.details as { projects?: InUse[] } | null)?.projects ?? []
      errorBox.textContent = inUseText(projects, myEmail)
      return
    }
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }
```

В `card(a)` строку с метой заменить на `<span class="meta">${escapeHtml(assetMetaText(a, myEmail))}</span>`, кнопку — `<button class="btn btn-ghost" data-drop-asset="${escapeHtml(a.id)}">Удалить</button>`. В `refresh()` после `if (stopped) return` — `shown = assets`. `wire()`:

```ts
  function wire(): void {
    list.querySelectorAll<HTMLButtonElement>('button[data-drop-asset]').forEach(b =>
      b.addEventListener('click', async () => {
        const asset = shown.find(a => a.id === b.dataset.dropAsset)
        if (!asset || !window.confirm(dropAssetQuestion(asset, myEmail))) return
        b.disabled = true
        try {
          await deleteAsset(asset.id)
        } catch (e) {
          b.disabled = false
          showError(e)
          return
        }
        errorBox.hidden = true
        onChanged?.()
        await refresh().catch(showError)
      }),
    )
  }
```

Удалить `teamCard`, `refreshTeam` и строку `void refreshTeam().catch(showError)`.

- [ ] **Step 4: `overview.ts`.** Шапка модуля:

```ts
/**
 * Подписи автора и итог места по людям.
 *
 * Проекты и записи общие: их видит вся команда, и на карточке стоит автор — «вы» у своего. Итог
 * «кто сколько занимает на диске» остался в «Кабинете доступа»: он отвечает на вопрос, чем занят
 * диск, и ему место рядом с доступами.
 */
```

Удалить `TeamAsset`, `othersOnly`, `loadTeamAssets`; в `mountOverview` строку про чужие проекты и записи заменить на `<p class="meta" style="margin:0">Проекты и записи всей команды — на экранах <a href="#/projects">«Проекты»</a> и <a href="#/files">«Записи»</a>; здесь — кто сколько занимает на диске.</p>`.

- [ ] **Step 5:** `npx vitest run` и `npx tsc --noEmit` → зелёное.

---

### Task 13: Главная — «Недавнее» команды

**Files:**
- Modify: `web/src/home.ts` (`recentRow` → экспортируемая `recentRowHtml`, `recentBlock`, `mountHome`)
- Test: `web/src/home.test.ts`

- [ ] **Step 1: Падающий тест.** Импорт `import { homeStepsHtml, recentRowHtml } from './home'` и `import type { ProjectCard } from './project'`; в конец:

```ts
describe('недавнее', () => {
  const p = (over: Partial<ProjectCard> = {}): ProjectCard => ({
    id: 'prj_1',
    name: 'Планёрка',
    version: 1,
    created_at: 'x',
    updated_at: 'x',
    clips_count: 3,
    duration: 42,
    owner_email: 'liza@ya.ru',
    owner_name: 'Лиза',
    ...over,
  })

  it('у проекта коллеги называет автора', () => {
    expect(recentRowHtml(p(), 0, 'gleb@ya.ru')).toContain('Лиза · 3 кл.')
  })

  it('у своего проекта автора не пишет', () => {
    expect(recentRowHtml(p({ owner_email: 'gleb@ya.ru' }), 0, 'gleb@ya.ru')).toContain('>3 кл.')
  })
})
```

- [ ] **Step 2:** `npx vitest run src/home.test.ts` → падает.

- [ ] **Step 3: Реализация.** Импорт `import { ownedBy, ownerLabel } from './overview'`. `recentRow` заменить:

```ts
/** Строка «Недавнего»: имя, у чужого проекта — автор, клипы и длительность. */
export function recentRowHtml(p: ProjectCard, i: number, myEmail: string): string {
  const whose = ownedBy(p, myEmail) ? '' : `${escapeHtml(ownerLabel(p))} · `
  return `
    <a class="row appear" href="#/p/${encodeURIComponent(p.id)}" style="${ROW_STYLE};--delay:${i * ROW_STEP_MS}ms">
      <span>${escapeHtml(p.name)}</span>
      <span class="meta">${whose}${p.clips_count} кл. · ${fmtDuration(p.duration)}</span>
    </a>`
}
```

`recentBlock(projects: ProjectCard[], myEmail: string)`, внутри `projects.map((p, i) => recentRowHtml(p, i, myEmail))`; в `mountHome` вызов `recentBlock(projects.slice(0, RECENT_LIMIT), me.email)`; комментарий над `listProjects()` — «“Недавнее” — три последних проекта команды; дорисовывается, когда придёт список…».

- [ ] **Step 4:** `npx vitest run` и `npx tsc --noEmit` → зелёное.

---

### Task 14: «Исходники» редактора — все записи, у чужой автор

**Files:**
- Modify: `web/src/source.ts` (`sourceOptionLabel`, `mountSource`, `setAssets`)
- Modify: `web/src/editor.ts` (`mountEditor(el, projectId, me)`, вызов `mountSource`)
- Modify: `web/src/main.ts` (`mountEditor(…, me)`)
- Test: `web/src/source.test.ts`

- [ ] **Step 1: Падающий тест.** В импорт добавить `sourceOptionLabel`; в конец:

```ts
test('у чужой записи в списке рядом с именем автор', () => {
  const a = asset({ owner_email: 'liza@ya.ru', owner_name: 'Лиза' })
  expect(sourceOptionLabel(a, 'gleb@ya.ru')).toBe('a.mp4 — Лиза')
  expect(sourceOptionLabel(a, 'liza@ya.ru')).toBe('a.mp4')
})
```

- [ ] **Step 2:** `npx vitest run src/source.test.ts` → падает.

- [ ] **Step 3: Реализация.** `source.ts`: импорт `import { ownedBy, ownerLabel } from './overview'`;

```ts
/** Строка списка «Исходников»: у чужой записи рядом с именем автор — записи общие. */
export function sourceOptionLabel(a: Asset, myEmail: string): string {
  return ownedBy(a, myEmail) ? a.original_name : `${a.original_name} — ${ownerLabel(a)}`
}
```

сигнатура `export function mountSource(el: HTMLElement, handlers: SourceHandlers, myEmail = '')`; в `setAssets` `${escapeHtml(a.original_name)}` → `${escapeHtml(sourceOptionLabel(a, myEmail))}`. `editor.ts`: `import type { Me } from './shell'`, `export function mountEditor(el: HTMLElement, projectId: string, me: Me)`, у `mountSource(sourceMain, { … })` третьим аргументом `me.email`. `main.ts`: `current = mountEditor(shell.screen, route.projectId, me)`.

- [ ] **Step 4:** `npx vitest run` и `npx tsc --noEmit` → зелёное.

---

### Task 15: Редактор подтягивает чужие правки

**Files:**
- Create: `web/src/sync.ts`, `web/src/sync.test.ts`
- Modify: `web/src/editor.ts` (импорт, `gone`, `pollAssets`, `onConflict`, `onError`)

- [ ] **Step 1: Падающий тест** — `web/src/sync.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { takeFresh } from './sync'

describe('свежий документ из опроса', () => {
  it('подставляется, когда проект подняли, а своей несохранённой правки нет', () => {
    expect(takeFresh(3, 4, false)).toBe(true)
  })

  it('не подставляется поверх несохранённой правки: опрос не должен её стирать', () => {
    expect(takeFresh(3, 4, true)).toBe(false)
  })

  it('старый или тот же ответ ничего не меняет', () => {
    expect(takeFresh(4, 4, false)).toBe(false)
    expect(takeFresh(5, 4, false)).toBe(false)
  })
})
```

- [ ] **Step 2:** `npx vitest run src/sync.test.ts` → падает.

- [ ] **Step 3: Реализация** — `web/src/sync.ts`:

```ts
/**
 * Совместная правка проекта: когда подставлять свежий документ и что сказать человеку.
 *
 * Проект открывает и правит любой из команды. Редактор узнаёт о чужой правке из опроса, который и
 * так идёт раз в 20 с, и подставляет свежий документ, только если у человека нет правки, которой
 * ещё нет на сервере: иначе опрос молча стёр бы её.
 */
export function takeFresh(localVersion: number, serverVersion: number, unsaved: boolean): boolean {
  return serverVersion > localVersion && !unsaved
}

export const FRESH_NOTICE = 'Проект обновили — показана свежая версия'
export const CONFLICT_TEXT =
  'Проект успели изменить — ваша последняя правка не сохранилась, показана свежая версия'
export const GONE_TEXT = 'Проект удалили — правки больше не сохраняются'
```

`editor.ts`: импорт `import { CONFLICT_TEXT, FRESH_NOTICE, GONE_TEXT, takeFresh } from './sync'`. После `showError`:

```ts
  const gone = () => {
    errorBox.hidden = false
    errorBox.textContent = GONE_TEXT
  }
```

В `pollAssets` тело таймера заменить:

```ts
    assetTimer = window.setTimeout(() => {
      // Вместе с записями — сам проект: его правит вся команда, и чужая правка должна дойти до
      // открытого редактора, пока человек не начал править старое.
      void Promise.all([listProjectAssets(projectId), loadProject(projectId)])
        .then(([r, fresh]) => {
          if (stopped) return
          applyAssets(r.assets)
          if (project && takeFresh(project.version, fresh.version, saver.unsaved())) {
            project = fresh
            history.clear()
            syncBar()
            render()
            notice(FRESH_NOTICE)
          }
          pollAssets()
        })
        .catch(e => {
          if (stopped) return
          if (e instanceof ApiError && e.status === 404) gone()
          pollAssets()
        })
    }, soon ? POLL_MS : IDLE_POLL_MS)
```

В `createSaver({…})`: `onConflict` — вместо `notice('Проект изменился в другом месте, показана свежая версия')`:

```ts
      // Не подсказка на 6 секунд: человек должен узнать, что его правки нет.
      errorBox.hidden = false
      errorBox.textContent = CONFLICT_TEXT
```

`onError: showError` → `onError: e => (e instanceof ApiError && e.status === 404 ? gone() : showError(e)),`.

- [ ] **Step 4:** `npx vitest run` и `npx tsc --noEmit` → зелёное.

---

### Task 16: Конвертер — только свои записи и конвертации

**Files:**
- Modify: `web/src/convert.ts` (`runningConvertsFromJobs`, `mountConvert`), `web/src/main.ts`
- Test: `web/src/convert.test.ts`

- [ ] **Step 1: Падающий тест.** В `it('подхватывает идущую конвертацию после перезагрузки экрана', …)` заголовок → `'подхватывает свою идущую конвертацию после перезагрузки экрана, чужую — нет'`; в список заданий после третьего элемента (`id: 'job_3'`) добавить:

```ts
      // Чужая конвертация: очередь общая, но конвертер личный — её не подхватываем.
      {
        id: 'job_4',
        type: 'convert',
        status: 'running',
        progress: 0.2,
        error: null,
        created_at: '',
        finished_at: null,
        label: 'b.mp4',
        cancelable: true,
        quality: null,
        target_id: 'ast_9',
        owner_email: 'x@y.z',
        owner_name: 'X',
      },
```

и закрывающую `])` этого вызова заменить на `], 'A@B.c')`.

- [ ] **Step 2:** `npx vitest run src/convert.test.ts` → падает (чужое задание подхвачено).

- [ ] **Step 3: Реализация.** `convert.ts`: импорт `import { ownedBy } from './overview'` и `import type { Me } from './shell'`;

```ts
/** Свои идущие convert-задания с GET /jobs: после перезагрузки экрана pending пуст. Очередь общая,
 * а конвертер личный — чужие конвертации не подхватываем. */
export function runningConvertsFromJobs(
  items: JobListItem[],
  myEmail: string,
): Array<{ assetId: string; job: JobView }> {
  return items
    .filter(job => job.type === 'convert' && CONVERT_RUNNING.has(job.status) && ownedBy(job, myEmail))
    .map(job => ({
      assetId: job.target_id,
      job: {
        id: job.id,
        type: job.type,
        status: job.status,
        progress: job.progress,
        error: job.error,
      },
    }))
}
```

`export function mountConvert(el: HTMLElement, me: Me)`; в `refresh` `await listAssets()` → `await listAssets(true)`, `runningConvertsFromJobs(listedJobs)` → `runningConvertsFromJobs(listedJobs, me.email)`. `main.ts`: `current = mountConvert(shell.screen, me)`.

- [ ] **Step 4:** `npx vitest run` и `npx tsc --noEmit` → зелёное.

---

### Task 17: Тексты — токен, полоса работ

**Files:**
- Modify: `web/src/settings.ts` (абзац про токен), `web/src/work.ts` (комментарий поля `owner`)

- [ ] **Step 1:** `settings.ts` — абзац под «Токены для агента»:

```ts
          <p class="lead" style="margin:0">Токен действует от вашего имени и открывает через API все
            проекты и записи команды — с правкой и удалением. Выдавайте его программе, а не человеку</p>
```

- [ ] **Step 2:** `work.ts` — комментарий над `owner: string | null` → `/** Чьё, если не моё: ход всей команды видят все, и без имени не понять, чья это сборка. */`.

- [ ] **Step 3:** `npx tsc --noEmit` и `npm run build` → чисто.

---

### Task 18: README и смоук агента

**Files:**
- Modify: `README.md` (разделы «Загрузка и файлы», «Проекты», «Экраны», «Соседи»)
- Modify: `tools/agent_smoke.py` (шаг 8/8)

- [ ] **Step 1: README.**
  - Строку `- \`GET /api/v1/assets\`, \`GET /api/v1/assets/{id}\` (…), \`DELETE /api/v1/assets/{id}\`. Квота и использование в \`GET /api/v1/me\`.` дополнить предложением: «Записи общие: список — все записи команды, свежие сверху, с автором (`owner_email`, `owner_name`), `?mine=1` — только свои; карточку, расшифровку и удаление получает любой вошедший; запись, которая стоит в чьём-либо проекте, не удаляется (`409 asset_in_use` называет проекты и их авторов).»
  - В строке Janitor «ассеты без обращений старше 24 ч» → «записи вне проектов без обращений старше 7 дней (кадры и волна в списках срок не продлевают)».
  - В разделе «Проекты (M2a)» первую строку: «`GET /api/v1/projects` отдаёт все проекты команды без документов, свежие правки сверху, с автором; `?mine=1` — только свои»; после неё новая строка: «- Проекты общие: открыть, сохранить, сделать точку, откатить, собрать и удалить может любой вошедший; работа идёт от имени автора — файлы в его каталоге, лимит его. Кто что удалил или отменил, пишет журнал сервера (логгер `video.audit`).» В строке про `DELETE` «после чего они уходят по обычному сроку хранения» → «после чего живут полный срок хранения (7 дней) с момента удаления». В строке «Файл, занятый в проекте, не удаляется: `409 asset_in_use` со списком проектов» → «Запись, занятая в любом проекте, не удаляется: `409 asset_in_use` со списком проектов и их авторов».
  - В «Экраны (M5)»: «ниже недавние проекты, если они есть» → «ниже три последних проекта команды»; «`#/files` — склад: загрузка, статус обработки, удаление.» → «`#/files` — общий склад команды: загрузка, статус обработки, автор, удаление; `#/projects` — все проекты команды, на карточке автор.»
  - В «Соседи»: «у нас файлы живут сутки» → «у нас записи вне проектов живут неделю, ролики — сутки».

- [ ] **Step 2: смоук** — после `say("8/8", "проект удалён вместе с роликами")`:

```python
    # Записи общие и живут неделю: без удаления прогон висел бы у всей команды в «Записях».
    call(base, token, "DELETE", f"/api/v1/assets/{asset_id}")
    say("8/8", "запись удалена")
```

- [ ] **Step 3:** `uv run ruff check tools` → чисто.

---

### Task 19: Проверка целиком и на стенде

- [ ] **Step 1:** `uv run python -m pytest -q` → всё зелёное; `uv run ruff check server tests tools` → чисто; `cd web && npx vitest run && npx tsc --noEmit && npm run build` → зелёное.
- [ ] **Step 2: Стенд** (8010, `scratchpad/stand/data`, воркер `uv run python -m server.worker` с тем же `VIDEO_DATA_DIR`): от имени Лизы через API (скрипт по образцу `liza_jobs.py`) — `GET /api/v1/assets` содержит картинку и звук админа с автором; `PUT` её проекта `prj_630ebbaa2976` с наложением (картинка админа, V2) и звуком (звук админа, A2) → 200; `POST …/render` → 202, задание `done`, ролик в папке Лизы. В браузере (снимки `cdp_pages.mjs`): «Проекты» и «Записи» — общий список с автором, «Исходники» редактора — чужая запись с автором.
- [ ] **Step 3:** уборка стенда: процессы по командной строке, файлы входа из `web/dist`.
- [ ] **Step 4 (после отмашки владельца):** коммиты `docs:` (спека и план) и `feat:` (сервер, клиент, тесты, README, смоук), пуш, выкатка обычным порядком. После выкатки: попросить команду обновить открытые вкладки; `/me`, `/api/v1/projects`, `/api/v1/assets` с продa; журнал `video-api` и `video-janitor` — предупреждений нет, уборщик не удаляет лишнего.

---

## Отступления при исполнении

- **Порядок зелёного набора.** После задачи 2 весь набор зелёным не был: три старых теста изоляции субтитров и сборок упали, потому что маршруты уже шли через общий `_project`, — их перевернули задачи 3 и 5. После задачи 5 упал `test_foreign_conversion_is_404`: очередь стала общей, и чужое задание конвертации больше не 404, — его перевернула задача 6. Весь набор зелёный с задачи 6.
- **Редактор, проект удалён.** На `404` из опроса редактор показывает «Проект удалили — правки больше не сохраняются» и опрос останавливает, а не продолжает раз в 20 с: проекта нет, и спрашивать о нём общую ВМ незачем.
- **Редактор, просмотр.** Свежий документ из опроса и документ из конфликта версий при идущем просмотре переставляют плеер по времени шкалы (`seek`), как после своей правки, отмены и возврата: иначе плеер мерил бы время клипа, которого в новом списке может не быть.
- **Устаревшие комментарии.** Кроме `settings.ts` и `work.ts` поправлены шапки `files.ts`, `convert.ts`, `admin.ts`, `shell.ts` и комментарий в `render.ts` — там ещё было «админ открывает чужие проекты».
- **README шире плана.** Кроме перечисленного поправлены: кому отдаются файлы (записи и ролики — любому вошедшему, конвертации — автору и админу), список заданий в «Рендере» (общая очередь, `?mine=1`, предел только законченным), `403 not_yours` у конвертера и раздел «Единый кабинет администрирования» — админских списков команды больше нет, `/projects/{id}/assets` отдаёт все записи команды. В смоуке поправлена и шапка: прогон удаляет проект и запись.
- **Стенд.** Проверка шагом 2 дополнена: кадр собранного ролика (картинка админа на V2 поверх клипа Лизы, звуковая дорожка есть) и живая проверка задачи 15 — Лиза переименовала проект через API, открытый у админа редактор показал новое имя и «Проект обновили — показана свежая версия» через 11 с; имя затем возвращено.
- **Окружение.** Системный Python 3.10 с `PYTHONUTF8=1` не стартует (битый `.pth` в site-packages), скрипты правки шли через `uv run python`. Команда шага 1 с `-q` глушит итоговую строку pytest: `-q` уже стоит в `addopts`, второй убирает итог. Запускать `uv run python -m pytest` без него.
