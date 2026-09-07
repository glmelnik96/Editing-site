# Список хода загрузки и заданий — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ ПОД-НАВЫК: выполнять этот план задача за задачей через
> superpowers:subagent-driven-development (рекомендуется) или executing-plans. Шаги помечены чекбоксами (`- [ ]`).

**Цель:** человек видит загрузку, анализ, прокси, расшифровку и сборку в одном списке под шапкой на любом экране после входа.

**Спека:** `docs/superpowers/specs/2026-09-08-work-progress-list-design.md`.

**Архитектура:** `GET /api/v1/jobs` отдаёт живые и только что закончившиеся задания человека. Загрузки считает браузер. Оболочка (`web/src/shell.ts` + `web/src/work.ts`) склеивает оба источника и рисует строки. Полосы на «Записях», во вкладках «Субтитры» и «Рендер» уходят.

**Стек:** FastAPI / SQLite, vanilla TS, pytest, vitest. Опрос ~2–3 с, как карточки записей.

---

## Порядок

Сервер первым: без списка заданий оболочка не знает чужие сессии и обновление страницы. Чистые функции склейки строк — вторыми. Оболочка и экраны — третьими. README — в конце.

Кнопку сброса Whisper не добавляем. `GET /api/v1/jobs/{id}` не меняем.

---

## Файлы

| Файл | Зачем |
|---|---|
| `server/db/migrations/0009_jobs_user_status.sql` | индекс `(user_id, status, finished_at)` |
| `server/app/jobs.py` | выборка списка, подпись, `cancelable` |
| `server/app/renders/routes.py` | `GET /api/v1/jobs` рядом с уже живущим `GET /jobs/{id}` |
| `tests/test_jobs.py` | индекс и чистая выборка |
| `tests/test_jobs_api.py` | HTTP: свои / чужие / окно 30 с / 401 |
| `web/src/work.ts` | подписи, склейка загрузок и заданий, вспышка успеха, удержание сбоя |
| `web/src/work.test.ts` | тесты склейки |
| `web/src/project.ts` | `listJobs()` |
| `web/src/upload.ts` | обрыв по `AbortSignal` |
| `web/src/upload.test.ts` | тест обрыва |
| `web/src/shell.ts` | контейнер списка, опрос, выход гасит опрос |
| `web/src/main.ts` | передать `trackUpload` на экран записей |
| `web/src/files.ts` | полоски под зоной сброса убрать, загрузка идёт в оболочку |
| `web/src/subtitles.ts` | убрать `.progress` задания |
| `web/src/render.ts` | убрать полосу, оставить «Отменить сборку» |
| `web/src/style.css` | строки хода |
| `README.md` | `GET /api/v1/jobs` и список в шапке |

---

### Task 1: Выборка списка заданий

**Files:**
- Create: `server/db/migrations/0009_jobs_user_status.sql`
- Modify: `server/app/jobs.py`
- Test: `tests/test_jobs.py`

- [ ] **Step 1: Написать падающие тесты выборки**

В конец `tests/test_jobs.py` (фикстура `conn` уже есть):

```python
from datetime import UTC, datetime, timedelta

from server.app.jobs import LIST_LIMIT, RECENT_SEC, job_cancelable, job_label, list_jobs_for_user
from server.app.util import iso


def test_index_jobs_user_status_exists(conn):
    names = {row[1] for row in conn.execute("PRAGMA index_list(jobs)")}
    assert "jobs_user_status_idx" in names


def test_cancelable_only_live_transcribe_and_render():
    assert job_cancelable("transcribe", "queued") is True
    assert job_cancelable("render", "running") is True
    assert job_cancelable("analyze", "running") is False
    assert job_cancelable("proxy", "queued") is False
    assert job_cancelable("render", "done") is False


def test_label_falls_back_when_target_is_gone():
    assert job_label("analyze", None, None) == "запись удалена"
    assert job_label("render", None, None) == "проект удалён"
    assert job_label("transcribe", "Нарезка.mp4", None) == "Нарезка.mp4"


def _stamp(conn, job_id, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))


def test_list_includes_open_jobs_and_recent_finished_only(conn):
    now = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
    uid = "usr_000000000001"
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, created_at, last_access_at) "
        "VALUES ('ast_list1', ?, 'video', 'Нарезка.mp4', 'mp4', 1, 'proxy_ready', ?, ?)",
        (uid, now_iso(), now_iso()),
    )
    conn.execute(
        "INSERT INTO projects (id, user_id, name, status, version, doc, created_at, updated_at) "
        "VALUES ('prj_list1', ?, 'Ролик', 'draft', 1, '{}', ?, ?)",
        (uid, now_iso(), now_iso()),
    )
    open_id = enqueue_job(conn, user_id=uid, type_="analyze", target_id="ast_list1")
    fresh = enqueue_job(conn, user_id=uid, type_="render", target_id="prj_list1", params={"quality": "draft"})
    stale = enqueue_job(conn, user_id=uid, type_="transcribe", target_id="ast_list1")
    _stamp(conn, fresh, status="done", finished_at=iso(now - timedelta(seconds=5)), progress=1)
    _stamp(conn, stale, status="done", finished_at=iso(now - timedelta(seconds=RECENT_SEC + 1)), progress=1)
    other = enqueue_job(conn, user_id="usr_otheruser01", type_="analyze", target_id="ast_x")
    rows = list_jobs_for_user(conn, uid, now=now)
    ids = [r["id"] for r in rows]
    assert open_id in ids and fresh in ids
    assert stale not in ids and other not in ids
    analyze = next(r for r in rows if r["id"] == open_id)
    assert analyze["label"] == "Нарезка.mp4" and analyze["cancelable"] is False
    render = next(r for r in rows if r["id"] == fresh)
    assert render["label"] == "Ролик" and render["quality"] == "draft" and render["cancelable"] is False
```

Проверьте имена колонок `projects` в `server/db/migrations/0005_projects.sql` и поправьте INSERT, если набор полей другой. Пользователя `usr_otheruser01` тоже вставьте в `users`, иначе внешний ключ упадёт.

- [ ] **Step 2: Запустить тесты — должны упасть**

Run: `uv run python -m pytest tests/test_jobs.py -v`

Expected: FAIL — нет `list_jobs_for_user` / индекса.

- [ ] **Step 3: Миграция и реализация**

`server/db/migrations/0009_jobs_user_status.sql`:

```sql
CREATE INDEX jobs_user_status_idx ON jobs(user_id, status, finished_at);
```

В `server/app/jobs.py` добавить (импорты: `json`, `datetime` из `datetime`):

```python
RECENT_SEC = 30
LIST_LIMIT = 50

def job_cancelable(type_: str, status: str) -> bool:
    return type_ in ("transcribe", "render") and status in ("queued", "running")


def job_label(type_: str, asset_name: str | None, project_name: str | None) -> str:
    if type_ in ("analyze", "proxy", "transcribe"):
        return asset_name or "запись удалена"
    if type_ == "render":
        return project_name or "проект удалён"
    return asset_name or project_name or ""


def list_jobs_for_user(conn: sqlite3.Connection, user_id: str, *, now: datetime) -> list[dict]:
    cutoff = iso(now - timedelta(seconds=RECENT_SEC))
    rows = conn.execute(
        """
        SELECT jobs.id, jobs.type, jobs.status, jobs.progress, jobs.error, jobs.created_at,
               jobs.finished_at, jobs.params, jobs.target_id,
               assets.original_name AS asset_name, projects.name AS project_name
        FROM jobs
        LEFT JOIN assets
          ON assets.id = jobs.target_id AND jobs.type IN ('analyze', 'proxy', 'transcribe')
        LEFT JOIN projects
          ON projects.id = jobs.target_id AND jobs.type = 'render'
        WHERE jobs.user_id = ?
          AND (
            jobs.status IN ('queued', 'running')
            OR (
              jobs.status IN ('done', 'canceled', 'failed')
              AND jobs.finished_at IS NOT NULL
              AND jobs.finished_at >= ?
            )
          )
        ORDER BY jobs.created_at DESC
        LIMIT ?
        """,
        (user_id, cutoff, LIST_LIMIT),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        params = json.loads(row["params"] or "{}")
        quality = params.get("quality") if row["type"] == "render" else None
        out.append(
            {
                "id": row["id"],
                "type": row["type"],
                "status": row["status"],
                "progress": row["progress"],
                "error": row["error"],
                "created_at": row["created_at"],
                "finished_at": row["finished_at"],
                "label": job_label(row["type"], row["asset_name"], row["project_name"]),
                "cancelable": job_cancelable(row["type"], row["status"]),
                "quality": quality if quality in ("draft", "final") else None,
            }
        )
    return out
```

- [ ] **Step 4: Тесты зелёные**

Run: `uv run python -m pytest tests/test_jobs.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/db/migrations/0009_jobs_user_status.sql server/app/jobs.py tests/test_jobs.py
git commit -m "feat(jobs): выборка живых и только что закончившихся заданий"
```

---

### Task 2: HTTP `GET /api/v1/jobs`

**Files:**
- Modify: `server/app/renders/routes.py`
- Create: `tests/test_jobs_api.py`

- [ ] **Step 1: Падающие HTTP-тесты**

`tests/test_jobs_api.py`:

```python
import sqlite3

from server.app.jobs import enqueue_job
from server.app.util import now_iso


def test_list_requires_auth(client):
    assert client.get("/api/v1/jobs").status_code == 401


def test_list_returns_own_open_jobs(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, created_at, last_access_at) "
        "VALUES ('ast_jobsapi1', ?, 'video', 'a.mp4', 'mp4', 1, 'proxy_ready', ?, ?)",
        (me["id"], now_iso(), now_iso()),
    )
    job_id = enqueue_job(conn, user_id=me["id"], type_="transcribe", target_id="ast_jobsapi1")
    conn.commit()
    conn.close()
    r = client.get("/api/v1/jobs")
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    row = next(j for j in jobs if j["id"] == job_id)
    assert row["type"] == "transcribe" and row["label"] == "a.mp4"
    assert row["cancelable"] is True
    assert "progress" in row


def test_list_hides_other_users_jobs(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES ('usr_stranger01', 'x@y.z', 'X', ?)",
        (now_iso(),),
    )
    enqueue_job(conn, user_id="usr_stranger01", type_="analyze", target_id="ast_nope")
    conn.commit()
    conn.close()
    ids = [j["id"] for j in client.get("/api/v1/jobs").json()["jobs"]]
    assert all(j.startswith("job_") for j in ids)
    # чужой analyze не приехал; своих могло не быть
    conn = sqlite3.connect(str(settings.db_path))
    stranger = conn.execute("SELECT id FROM jobs WHERE user_id = 'usr_stranger01'").fetchone()[0]
    conn.close()
    assert stranger not in ids
    assert me["id"]


def test_get_by_id_still_works(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    conn = sqlite3.connect(str(settings.db_path))
    job_id = enqueue_job(conn, user_id=me["id"], type_="proxy", target_id="ast_x")
    conn.commit()
    conn.close()
    r = client.get(f"/api/v1/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == job_id and "label" not in body
```

- [ ] **Step 2: Запустить — FAIL**

Run: `uv run python -m pytest tests/test_jobs_api.py -v`

Expected: FAIL (404/405 на `GET /api/v1/jobs`).

- [ ] **Step 3: Ручка**

В `server/app/renders/routes.py` **выше** `@router.get("/jobs/{job_id}")` добавить модель и обработчик. `now` в выборку передавать `utcnow()`, не `Date.now` в SQL.

```python
from datetime import datetime

from server.app.jobs import list_jobs_for_user
from server.app.util import utcnow


class JobListItem(BaseModel):
    id: str
    type: str
    status: str
    progress: float
    error: str | None
    created_at: str
    finished_at: str | None
    label: str
    cancelable: bool
    quality: str | None


class JobList(BaseModel):
    jobs: list[JobListItem]


@router.get("/jobs", response_model=JobList)
def list_jobs(
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> JobList:
    rows = list_jobs_for_user(conn, user.id, now=utcnow())
    return JobList(jobs=[JobListItem(**row) for row in rows])
```

Импорт `utcnow` не должен сломать существующие импорты файла. `GET /jobs/{job_id}` оставить без `label`.

- [ ] **Step 4: Тесты и линт**

Run: `uv run python -m pytest tests/test_jobs_api.py tests/test_renders_api.py -v`

Run: `uv run ruff check server/app/jobs.py server/app/renders/routes.py tests/test_jobs.py tests/test_jobs_api.py`

Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add server/app/renders/routes.py tests/test_jobs_api.py
git commit -m "feat(jobs): GET /api/v1/jobs отдаёт список своих заданий"
```

---

### Task 3: Подписи и склейка строк (чистые функции)

**Files:**
- Create: `web/src/work.ts`
- Create: `web/src/work.test.ts`
- Modify: `web/src/project.ts`

- [ ] **Step 1: Падающие vitest**

`web/src/work.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { FLASH_MS, foldIncoming, jobTitle, workRows, type WorkJob, type WorkState } from './work'

const job = (over: Partial<WorkJob> = {}): WorkJob => ({
  id: 'job_1',
  type: 'analyze',
  status: 'running',
  progress: 0.4,
  error: null,
  label: 'Нарезка.mp4',
  cancelable: false,
  quality: null,
  ...over,
})

const empty = (): WorkState => ({
  jobs: [],
  uploads: [],
  dismissed: [],
  heldFailed: [],
  flashes: [],
})

describe('подписи', () => {
  it('ставит кавычки в действии', () => {
    expect(jobTitle(job())).toBe('Анализ «Нарезка.mp4»')
    expect(jobTitle(job({ type: 'proxy' }))).toBe('Прокси «Нарезка.mp4»')
    expect(jobTitle(job({ type: 'transcribe' }))).toBe('Расшифровка «Нарезка.mp4»')
    expect(jobTitle(job({ type: 'render', quality: 'draft', label: 'Ролик' }))).toBe('Сборка черновика «Ролик»')
    expect(jobTitle(job({ type: 'render', quality: 'final', label: 'Ролик' }))).toBe('Сборка финала «Ролик»')
  })
})

describe('склейка', () => {
  it('показывает загрузку и живое задание разными строками', () => {
    const state: WorkState = {
      ...empty(),
      uploads: [{ id: 'up_1', name: 'подкаст.mp4', done: 7, total: 10 }],
      jobs: [job()],
    }
    const rows = workRows(state, 0)
    expect(rows.map(r => r.key)).toEqual(['up:up_1', 'job_1'])
    expect(rows[0]?.title).toBe('Загрузка «подкаст.mp4»')
    expect(rows[0]?.percent).toBe(70)
    expect(rows[0]?.cancelable).toBe(true)
    expect(rows[1]?.percent).toBe(40)
    expect(rows[1]?.cancelable).toBe(false)
  })

  it('успех мелькает и пропадает', () => {
    const prev = { ...empty(), jobs: [job({ status: 'running' })] }
    const now = 1000
    const next = foldIncoming(prev, [job({ status: 'done', progress: 1 })], now)
    const during = workRows(next, now)
    expect(during.some(r => r.title.includes('готово'))).toBe(true)
    const after = workRows(next, now + FLASH_MS + 1)
    expect(after.some(r => r.key === 'job_1')).toBe(false)
  })

  it('сбой держится после того, как сервер его перестал отдавать', () => {
    const failed = job({ status: 'failed', error: 'нет места на диске' })
    const afterFail = foldIncoming(empty(), [failed], 0)
    const gone = foldIncoming(afterFail, [], 10)
    const rows = workRows(gone, 10)
    expect(rows).toHaveLength(1)
    expect(rows[0]?.error).toBe('нет места на диске')
    expect(rows[0]?.closeable).toBe(true)
    const closed = { ...gone, dismissed: ['job_1'] }
    expect(workRows(closed, 10)).toEqual([])
  })
})
```

- [ ] **Step 2: Запустить — FAIL**

Run: `cd web && npm test -- src/work.test.ts`

Expected: FAIL — модуля нет.

- [ ] **Step 3: Реализация**

`web/src/work.ts` — экспорт `FLASH_MS = 2000`, типы `WorkJob`, `UploadWork`, `WorkState`, `WorkRow`, функции `jobTitle`, `foldIncoming`, `workRows`.

Правила `foldIncoming(prev, incoming, now)`:

- `jobs` := `incoming`.
- Если задание перешло в `done` / `canceled` (или пропало из списка, будучи `queued`/`running`) — вспышка `{ id, kind, until: now + FLASH_MS, title: jobTitle(старое или новое) }`.
- Вспышки с `until <= now` выкинуть.
- `failed` из `incoming`, которых нет в `dismissed`, добавить в `heldFailed` (по id без дублей). Уже удерживаемые сбои, которых нет во `incoming` и нет в `dismissed`, оставить.

Правила `workRows`:

- Сначала загрузки: ключ `up:{id}`, процент `round(done/total*100)`, `cancelable: true`.
- Потом вспышки ещё живые по `now`.
- Потом `queued`/`running` из `jobs`.
- Потом `failed` из `jobs` ∪ `heldFailed`, кроме `dismissed`.
- `done`/`canceled` без вспышки не рисовать.

В `web/src/project.ts`:

```typescript
export type JobListItem = {
  id: string
  type: 'analyze' | 'proxy' | 'transcribe' | 'render'
  status: JobView['status']
  progress: number
  error: string | null
  created_at: string
  finished_at: string | null
  label: string
  cancelable: boolean
  quality: 'draft' | 'final' | null
}

export function listJobs(): Promise<{ jobs: JobListItem[] }> {
  return api('/api/v1/jobs')
}
```

- [ ] **Step 4: Тесты зелёные**

Run: `cd web && npm test -- src/work.test.ts src/project.test.ts`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/work.ts web/src/work.test.ts web/src/project.ts
git commit -m "feat(web): склейка загрузок и заданий в строки хода"
```

---

### Task 4: Обрыв загрузки

**Files:**
- Modify: `web/src/upload.ts`
- Modify: `web/src/upload.test.ts`

- [ ] **Step 1: Падающий тест**

В `web/src/upload.test.ts`:

```typescript
it('останавливается, если сигнал оборвали', async () => {
  const ac = new AbortController()
  const request = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    if (path === '/api/v1/uploads' && init.method === 'POST') {
      ac.abort()
      return { upload_id: 'upl_1', chunk_size: 4, total_chunks: 3, expires_at: 'x' } as T
    }
    throw new Error('не должны слать части после abort')
  }
  await expect(uploadFile(fakeFile(10), { request, sleep: noSleep, signal: ac.signal })).rejects.toMatchObject({
    name: 'UploadAborted',
  })
})
```

- [ ] **Step 2: Запустить — FAIL**

Run: `cd web && npm test -- src/upload.test.ts`

Expected: FAIL — нет `signal` / `UploadAborted`.

- [ ] **Step 3: Минимальная реализация**

В `UploadOptions` добавить `signal?: AbortSignal`.

```typescript
export class UploadAborted extends Error {
  constructor() {
    super('Загрузка отменена')
    this.name = 'UploadAborted'
  }
}
```

В `withRetry` и в цикле `worker` внутри `uploadFile`: если `opts.signal?.aborted` — `throw new UploadAborted()`. `UploadAborted` не ретраить. В `request(...)` передавать `{ ...init, signal: opts.signal }`.

- [ ] **Step 4: Тесты зелёные**

Run: `cd web && npm test -- src/upload.test.ts`

Expected: PASS, прежние тесты загрузки тоже.

- [ ] **Step 5: Commit**

```bash
git add web/src/upload.ts web/src/upload.test.ts
git commit -m "feat(web): загрузку можно оборвать сигналом"
```

---

### Task 5: Список в оболочке

**Files:**
- Modify: `web/src/shell.ts`
- Modify: `web/src/main.ts`
- Modify: `web/src/style.css`

- [ ] **Step 1: Разметка оболочки**

Между `<header>` и `#shell-screen` в `mountShell`:

```html
<div id="shell-work" hidden></div>
```

Тип `Shell` расширить:

```typescript
work: {
  trackUpload: (name: string) => {
    id: string
    setProgress: (done: number, total: number) => void
    succeed: () => void
    fail: (message: string) => void
    abort: () => void
  }
  start: () => void
  stop: () => void
}
```

- [ ] **Step 2: Опрос и рисунок**

Новый модуль рисования можно держать в `work.ts` функцией `mountWork(el: HTMLElement)` (возвращает `start`/`stop`/`trackUpload`), а `shell.ts` только вставляет узел. Не монтировать это внутри `mountFiles` / `mountEditor`.

Поведение:

- `start`: опрос `listJobs()` каждые `POLL_MS` из `web/src/assets.ts` (3000). Срыв запроса: список не чистить, на контейнере показать тихий `.meta` «ход мог устареть».
- `stop`: снять таймер, `innerHTML = ''`, `hidden = true`.
- После `foldIncoming` вызвать `workRows` и нарисовать. Пустой массив — `hidden = true`.
- Строка: заголовок, при `bar` — `<div class="progress"><i style="width:N%"></i></div>`, «Отменить» если `cancelable`, «Закрыть» если `closeable`, текст `error`.
- Отмена задания: `cancelJob(id)` из `project.ts`. Ошибка API — написать в эту строку, не удалять.
- Отмена загрузки: `abort()` трекера (см. Task 6) — строка сразу пропадает (`uploads` без этого id).
- «Закрыть»: добавить id в `dismissed`.
- `setUser` вызывает `work.start()`, `clearUser` — `work.stop()`.

CSS в `web/src/style.css` рядом с `.progress`:

```css
#shell-work {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px 0;
}
#shell-work[hidden] { display: none; }
.work-row {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
}
```

- [ ] **Step 3: Собрать фронт**

Run: `cd web && npm test && npm run build`

Expected: PASS, `tsc` без ошибок.

- [ ] **Step 4: Commit**

```bash
git add web/src/shell.ts web/src/main.ts web/src/work.ts web/src/style.css
git commit -m "feat(web): список хода живёт в шапке, а не на экране"
```

---

### Task 6: Экран записей отдаёт загрузку в оболочку

**Files:**
- Modify: `web/src/files.ts`
- Modify: `web/src/main.ts`

- [ ] **Step 1: Убрать локальные полоски**

Удалить `#f-progress` из разметки `mountFiles` и весь `take()`-код, который кладёт `.upload-line` в этот узел.

Сигнатура:

```typescript
export function mountFiles(
  el: HTMLElement,
  onChanged?: () => void,
  work?: Shell['work'],
)
```

В `take(files)` для каждого файла:

```typescript
const handle = work?.trackUpload(file.name)
try {
  await uploadFile(file, {
    onProgress: (d, t) => handle?.setProgress(d, t),
    signal: handle?.signal,
  })
  handle?.succeed()
} catch (e) {
  if (e instanceof UploadAborted) {
    handle?.abort() // строка уже снята кнопкой; повторный abort безвредный
    continue
  }
  handle?.fail(e instanceof Error ? e.message : String(e))
  showError(e)
  continue
}
```

`trackUpload` должен вернуть ещё `signal: AbortSignal` (тот же `AbortController`, что дергает «Отменить»).

На карточке записи `note` — только `a.error`, без `progressText(...)`. Импорт `progressText` убрать.

В `main.ts`: `mountFiles(shell.screen, refreshQuota, shell.work)`.

- [ ] **Step 2: Сборка**

Run: `cd web && npm test && npm run build`

Expected: PASS. `progressText` остаётся в `player.ts` и его тесте — карточке больше не нужен.

- [ ] **Step 3: Commit**

```bash
git add web/src/files.ts web/src/main.ts web/src/shell.ts web/src/work.ts
git commit -m "feat(web): загрузка пишет ход в шапку, не под зону сброса"
```

---

### Task 7: Убрать полосы во вкладках Субтитры и Рендер

**Files:**
- Modify: `web/src/subtitles.ts`
- Modify: `web/src/render.ts`

- [ ] **Step 1: Субтитры**

В `draw()`, ветка `if (jobId)`:

```typescript
el.innerHTML = shell(`<p class="lead" style="margin:0">Расшифровываю — ход вверху. Можно уйти
  на другую вкладку, работа не прервётся</p>`)
```

Удалить `#sub-bar` и присвоение `bar.style.width` в `tick()`. Опрос `loadJob` оставить: иначе панель не узнает, что расшифровка доехала.

- [ ] **Step 2: Рендер**

В разметке `#rnd-job` удалить `<div class="progress"><i id="rnd-bar"...`. Кнопку «Отменить сборку» и `#rnd-status` оставить.

В `showJob` не трогать ширину полосы. Текст статуса без процента, когда идёт работа: `Собираю — ход вверху` (для `running`/`queued`). `failed` / `canceled` / `done` — как сейчас словами (`не собралось` / `отменено` / `готово`).

Удалить неиспользуемые `percent` / `bar`, если после этого никто не ссылается.

- [ ] **Step 3: Сборка**

Run: `cd web && npm test && npm run build`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web/src/subtitles.ts web/src/render.ts
git commit -m "fix(web): ход расшифровки и сборки только в шапке"
```

---

### Task 8: README и живая проверка

**Files:**
- Modify: `README.md` (блок «Рендер (M3)» ~строка 76 и «В редакторе вкладка Рендер» ~строка 82)

- [ ] **Step 1: Документация**

После предложения про `GET /api/v1/jobs/{job_id}` добавить:

`GET /api/v1/jobs` — список своих заданий в `queued`/`running` и закончившихся за последние 30 с; у строки есть `label`, `cancelable`, у сборки `quality`.

Строку про вкладку «Рендер» заменить: две кнопки, отмена на панели, ход всех заданий (и загрузок) — список под шапкой сайта.

- [ ] **Step 2: Полный прогон тестов**

Run: `uv run python -m pytest`

Run: `uv run ruff check .`

Run: `cd web && npm test && npm run build`

Expected: всё зелёное.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: список хода в шапке и GET /api/v1/jobs"
```

- [ ] **Step 4: Живая проверка на VM** (после выкатки, не в этом коммите)

Чеклист из спеки §6: загрузить на «Записях» — полоса в шапке; уйти на главную — полоса на месте; анализ сменяется прокси; в редакторе расшифровка и черновик видны без открытой вкладки; отмена сборки из строки; сбой остаётся до «Закрыть»; на «Записях» нет полос под зоной сброса; во вкладках нет `.progress` у задания.

---

## Самопроверка плана против спеки

| Спека | Задача |
|---|---|
| Список в оболочке, все экраны после входа | Task 5 |
| Загрузки + `GET /jobs` | Task 2, 3, 6 |
| Несколько строк | Task 3 |
| Успех ~2 с, отмена вспышкой | Task 3 |
| Сбой до «Закрыть», в т.ч. после исчезновения с сервера | Task 3 |
| Отмена загрузки / transcribe / render, не analyze/proxy | Task 1 `cancelable`, Task 4–5 |
| Нет полос на Записях / Субтитрах / Рендере | Task 6–7 |
| Пилюля без повторного процента | Task 6 |
| `GET /jobs/{id}` без изменений | Task 2 тест |
| Нет кнопки сброса Whisper | нигде не добавляем |
| Индекс | Task 1 |
| README | Task 8 |
