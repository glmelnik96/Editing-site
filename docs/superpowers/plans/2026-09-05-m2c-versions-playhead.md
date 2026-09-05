# M2c: ручные сохранения с откатом и навигация по шкале

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** закрыть две дыры, из-за которых монтировать на практике нельзя. Первая: некуда откатиться, если испортил монтаж — нужны ручные сохранения и пул из пяти последних с возвратом. Вторая: курсор шкалы никуда не двигается, а без перемотки вперёд и назад монтаж невозможен.

**Architecture:** снимки документа живут в новой таблице `project_versions`, по пять на проект; ручное сохранение делает снимок текущего состояния, возврат кладёт снимок обратно как обычное сохранение (версия растёт, история не переписывается). Навигация по шкале целиком во фронтенде: клик и перетаскивание курсора, клавиши, показ текущего времени.

**Спека:** `docs/superpowers/specs/2026-09-03-video-editor-design.md`, разделы 4, 5, 8. **Предыдущие планы:** `2026-09-05-m2a-projects.md`, `2026-09-05-m2b-timeline.md`.

---

## Решения M2c

| Вопрос | Решение | Почему |
|---|---|---|
| Что попадает в пул | Только ручные сохранения (кнопка «Сохранить точку») | Автосохранение идёт каждые полсекунды тишины: в пуле из пяти оно вытеснило бы всё осмысленное |
| Размер пула | Пять последних на проект, старые вытесняются | Просьба пользователя; настройка `VIDEO_VERSIONS_KEPT` |
| Что такое возврат | Снимок применяется как обычное сохранение: версия растёт, документ заменяется | История не переписывается, откат самого отката возможен |
| Имя точки | Необязательное, до 100 знаков; пустое заменяется временем | Человеку проще узнать «до перестановки», чем «версия 7» |
| Снимок хранит | Документ и имя проекта целиком | Возврат должен восстанавливать и название |
| Клик по шкале | Всегда перемотка, плюс выделение блока под курсором | Сейчас клик по блоку только выделяет, и курсор стоит на месте |
| Перетаскивание | По блоку — перенос, по краю — подрезка, по линейке и полосе курсора — перемотка | Разведено по зонам, как в монтажных программах |
| Клавиши | Пробел — играть и стоп, стрелки — шаг 1 с, с Shift 0.1 с, Home и End — края | Монтаж делается с клавиатуры |
| Текущее время | Показывается рядом с общим: `0:03.5 / 0:10.0` | Без него непонятно, где стоишь |

## Структура файлов

| Файл | Обязанность |
|---|---|
| `server/db/migrations/0006_project_versions.sql` | Таблица снимков |
| `server/app/config.py`, `.env.example` | + `versions_kept` |
| `server/app/projects/store.py` | Снимок, список снимков, возврат, вытеснение старых |
| `server/app/projects/routes.py` | `POST /checkpoint`, `GET /versions`, `POST /restore` |
| `web/src/project.ts` | Клиент трёх новых маршрутов |
| `web/src/versions.ts` | Панель точек сохранения |
| `web/src/timeline/view.ts` | Перемотка кликом, перетаскивание курсора, отличие клика от переноса |
| `web/src/editor.ts` | Кнопка ручного сохранения, панель версий, клавиши, показ времени |
| `web/src/style.css` | Стили панели версий и полосы курсора |

Команды: `uv run python -m pytest`, `uv run ruff check .`, `cd web && npm test && npm run build`. Ветка: `m2c-versions-playhead` от `main`.

---

### Task 1: Таблица снимков и настройка

**Files:**
- Create: `server/db/migrations/0006_project_versions.sql`
- Modify: `server/app/config.py`, `.env.example`, `tests/test_db_migrate.py`, `tests/test_config.py`

- [ ] **Step 1: Тесты**

Добавить в `tests/test_config.py`:

```python
def test_versions_kept_default():
    s = Settings(_env_file=None)
    assert s.versions_kept == 5


def test_versions_kept_is_bounded():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, versions_kept=0)
```

В `tests/test_db_migrate.py` обновить оба точных списка версий (искать `migrate(conn) ==`): `[1, 2, 3, 4, 5]` → `[1, 2, 3, 4, 5, 6]` и `[2, 3, 4, 5]` → `[2, 3, 4, 5, 6]`. В множество `TABLES` добавить `"project_versions"`.

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_config.py tests/test_db_migrate.py`
Expected: FAIL.

- [ ] **Step 3: Миграция и настройка**

Создать `server/db/migrations/0006_project_versions.sql`:

```sql
-- Точки сохранения проекта: снимок документа и имени на момент нажатия кнопки.
-- Автосохранение сюда не пишет: пул маленький, и полминуты правок вытеснили бы всё осмысленное.
CREATE TABLE project_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    doc TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX project_versions_project_idx ON project_versions(project_id, created_at DESC);
```

В `server/app/config.py` рядом с пределами проекта:

```python
    versions_kept: int = Field(default=5, ge=1, le=50)
```

В `.env.example` рядом с `VIDEO_MAX_CLIPS`:

```
# Сколько точек сохранения проекта держать (старые вытесняются)
VIDEO_VERSIONS_KEPT=5
```

- [ ] **Step 4: Прогон и коммит**

Run: `uv run python -m pytest && uv run ruff check .`

```bash
git add server/db/migrations/0006_project_versions.sql server/app/config.py .env.example tests/test_config.py tests/test_db_migrate.py
git commit -m "feat(db): migration 0006 (project versions) and pool size setting"
```

---

### Task 2: Снимки в хранилище

**Files:**
- Modify: `server/app/projects/store.py`
- Test: `tests/test_project_versions.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_project_versions.py`:

```python
import pytest

from server.app.config import Settings
from server.app.projects.doc import ProjectInvalid
from server.app.projects.store import (
    create_checkpoint,
    create_project,
    finish_project,
    get_project,
    list_versions,
    restore_version,
    save_project,
)
from server.app.storage import asset_dir
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate

USER = "usr_00000000000a"
ASSET = "ast_000000000001"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", versions_kept=3)


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = connect(settings.db_path)
    migrate(c)
    c.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)", (USER, now_iso())
    )
    c.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
        "created_at, last_access_at) VALUES (?, ?, 'video', 'a', 'mp4', 1, 'ready', 120, ?, ?)",
        (ASSET, USER, now_iso(), now_iso()),
    )
    folder = asset_dir(settings, USER, ASSET)
    folder.mkdir(parents=True, exist_ok=True)
    yield c
    c.close()


def doc(out=5.0):
    return {"clips": [{"asset_id": ASSET, "in": 1.0, "out": out}]}


def test_checkpoint_snapshots_the_current_state(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    made = create_checkpoint(conn, settings, USER, p["id"], label="до перестановки")
    assert made["version"] == p["version"] and made["label"] == "до перестановки"
    versions = list_versions(conn, USER, p["id"])
    assert len(versions) == 1
    assert versions[0]["clips_count"] == 1 and versions[0]["duration"] == 4.0
    assert versions[0]["name"] == "Мой"


def test_empty_label_is_allowed(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    made = create_checkpoint(conn, settings, USER, p["id"], label="")
    assert made["label"] == ""


def test_too_long_label_is_rejected(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    with pytest.raises(ProjectInvalid) as e:
        create_checkpoint(conn, settings, USER, p["id"], label="я" * 201)
    assert e.value.errors[0]["field"] == "label"


def test_pool_keeps_only_the_newest(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    for step in range(5):
        saved = save_project(
            conn, settings, USER, p["id"], name="Мой", raw_doc=doc(5 + step), version=p["version"] + step
        )
        create_checkpoint(conn, settings, USER, p["id"], label=f"точка {step}")
        assert saved["version"] == p["version"] + step + 1
    versions = list_versions(conn, USER, p["id"])
    assert [v["label"] for v in versions] == ["точка 4", "точка 3", "точка 2"]  # versions_kept = 3


def test_restore_puts_the_snapshot_back_as_a_new_save(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc(5.0))
    point = create_checkpoint(conn, settings, USER, p["id"], label="хорошая")
    save_project(conn, settings, USER, p["id"], name="Испорчено", raw_doc=doc(9.0), version=1)
    restored = restore_version(conn, settings, USER, p["id"], point["id"])
    assert restored["version"] == 3  # 1 создание, 2 порча, 3 возврат
    assert restored["doc"]["clips"][0]["out"] == 5.0
    assert restored["name"] == "Мой"
    assert get_project(conn, USER, p["id"])["doc"]["clips"][0]["out"] == 5.0


def test_restore_keeps_the_pool_intact(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    point = create_checkpoint(conn, settings, USER, p["id"], label="первая")
    restore_version(conn, settings, USER, p["id"], point["id"])
    assert [v["label"] for v in list_versions(conn, USER, p["id"])] == ["первая"]


def test_versions_are_scoped_to_the_owner(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    point = create_checkpoint(conn, settings, USER, p["id"], label="моя")
    assert list_versions(conn, "usr_00000000000b", p["id"]) == []
    with pytest.raises(KeyError):
        restore_version(conn, settings, "usr_00000000000b", p["id"], point["id"])


def test_restore_of_a_missing_point_is_an_error(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    with pytest.raises(KeyError):
        restore_version(conn, settings, USER, p["id"], "pvr_00000000dead")


def test_finished_project_takes_no_checkpoints(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    finish_project(conn, settings, USER, p["id"])
    with pytest.raises(ProjectInvalid) as e:
        create_checkpoint(conn, settings, USER, p["id"], label="поздно")
    assert e.value.errors[0]["field"] == "status"


def test_deleting_a_project_takes_its_versions(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    create_checkpoint(conn, settings, USER, p["id"], label="точка")
    conn.execute("DELETE FROM projects WHERE id = ?", (p["id"],))
    assert conn.execute("SELECT count(*) FROM project_versions").fetchone()[0] == 0
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_project_versions.py`
Expected: FAIL.

- [ ] **Step 3: Реализация**

В `server/app/projects/store.py` добавить:

```python
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
```

- [ ] **Step 4: Прогон и коммит**

Run: `uv run python -m pytest && uv run ruff check .`

```bash
git add server/app/projects/store.py tests/test_project_versions.py
git commit -m "feat(projects): manual checkpoints with a bounded pool and restore"
```

---

### Task 3: Маршруты точек сохранения

**Files:**
- Modify: `server/app/projects/routes.py`
- Test: `tests/test_projects_api.py`

- [ ] **Step 1: Тесты**

Добавить в `tests/test_projects_api.py`:

```python
def test_checkpoints_list_restore(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()

    r = client.post(f"/api/v1/projects/{p['id']}/checkpoint", json={"label": "до правок"})
    assert r.status_code == 201, r.text
    point = r.json()
    assert point["label"] == "до правок" and point["clips_count"] == 1

    bad = {"clips": [{"asset_id": VIDEO, "in": 0, "out": 30}]}
    client.put(f"/api/v1/projects/{p['id']}", json={"name": "Испорчено", "version": 1, "doc": bad})

    versions = client.get(f"/api/v1/projects/{p['id']}/versions").json()["versions"]
    assert [v["label"] for v in versions] == ["до правок"]

    r = client.post(f"/api/v1/projects/{p['id']}/restore", json={"version_id": point["id"]})
    assert r.status_code == 200, r.text
    restored = r.json()
    assert restored["version"] == 3 and restored["name"] == "Мой"
    assert restored["doc"]["clips"][0]["out"] == 5.0


def test_checkpoint_without_a_label(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    r = client.post(f"/api/v1/projects/{p['id']}/checkpoint", json={})
    assert r.status_code == 201 and r.json()["label"] == ""


def test_restore_of_a_foreign_point_is_404(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    r = client.post(f"/api/v1/projects/{p['id']}/restore", json={"version_id": "pvr_00000000dead"})
    assert r.status_code == 404


def test_versions_of_a_foreign_project_are_404(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(f"/api/v1/projects/{p['id']}/versions").status_code == 404
    assert client.post(f"/api/v1/projects/{p['id']}/checkpoint", json={}).status_code == 404
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_projects_api.py`
Expected: FAIL.

- [ ] **Step 3: Маршруты**

В `server/app/projects/routes.py` добавить модели и три обработчика:

```python
class CheckpointCreate(BaseModel):
    label: str = Field(default="", max_length=200)


class RestoreRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=64)


class VersionView(BaseModel):
    id: str
    version: int
    label: str
    name: str
    created_at: str
    clips_count: int
    duration: float


class VersionList(BaseModel):
    versions: list[VersionView]


@router.post("/{project_id}/checkpoint", status_code=201, response_model=VersionView)
def checkpoint(
    project_id: str,
    body: CheckpointCreate,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> VersionView:
    _owned(conn, user, project_id)
    try:
        made = create_checkpoint(
            conn, request.app.state.settings, user.id, project_id, label=body.label
        )
    except ProjectInvalid as exc:
        raise invalid(exc) from exc
    except KeyError as exc:
        raise ApiError(404, "not_found", "Проект не найден") from exc
    return VersionView(**made)


@router.get("/{project_id}/versions", response_model=VersionList)
def versions(
    project_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> VersionList:
    _owned(conn, user, project_id)
    return VersionList(versions=[VersionView(**v) for v in list_versions(conn, user.id, project_id)])


@router.post("/{project_id}/restore", response_model=ProjectView)
def restore(
    project_id: str,
    body: RestoreRequest,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    _owned(conn, user, project_id)
    try:
        project = restore_version(
            conn, request.app.state.settings, user.id, project_id, body.version_id
        )
    except ProjectInvalid as exc:
        raise invalid(exc) from exc
    except ProjectConflict as exc:
        raise conflict(exc) from exc
    except KeyError as exc:
        raise ApiError(404, "not_found", "Точка сохранения не найдена") from exc
    return ProjectView(**project)
```

Импорты дополнить: `create_checkpoint`, `list_versions`, `restore_version` из `server.app.projects.store`.

- [ ] **Step 4: Прогон и коммит**

Run: `uv run python -m pytest && uv run ruff check .`

```bash
git add server/app/projects/routes.py tests/test_projects_api.py
git commit -m "feat(projects): checkpoint, versions and restore endpoints"
```

---

### Task 4: Навигация по шкале

**Files:**
- Modify: `web/src/timeline/view.ts`, `web/src/style.css`
- Test: живая проверка

- [ ] **Step 1: Правки шкалы**

В `web/src/timeline/view.ts`:

1. Разметку дополнить полосой перемотки под линейкой:

```ts
  el.innerHTML = `
    <div class="timeline">
      <div class="ruler" id="tl-ruler"></div>
      <div class="scrub" id="tl-scrub" title="Перемотка"></div>
      <div class="track" id="tl-track"><div class="playhead" id="tl-playhead"></div></div>
      <div class="tl-hint muted" id="tl-hint"></div>
    </div>`
```

и получить `const scrub = el.querySelector('#tl-scrub') as HTMLElement`.

2. Завести порог, отличающий клик от переноса, и запоминать, двигался ли указатель:

```ts
const CLICK_SLOP_PX = 4 // сдвиг меньше этого — это клик, а не перенос
```

В объект `drag` добавить поле `moved: boolean`, ставить `false` при `pointerdown`, а в `pointermove` — `drag.moved = drag.moved || Math.abs(event.clientX - drag.startX) > CLICK_SLOP_PX`.

3. В `finishDrag` ничего не менять, если указатель не двигался: вместо правки списка сделать перемотку и выделение:

```ts
  function finishDrag(clientX: number): void {
    if (!drag) return
    if (!drag.moved) {
      // Клик без переноса: ставим курсор туда, куда ткнули, и выделяем блок под ним.
      handlers.onSeek(timeAt(clientX))
      drag = null
      hint.textContent = ''
      return
    }
    ...остальное как было...
  }
```

4. Перемотка по полосе и по линейке: тянуть курсор указателем.

```ts
  let scrubbing = false
  const startScrub = (event: PointerEvent) => {
    scrubbing = true
    scrub.setPointerCapture(event.pointerId)
    handlers.onSeek(timeAt(event.clientX))
  }
  scrub.addEventListener('pointerdown', startScrub)
  ruler.addEventListener('pointerdown', startScrub)
  scrub.addEventListener('pointermove', event => {
    if (scrubbing) handlers.onSeek(timeAt(event.clientX))
  })
  const endScrub = () => {
    scrubbing = false
  }
  scrub.addEventListener('pointerup', endScrub)
  scrub.addEventListener('pointercancel', endScrub)
```

5. `setPlayhead` должен ещё и подтягивать видимую область, если курсор ушёл за край прокрутки:

```ts
    setPlayhead(time: number): void {
      const left = time * current.pxPerSec
      playhead.style.left = `${left}px`
      const view = el.querySelector('.timeline') as HTMLElement
      const margin = 40
      if (left < view.scrollLeft + margin) view.scrollLeft = Math.max(0, left - margin)
      else if (left > view.scrollLeft + view.clientWidth - margin) {
        view.scrollLeft = left - view.clientWidth + margin
      }
    },
```

- [ ] **Step 2: Стили**

Добавить в `web/src/style.css`:

```css
.scrub { position: relative; height: 12px; background: #8881; cursor: ew-resize; touch-action: none; }
.playhead { z-index: 3; }
.timeline { position: relative; }
```

- [ ] **Step 3: Прогон и коммит**

Run: `cd web && npm test && npm run build`

```bash
git add web/src/timeline/view.ts web/src/style.css
git commit -m "feat(web): scrubbing, click-to-seek and playhead follow"
```

---

### Task 5: Кнопки, клавиши и панель версий

**Files:**
- Create: `web/src/versions.ts`
- Modify: `web/src/project.ts`, `web/src/editor.ts`, `web/src/style.css`

- [ ] **Step 1: Клиент**

В `web/src/project.ts` добавить типы и три функции:

```ts
export type VersionCard = {
  id: string
  version: number
  label: string
  name: string
  created_at: string
  clips_count: number
  duration: number
}

export function listVersions(id: string): Promise<{ versions: VersionCard[] }> {
  return api<{ versions: VersionCard[] }>(`/api/v1/projects/${encodeURIComponent(id)}/versions`)
}

export function createCheckpoint(id: string, label: string): Promise<VersionCard> {
  return api<VersionCard>(`/api/v1/projects/${encodeURIComponent(id)}/checkpoint`, {
    method: 'POST',
    body: JSON.stringify({ label }),
  })
}

export function restoreVersion(id: string, versionId: string): Promise<Project> {
  return api<Project>(`/api/v1/projects/${encodeURIComponent(id)}/restore`, {
    method: 'POST',
    body: JSON.stringify({ version_id: versionId }),
  })
}
```

- [ ] **Step 2: Панель версий**

Создать `web/src/versions.ts`:

```ts
/** Точки сохранения проекта: показать пул, сохранить новую, вернуться к выбранной. */
import { ApiError } from './api'
import { fmtDuration } from './assets'
import { escapeHtml } from './html'
import { createCheckpoint, listVersions, restoreVersion, type Project, type VersionCard } from './project'

function when(iso: string): string {
  return iso.replace('T', ' ').slice(11, 19)
}

export function mountVersions(el: HTMLElement, projectId: string, onRestored: (p: Project) => void) {
  el.innerHTML = `
    <main class="card">
      <h3>Точки сохранения</h3>
      <form id="ver-form" class="row">
        <input name="label" placeholder="Например: до перестановки" maxlength="200" />
        <button type="submit">Сохранить точку</button>
      </form>
      <ul id="ver-list" class="versions"><li class="muted">Пока нет</li></ul>
      <pre id="ver-error" hidden></pre>
    </main>`
  const list = el.querySelector('#ver-list') as HTMLElement
  const errorBox = el.querySelector('#ver-error') as HTMLPreElement

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  function row(v: VersionCard): string {
    const title = v.label || `версия ${v.version}`
    return `<li><span>${escapeHtml(title)} · ${when(v.created_at)} · ${v.clips_count} кл. · ${fmtDuration(v.duration)}</span>
      <button data-restore="${escapeHtml(v.id)}" data-title="${escapeHtml(title)}">Вернуться</button></li>`
  }

  async function refresh(): Promise<void> {
    const { versions } = await listVersions(projectId)
    list.innerHTML = versions.map(row).join('') || '<li class="muted">Пока нет</li>'
    list.querySelectorAll<HTMLButtonElement>('button[data-restore]').forEach(b =>
      b.addEventListener('click', async () => {
        if (!window.confirm(`Вернуться к точке «${b.dataset.title}»? Текущее состояние заменится.`)) return
        try {
          onRestored(await restoreVersion(projectId, b.dataset.restore ?? ''))
          await refresh()
        } catch (e) {
          showError(e)
        }
      }),
    )
  }

  const form = el.querySelector('#ver-form') as HTMLFormElement
  form.addEventListener('submit', async event => {
    event.preventDefault()
    const label = String(new FormData(form).get('label') ?? '').trim()
    try {
      await createCheckpoint(projectId, label)
      form.reset()
      await refresh()
    } catch (e) {
      showError(e)
    }
  })

  void refresh().catch(showError)
  return { refresh }
}
```

- [ ] **Step 3: Редактор**

В `web/src/editor.ts`:

1. В разметку после панели исходника добавить `<section id="ed-versions"></section>`, а в строку кнопок — показ времени и кнопку ручного сохранения:

```html
          <button id="ed-save" type="button">Сохранить точку</button>
          <span class="muted" id="ed-time">0:00.0</span>
```

2. Смонтировать панель версий после загрузки проекта:

```ts
    versions = mountVersions(el.querySelector('#ed-versions') as HTMLElement, projectId, restored => {
      project = restored
      timelineTime = 0
      render()
      notice('Вернулись к сохранённой точке')
    })
```

(переменная `let versions: { refresh: () => Promise<void> } | null = null` рядом с остальными).

3. Кнопка «Сохранить точку» сначала дописывает текущие правки, потом делает снимок:

```ts
  el.querySelector('#ed-save')!.addEventListener('click', async () => {
    if (!project) return
    try {
      // Сначала дописываем несохранённое: снимок должен поймать то, что видит человек.
      if (saver.pending()) await saver.flush(project)
      await createCheckpoint(projectId, '')
      await versions?.refresh()
      notice('Точка сохранена')
    } catch (e) {
      showError(e)
    }
  })
```

4. Показывать текущее время рядом с общим: завести

```ts
  const timeBox = el.querySelector('#ed-time') as HTMLElement
  function showTime(): void {
    timeBox.textContent = `${timelineTime.toFixed(1)} с`
  }
```

и звать `showTime()` в `seek`, в обработчике времени и в `render`.

5. Клавиши: пробел играет и останавливает, стрелки двигают курсор, Home и End прыгают на края. Слушатель вешать на `document`, снимать в `stop()`, игнорировать нажатия внутри полей ввода:

```ts
  function onKey(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null
    if (target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return
    if (!project) return
    const total = totalDuration(project.doc.clips)
    const step = event.shiftKey ? 0.1 : 1
    if (event.code === 'Space') {
      event.preventDefault()
      ;(el.querySelector('#ed-play') as HTMLButtonElement).click()
    } else if (event.code === 'ArrowLeft') {
      event.preventDefault()
      seek(Math.max(0, timelineTime - step))
    } else if (event.code === 'ArrowRight') {
      event.preventDefault()
      seek(Math.min(total, timelineTime + step))
    } else if (event.code === 'Home') {
      seek(0)
    } else if (event.code === 'End') {
      seek(Math.max(0, total - 0.05))
    }
  }
  document.addEventListener('keydown', onKey)
```

В `stop()` добавить `document.removeEventListener('keydown', onKey)`.

6. Подсказку по клавишам добавить в шапку редактора: `<span class="muted">пробел — играть, стрелки — шаг, Shift — точнее</span>`.

7. Важно: `seek` должен работать и когда воспроизведение не идёт. Если у клипа под курсором нет прокси, показать уведомление и не двигать курсор.

- [ ] **Step 4: Стили**

Добавить в `web/src/style.css`:

```css
.versions { list-style: none; padding: 0; margin: 8px 0 0; }
.versions li { display: flex; gap: 8px; align-items: center; justify-content: space-between;
  padding: 4px 0; border-bottom: 1px solid #8882; font-size: 13px; }
```

- [ ] **Step 5: Прогон и коммит**

Run: `cd web && npm test && npm run build`

```bash
git add web/src/versions.ts web/src/project.ts web/src/editor.ts web/src/style.css
git commit -m "feat(web): manual checkpoints panel, keyboard navigation and time readout"
```

---

### Task 6: Документация и выкатка

**Files:**
- Modify: `README.md`
- Живая проверка (координатор)

- [ ] **Step 1: README**

Дополнить раздел про редактор:

```markdown
### Точки сохранения и навигация (M2c)

- Кнопка «Сохранить точку» снимает состояние проекта; пул хранит пять последних, старые вытесняются. Кнопка «Вернуться» кладёт снимок обратно как обычное сохранение, поэтому версия растёт и откатить откат тоже можно.
- API: `POST /api/v1/projects/{id}/checkpoint` `{label?}`, `GET /api/v1/projects/{id}/versions`, `POST /api/v1/projects/{id}/restore` `{version_id}`.
- Навигация: клик по шкале ставит курсор, полоса под линейкой и сама линейка перематывают перетаскиванием, пробел играет и останавливает, стрелки двигают курсор на секунду (с Shift — на 0.1 с), Home и End прыгают на края. Текущее время видно рядом с общим.
```

- [ ] **Step 2: Прогон и коммит**

Run: `uv run python -m pytest && uv run ruff check . && cd web && npm test && npm run build`

```bash
git add README.md
git commit -m "docs: checkpoints and timeline navigation"
```

- [ ] **Step 3: Слияние и выкатка** (координатор)

- [ ] **Step 4: Живая проверка** (координатор): точка сохранения, порча монтажа, возврат, вытеснение шестой точки, перемотка кликом и перетаскиванием, клавиши.

---

## Поправки по ходу выполнения

- **Task 2**: в таблице решений было написано «имя точки до 100 знаков», а в тексте задачи и её тесте — 200. Сделано по тексту задачи, предел 200.
- **Task 5**: панель версий, вставленная третьей секцией в сетку редактора, выдавливала плеер во вторую строку и в узкую колонку. Исполнитель добавил правило раскладки, координатор переделал разметку: левая колонка теперь один блок с исходником и точками сохранения друг под другом, обе панели видны без прокрутки.
- **Живая проверка в браузере 2026-09-05** (локальный стенд, домен с ПК не резолвится): клик по блоку теперь перематывает курсор и выделяет блок одновременно (проверено настоящим кликом, а не синтетическим событием: синтетический не проходит `setPointerCapture`). Стрелки двигают курсор на секунду, с Shift на 0.1 с, Home и End прыгают на края, перетаскивание полосы перематывает. Точка сохранения с именем создана, монтаж испорчен удалением клипа, возврат восстановил оба клипа и длину, версия проекта выросла. Шесть точек подряд оставили в пуле ровно пять, самая старая вытеснена.
