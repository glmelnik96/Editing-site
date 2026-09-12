# Место на диске по файлам — план

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Шапка у всех показывает место на сервере («1.1 ГБ · свободно 87.0 ГБ»), личный лимит и «Кабинет доступа» считают место по настоящим файлам на диске.

**Architecture:** Подсчёт — обход каталогов (`storage.tree_bytes`) без кэша. Личное место — папка человека плюс недокачанные загрузки (`uploads.store.used_bytes`); место сервиса — каталог данных плюс отдельный временный каталог (`storage.server_space`). `/me` отдаёт `server`, новый `GET /api/v1/admin/usage` — место по людям. Спека: `docs/superpowers/specs/2026-09-12-disk-space-design.md`.

**Tech Stack:** FastAPI + SQLite, pytest (`uv run python -m pytest` из корня репозитория); клиент vanilla TS + vitest без jsdom (`npx vitest run`, `npx tsc --noEmit` в `web/`).

**Правило репозитория:** коммитить только после явной отмашки владельца — шаги «Commit» ниже выполняются одним заходом в конце, когда она получена.

---

## Файлы

| Файл | Что меняется |
|---|---|
| `server/app/storage.py` | + `user_dir`, `tree_bytes`, `server_space` |
| `server/app/uploads/store.py` | `used_bytes(conn, settings, user_id)` по файлам; + `require_quota`; `check_capacity` и перепроверка в `finalize_file` через неё |
| `server/app/auth/routes.py` | `/me`: + `server: {files_bytes, free_bytes}`, квота по файлам |
| `server/app/admin/routes.py` | + `GET /api/v1/admin/usage` |
| `tests/test_storage.py`, `tests/test_uploads_store.py`, `tests/test_assets_api.py`, `tests/test_admin_api.py` | тесты на файлах вместо строк базы |
| `web/src/shell.ts`, `web/src/shell.test.ts` | `Me.server`, шапка «X · свободно Y», подсказка со своим расходом |
| `web/src/overview.ts`, `web/src/overview.test.ts` | кабинет из `/admin/usage`, `diskByOwner` уходит |

---

### Task 1: Подсчёт файлов в `storage`

**Files:**
- Modify: `server/app/storage.py` (импорты; новые функции сразу после `_check_id`)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Падающие тесты** — в импорт `from server.app.storage import (...)` добавить `server_space`, `tree_bytes`, `user_dir`; в конец файла:

```python
def test_tree_bytes_sums_nested_files_and_ignores_missing(tmp_path):
    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    (root / "one.bin").write_bytes(b"x" * 10)
    (root / "a" / "two.bin").write_bytes(b"x" * 20)
    (root / "a" / "b" / "three.bin").write_bytes(b"x" * 30)
    assert tree_bytes(root) == 60
    assert tree_bytes(tmp_path / "missing") == 0


def test_user_dir_holds_assets_and_projects(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path)
    user = "usr_0123456789ab"
    assert asset_dir(s, user, "ast_0123456789ab").parent.parent == user_dir(s, user)
    assert render_dir(s, user, "prj_0123456789ab").parent.parent.parent == user_dir(s, user)
    with pytest.raises(ValueError):
        user_dir(s, "../etc")


def test_server_space_counts_data_and_separate_tmp_once(tmp_path):
    data, tmp = tmp_path / "data", tmp_path / "tmp"
    (data / "usr_0123456789ab").mkdir(parents=True)
    (data / "usr_0123456789ab" / "f.bin").write_bytes(b"x" * 100)
    (data / "video.db").write_bytes(b"x" * 7)
    tmp.mkdir()
    (tmp / "upload.part").write_bytes(b"x" * 50)
    files, free = server_space(Settings(_env_file=None, data_dir=data, tmp_dir=tmp))
    assert files == 157 and free > 0
    # Временный каталог внутри данных уже посчитан обходом данных — второй раз его не прибавляем.
    (data / "tmp").mkdir()
    (data / "tmp" / "upload.part").write_bytes(b"x" * 40)
    files, _ = server_space(Settings(_env_file=None, data_dir=data))
    assert files == 147
```

- [ ] **Step 2: Убедиться, что падают**

Run: `uv run python -m pytest tests/test_storage.py -q`
Expected: ошибка импорта `server_space` / `tree_bytes` / `user_dir`.

- [ ] **Step 3: Реализация** — в `storage.py` импорты `import os` и `import shutil` рядом с `import re`; после `_check_id`:

```python
def user_dir(settings: Settings, user_id: str) -> Path:
    """Папка человека: в ней его записи и проекты — всё, что он занимает на диске."""
    return settings.data_dir / _check_id(user_id)


def tree_bytes(path: Path) -> int:
    """Сколько занимают файлы под каталогом. Нет каталога — 0; файл, исчезнувший посреди обхода
    (его удалили или перенесли), просто не считается."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def server_space(settings: Settings) -> tuple[int, int]:
    """Файлы сервиса и свободное место на разделе с данными.

    Файлы сервиса — всё в каталоге данных (папки людей, база, её копии) и во временном каталоге,
    если он лежит отдельно: на проде там недокачанные загрузки. Внутри каталога данных временный
    каталог уже посчитан обходом — второй раз его не прибавляем.
    """
    data = settings.data_dir
    files = tree_bytes(data)
    tmp = settings.tmp_path
    if not tmp.resolve().is_relative_to(data.resolve()):
        files += tree_bytes(tmp)
    try:
        free = shutil.disk_usage(data).free
    except OSError:
        free = 0
    return files, free
```

- [ ] **Step 4: Прогнать** — `uv run python -m pytest tests/test_storage.py -q` → PASS.

---

### Task 2: Личный лимит по файлам

**Files:**
- Modify: `server/app/uploads/store.py` (`used_bytes`, `check_capacity`, новая `require_quota`, перепроверка в `finalize_file`)
- Test: `tests/test_uploads_store.py`

- [ ] **Step 1: Падающие тесты.** Импорт из `server.app.storage`: `asset_dir, render_dir`. Все вызовы `used_bytes(conn, USER)` → `used_bytes(conn, settings, USER)` (в `test_create_reserves_file_and_counts_quota` и в тесте завершения чанковой загрузки — `== 2048`). Тест `test_quota_does_not_count_conversions` заменить двумя:

```python
def test_quota_counts_every_file_in_the_persons_folder(conn, settings):
    """Лимит считает всё, что лежит в папке человека: исходник и то, что из него сделано, —
    прокси, конверсии, — и готовые ролики проектов. Место занимают они все."""
    src = settings.data_dir / "a.mp4"
    src.write_bytes(b"x" * 100)
    row = finalize_file(conn, settings, user_id=USER, src=src, filename="a.mp4", size=100, kind="video")
    folder = asset_dir(settings, USER, row["id"])
    (folder / "proxy.mp4").write_bytes(b"x" * 40)
    (folder / "conversions").mkdir()
    (folder / "conversions" / "a.mp3").write_bytes(b"x" * 25)
    renders = render_dir(settings, USER, "prj_000000000001")
    renders.mkdir(parents=True)
    (renders / "r.mp4").write_bytes(b"x" * 300)
    assert used_bytes(conn, settings, USER) == 465


def test_quota_ignores_database_rows_without_files(conn, settings):
    """Место занимают файлы, а не строки: запись в базе, чей файл уже стёрт, лимит не ест."""
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, created_at, "
        "last_access_at) VALUES ('ast_0000000000aa', ?, 'video', 'a.mp4', 'mp4', 9000, 'ready', ?, ?)",
        (USER, now_iso(), now_iso()),
    )
    assert used_bytes(conn, settings, USER) == 0
```

`test_finalize_rechecks_quota_and_restores_file` — вместо строки в базе настоящий файл, и записей в базе после отказа ноль:

```python
def test_finalize_rechecks_quota_and_restores_file(conn, settings, tmp_path):
    folder = asset_dir(settings, USER, "ast_0000000000aa")
    folder.mkdir(parents=True)
    (folder / "source.mp4").write_bytes(b"x" * 9000)
    src = tmp_path / "b.mp4"
    src.write_bytes(b"y" * 2000)
    with pytest.raises(UploadError) as e:
        finalize_file(
            conn, settings, user_id=USER, src=src, filename="b.mp4", size=2000, kind="video",
            check_quota=True,
        )
    assert e.value.code == "quota_exceeded"
    assert src.read_bytes() == b"y" * 2000
    assert conn.execute("SELECT count(*) FROM assets").fetchone()[0] == 0


def test_finalize_recheck_does_not_count_the_new_file_twice(conn, settings, tmp_path):
    """Перепроверка идёт, когда файл уже в папке записи: прибавь его размер ещё раз — и загрузка,
    которая помещается в лимит ровно, получила бы отказ."""
    folder = asset_dir(settings, USER, "ast_0000000000aa")
    folder.mkdir(parents=True)
    (folder / "source.mp4").write_bytes(b"x" * 6000)
    src = tmp_path / "b.mp4"
    src.write_bytes(b"y" * 4000)
    row = finalize_file(
        conn, settings, user_id=USER, src=src, filename="b.mp4", size=4000, kind="video",
        check_quota=True,
    )
    assert row["size"] == 4000
    assert used_bytes(conn, settings, USER) == 10_000
```

- [ ] **Step 2: Убедиться, что падают** — `uv run python -m pytest tests/test_uploads_store.py -q` → TypeError на `used_bytes(conn, settings, USER)`.

- [ ] **Step 3: Реализация.** Импорт из `server.app.storage` дополнить `tree_bytes, user_dir`. Заменить `used_bytes` и начало `check_capacity`:

```python
def used_bytes(conn: sqlite3.Connection, settings: Settings, user_id: str) -> int:
    """Сколько места занимает человек: все файлы его папки — записи со всем, что из них сделано,
    и проекты с готовыми роликами, — плюс незавершённые загрузки: их файл уже зарезервирован во
    временном каталоге целиком."""
    uploads = conn.execute(
        "SELECT coalesce(sum(size), 0) FROM uploads WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    return tree_bytes(user_dir(settings, user_id)) + int(uploads)


def require_quota(conn: sqlite3.Connection, settings: Settings, user_id: str, extra: int) -> None:
    """Поместятся ли ещё extra байт в личный лимит. Файл, который уже лежит в папке человека,
    посчитан обходом — для него extra = 0."""
    used = used_bytes(conn, settings, user_id)
    if used + extra > settings.user_quota_bytes:
        details = {"used_bytes": used, "limit_bytes": settings.user_quota_bytes}
        raise UploadError(413, "quota_exceeded", "Квота исчерпана", details)
```

В `check_capacity` три строки подсчёта `used` и `if used + size > ...` заменить на `require_quota(conn, settings, user_id, size)`. В `finalize_file`:

```python
            if check_quota:
                # Файл уже перенесён в папку записи и посчитан обходом — второй раз его не прибавляем.
                require_quota(conn, settings, user_id, 0)
```

- [ ] **Step 4: Прогнать** — `uv run python -m pytest tests/test_uploads_store.py tests/test_uploads_api.py -q` → PASS (вызов из `/me` чинится в Task 3; до неё API-тесты `/me` могут падать — это ожидаемо).

---

### Task 3: `/me` — место на сервере и квота по файлам

**Files:**
- Modify: `server/app/auth/routes.py` (импорт `server_space`; модели `ServerSpace`, `MeView`; `me()`)
- Test: `tests/test_assets_api.py`

- [ ] **Step 1: Падающие тесты.** В `test_small_upload_list_get_delete` проверку квоты заменить: у субтитра рядом с исходником лежит `subs.vtt`, и квота равна всей папке записи:

```python
    used = sum(f.stat().st_size for f in source.parent.iterdir())  # source.srt и subs.vtt
    assert client.get("/api/v1/me").json()["quota"] == {"used_bytes": used, "limit_bytes": 10 * 1024 * 1024}
```

и добавить тест:

```python
def test_me_reports_server_space(client, login_as, settings):
    login_as()
    folder = settings.data_dir / "usr_0123456789ab"
    folder.mkdir(parents=True)
    (folder / "x.bin").write_bytes(b"x" * 1000)
    server = client.get("/api/v1/me").json()["server"]
    assert server["files_bytes"] >= 1000 and server["free_bytes"] > 0
```

- [ ] **Step 2: Убедиться, что падают** — `uv run python -m pytest tests/test_assets_api.py -q` → KeyError `server` / TypeError в `used_bytes`.

- [ ] **Step 3: Реализация** — импорт `from server.app.storage import known_exts, server_space`;

```python
class ServerSpace(BaseModel):
    files_bytes: int
    free_bytes: int


class MeView(CurrentUser):
    quota: Quota
    server: ServerSpace
```

тело `me()` после `Cache-Control`:

```python
    settings = request.app.state.settings
    files, free = server_space(settings)
    return MeView(
        **user.model_dump(),
        quota=Quota(used_bytes=used_bytes(conn, settings, user.id), limit_bytes=settings.user_quota_bytes),
        server=ServerSpace(files_bytes=files, free_bytes=free),
    )
```

- [ ] **Step 4: Прогнать** — `uv run python -m pytest tests/test_assets_api.py tests/test_uploads_api.py tests/test_uploads_store.py -q` → PASS.

---

### Task 4: `GET /api/v1/admin/usage`

**Files:**
- Modify: `server/app/admin/routes.py` (импорт `used_bytes`; модели `PersonUse`, `UsageList`; маршрут перед `/stats`)
- Test: `tests/test_admin_api.py`

- [ ] **Step 1: Падающий тест** (после `test_these_lists_are_for_the_admin_only`):

```python
def test_admin_sees_who_takes_how_much_disk(login_as, settings):
    """Место по людям — по файлам на диске: исходник, прокси, ролики. Строки базы тут ни при чём:
    у записи в базе 1234 байта, а на диске её исходник занимает один."""
    admin = login_as("admin@ya.ru")
    admin.post("/api/v1/admin/whitelist", json={"email": "user@ya.ru"})
    user = login_as("user@ya.ru", "Пользователь")
    me = user.get("/api/v1/me").json()
    asset = _seed_asset(settings, me["id"])
    (settings.data_dir / me["id"] / "assets" / asset / "proxy.mp4").write_bytes(b"x" * 99)
    renders = settings.data_dir / me["id"] / "projects" / "prj_000000000001" / "renders"
    renders.mkdir(parents=True)
    (renders / "r.mp4").write_bytes(b"x" * 900)

    admin = login_as("admin@ya.ru")
    people = admin.get("/api/v1/admin/usage").json()["people"]
    assert people == [{"email": "user@ya.ru", "name": "Пользователь", "bytes": 1000, "records": 1}]
    assert login_as("user@ya.ru", "Пользователь").get("/api/v1/admin/usage").status_code == 403
```

- [ ] **Step 2: Убедиться, что падает** — `uv run python -m pytest tests/test_admin_api.py -q -k usage` → 404.

- [ ] **Step 3: Реализация** — импорт `from server.app.uploads.store import used_bytes`; модели рядом с `Stats`:

```python
class PersonUse(BaseModel):
    email: str
    name: str
    bytes: int
    records: int


class UsageList(BaseModel):
    people: list[PersonUse]
```

маршрут перед `/stats`:

```python
@router.get("/usage", response_model=UsageList)
def usage(
    request: Request,
    _: CurrentUser = Depends(require_admin),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> UsageList:
    """Кто сколько занимает на диске — по файлам в папке человека, так же, как считает его лимит.
    Тяжёлые сверху: место кончается из-за них; у кого на диске ничего нет, в списке не стоит."""
    settings = request.app.state.settings
    records = {r[0]: r[1] for r in conn.execute("SELECT user_id, count(*) FROM assets GROUP BY user_id")}
    people = []
    for row in conn.execute("SELECT id, email, name FROM users").fetchall():
        taken = used_bytes(conn, settings, row["id"])
        if taken:
            people.append(PersonUse(
                email=row["email"], name=row["name"], bytes=taken, records=records.get(row["id"], 0),
            ))
    people.sort(key=lambda p: (-p.bytes, p.email))
    return UsageList(people=people)
```

- [ ] **Step 4: Прогнать** — `uv run python -m pytest tests/test_admin_api.py -q` → PASS.

- [ ] **Step 5: Весь сервер** — `uv run python -m pytest -q` → всё зелёное, как до правок.

---

### Task 5: Шапка — место на сервере

**Files:**
- Modify: `web/src/shell.ts` (`Me.server`, `navHtml`)
- Test: `web/src/shell.test.ts`

- [ ] **Step 1: Падающие тесты** — в заготовке `me()` квота `{ used_bytes: 1_073_741_824, limit_bytes: 21_474_836_480 }` и `server: { files_bytes: 1_181_116_006, free_bytes: 93_415_538_688 }`; два теста:

```ts
  it('место — на всём сервере: файлы сервиса и сколько свободно', () => {
    expect(navHtml(me('user'))).toContain('1.1 ГБ · свободно 87.0 ГБ')
  })

  it('в подсказке — почта и свой расход с лимитом', () => {
    expect(navHtml(me('user'))).toContain('title="someone@example.com&#10;Ваши файлы: 1.0 ГБ из 20.0 ГБ"')
  })
```

- [ ] **Step 2: Убедиться, что падают** — `npx vitest run src/shell.test.ts` (в `web/`) → FAIL, в шапке «1.0 ГБ из 20.0 ГБ».

- [ ] **Step 3: Реализация** — в `Me`:

```ts
  /** Место на сервере — одно на всех: файлы сервиса на диске и сколько ещё свободно. */
  server: { files_bytes: number; free_bytes: number }
```

в `navHtml` строка места:

```ts
  const tip = `${escapeHtml(me.email)}&#10;Ваши файлы: ${fmtSize(me.quota.used_bytes)} из ${fmtSize(me.quota.limit_bytes)}`
  ...
      <div class="meta" title="${tip}">${fmtSize(me.server.files_bytes)} · свободно ${fmtSize(me.server.free_bytes)}</div>
```

- [ ] **Step 4: Прогнать** — `npx vitest run src/shell.test.ts` → PASS.

---

### Task 6: Кабинет — место по людям с сервера

**Files:**
- Modify: `web/src/overview.ts` (−`OwnerUse`, −`diskByOwner`; +`PersonUse`, `loadUsage`, `usageHtml`; `mountOverview`)
- Test: `web/src/overview.test.ts`

- [ ] **Step 1: Падающие тесты** — импорт `diskByOwner` → `usageHtml, type PersonUse`; блок `describe('итог по людям', …)` заменить:

```ts
describe('место по людям', () => {
  it('имя или почта, место и число записей', () => {
    const rows: PersonUse[] = [
      { email: 'two@ya.ru', name: 'Второй', bytes: 1_073_741_824, records: 3 },
      { email: 'one@ya.ru', name: '', bytes: 1024, records: 0 },
    ]
    const html = usageHtml(rows)
    expect(html).toContain('Второй')
    expect(html).toContain('1.0 ГБ · записей: 3')
    expect(html).toContain('one@ya.ru')
    expect(html).toContain('1.0 КБ · записей: 0')
  })

  it('пустой список так и говорит, а не рисует нули', () => {
    expect(usageHtml([])).toContain('На диске пока ничего нет')
  })
})
```

- [ ] **Step 2: Убедиться, что падают** — `npx vitest run src/overview.test.ts` → `usageHtml is not a function`.

- [ ] **Step 3: Реализация** — вместо `OwnerUse` и `diskByOwner`:

```ts
/** Место человека на диске — по файлам в его папке, как считает его лимит (GET /admin/usage). */
export type PersonUse = { email: string; name: string; bytes: number; records: number }

export function loadUsage(): Promise<PersonUse[]> {
  return api<{ people: PersonUse[] }>('/api/v1/admin/usage').then(body => body.people)
}

/** Строки кабинета. Порядок — тяжёлые сверху — уже задал сервер. */
export function usageHtml(rows: PersonUse[]): string {
  if (!rows.length) return '<span class="muted">На диске пока ничего нет</span>'
  return rows
    .map(
      row =>
        `<div class="row" style="margin:0;justify-content:space-between">
          <span>${escapeHtml(row.name.trim() || row.email)}</span>
          <span class="meta">${fmtSize(row.bytes)} · записей: ${row.records}</span>
        </div>`,
    )
    .join('')
}
```

В `mountOverview` убрать внутреннюю `useHtml`, загрузку заменить на
`void loadUsage().then(rows => { if (!stopped) useBox.innerHTML = usageHtml(rows) })` с прежним `.catch`.

- [ ] **Step 4: Прогнать** — `npx vitest run` и `npx tsc --noEmit` (в `web/`) → всё зелёное.

---

### Task 7: Проверка на стенде и коммит

- [ ] **Step 1:** `npm run build` в `web/`, стенд на 8010 с данными `scratchpad/stand/data`, вход через `stand-shot.html`; снимки главной и «Кабинета доступа» (`cdp_pages.mjs`, `MSYS_NO_PATHCONV=1`). В шапке — «X · свободно Y», X сверить с обходом `stand/data`; в кабинете — место по людям.
- [ ] **Step 2:** уборка стенда (процессы по командной строке, файлы входа из `web/dist`).
- [ ] **Step 3 (после отмашки владельца):** коммиты `docs:` (спека и план) и `feat:` (сервер, клиент, тесты), пуш, выкатка обычным порядком; на проде сверить `/me.server` с замером `prod_disk_check.py`.
