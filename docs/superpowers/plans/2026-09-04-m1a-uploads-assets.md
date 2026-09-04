# M1a: загрузка по частям, ассеты, раздача файлов, квоты, janitor

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** пользователь и агент могут залить файл до 5 ГБ по частям с докачкой, увидеть его в списке ассетов, удалить, получить ссылки на производные файлы; сервер держит квоты и чистит диск по срокам. Анализ, прокси и воркер идут отдельным планом M1b.

**Architecture:** новые модули `server/app/storage.py` (раскладка путей), `server/app/uploads/` (протокол частей), `server/app/assets/` (карточки, мелкая загрузка), `server/app/files.py` (раздача файлов и `/internal/authz` для Caddy `forward_auth`), `server/app/jobs.py` (постановка заданий в таблицу `jobs`), `server/janitor/` (правила очистки и бэкап), миграция `0004_assets.sql`. Файл загрузки живёт в `tmp/uploads/{upload_id}` и одним `os.replace` переезжает в `data/{user_id}/assets/{asset_id}/source.<ext>`. Во всех обработчиках сначала запись в базе, потом файлы; сироты подбирает janitor.

**Tech Stack:** FastAPI 0.141 / Starlette 1.6 (`FileResponse` с Range), SQLite (stdlib), Vite 5 + TypeScript, Caddy `forward_auth` + `file_server`, systemd timer.

**Спека:** `docs/superpowers/specs/2026-09-03-video-editor-design.md`, разделы 3, 5, 6, 11, 12, 14. **Предыдущий план:** `docs/superpowers/plans/2026-09-03-m0-skeleton-auth.md` (там же «Поправки по ходу выполнения» с правилами: применённые миграции не редактируются; `uv run python -m pytest`; коммиты после отчёта исполнителя).

---

## Решения M1a (что уточнено относительно спеки)

| Вопрос | Решение | Почему |
|---|---|---|
| Запись части | `open(path, "r+b")`, `seek`, `write` кусками по 4 МиБ через `run_in_threadpool` | `os.pwrite` нет на Windows, где идёт разработка; память на запрос не больше 4 МиБ |
| Резерв файла | `os.posix_fallocate` там, где есть, иначе `ftruncate` | На VM ошибка «нет места» приходит при создании загрузки, а не на последней части |
| `tmp` каталог | Настройка `VIDEO_TMP_DIR`, по умолчанию `data_dir/tmp`; на VM `/srv/video/tmp` | Тот же раздел диска, что и `data`, иначе `os.replace` падает с EXDEV |
| Столбец `assets.ext` | Добавлен (в спеке нет) | Чтобы находить `source.<ext>` без обращения к диску |
| Столбцы `jobs.attempts`, `jobs.heartbeat_at` | Добавлены | Janitor возвращает зависшее задание в очередь один раз и помечает `failed` на второй |
| Субтитры (`srt`, `vtt`) | Ассет сразу `ready`, без задания `analyze` | Анализировать нечего; конвертация SRT → VTT появится в M2 вместе с плеером |
| Маршрут `/files/...` | Есть и в приложении (FileResponse), и в Caddy (`forward_auth` + `file_server`) | Локально и в тестах Caddy нет; на VM большие файлы идут мимо Python. Правила доступа одни: функция `authorize_file` |
| Удаление ассета | Без проверки «стоит в проекте» | Проектов ещё нет, проверка появится в M2 вместе с таблицей `projects` |
| Квота | `sum(assets.size) + sum(uploads.size)` владельца ≤ 20 ГБ | Незавершённые загрузки тоже занимают диск |
| Бэкап базы | Раз в сутки из того же janitor через `sqlite3.Connection.backup`, хранится 7 копий в `data/backups/` | Отдельный таймер не нужен; копия вне VM остаётся ручной операцией (scp) |
| Просроченные сессии | Janitor удаляет (пункт из бэклога M0) | Таблица не должна расти вечно |

## Структура файлов

| Файл | Обязанность |
|---|---|
| `server/app/config.py` | + настройки хранения, квот и сроков; свойства `tmp_path`, `uploads_tmp_path` |
| `server/app/storage.py` | Пути на диске из идентификаторов, расширение и вид файла, публичные имена файлов, разбор `/files/...` |
| `server/db/migrations/0004_assets.sql` | Таблицы `uploads`, `upload_chunks`, `assets`, `jobs` |
| `server/app/jobs.py` | `enqueue_job`, `cancel_jobs_for_target` |
| `server/app/uploads/store.py` | Квота и диск, создание загрузки, запись части, полнота, завершение (`finalize_file`) |
| `server/app/uploads/routes.py` | `/api/v1/uploads` |
| `server/app/assets/views.py` | Карточка ассета со ссылками |
| `server/app/assets/routes.py` | `/api/v1/assets`, `POST /assets/upload` |
| `server/app/auth/routes.py` | `/me` с квотой |
| `server/app/files.py` | `GET /files/...`, `GET /internal/authz`, `authorize_file` |
| `server/janitor/rules.py`, `server/janitor/__main__.py` | Правила очистки, бэкап, точка входа `python -m server.janitor` |
| `deploy/Caddyfile`, `deploy/video-janitor.service`, `deploy/video-janitor.timer`, `deploy/deploy.sh`, `deploy/bootstrap.sh` | Маршруты файлов и лимиты тела, таймер janitor, группа `video` для Caddy |
| `web/src/upload.ts`, `web/src/assets.ts`, `web/src/main.ts` | Загрузка по частям с докачкой, панель ассетов |
| `tests/test_storage.py`, `tests/test_uploads_api.py`, `tests/test_assets_api.py`, `tests/test_files.py`, `tests/test_janitor.py`, `tests/test_deploy_files.py`, `tests/test_db_migrate.py` | Тесты |

Команды: `uv run python -m pytest` (не `uv run pytest`: минута на старт), `uv run ruff check .`, `cd web && npm test && npm run build`. Ветка: `m1a-uploads-assets` от `main`.

---

### Task 1: Настройки хранения и модуль раскладки путей

**Files:**
- Modify: `server/app/config.py`
- Create: `server/app/storage.py`
- Modify: `.env.example`
- Test: `tests/test_storage.py`, `tests/test_config.py`

- [ ] **Step 1: Тест настроек**

Добавить в `tests/test_config.py`:

```python
def test_storage_defaults_and_tmp_path(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "d")
    assert s.chunk_size == 32 * 1024 * 1024
    assert s.user_quota_bytes == 20 * 1024**3
    assert s.max_upload_bytes == 5 * 1024**3
    assert s.small_upload_max_bytes == 64 * 1024 * 1024
    assert s.disk_low_pct == 10.0
    assert s.uploads_per_hour == 20
    assert s.upload_ttl_hours == 24 and s.asset_ttl_hours == 24
    assert s.tmp_path == tmp_path / "d" / "tmp"
    assert s.uploads_tmp_path == tmp_path / "d" / "tmp" / "uploads"


def test_tmp_dir_override(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "d", tmp_dir=tmp_path / "t")
    assert s.uploads_tmp_path == tmp_path / "t" / "uploads"


def test_chunk_size_bounds():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, chunk_size=512)
```

(`pytest` и `ValidationError` из `pydantic` уже импортированы в этом файле; если нет, добавить импорты.)

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_config.py`
Expected: FAIL, `chunk_size` не существует.

- [ ] **Step 3: Настройки**

В `server/app/config.py` после `log_level: str = "INFO"` добавить поля:

```python
    # Хранение файлов. tmp_dir обязан быть на том же разделе, что data_dir: завершение загрузки делает os.replace.
    tmp_dir: Path | None = None
    chunk_size: int = Field(default=32 * 1024 * 1024, ge=1024, le=256 * 1024 * 1024)
    max_upload_bytes: int = Field(default=5 * 1024**3, ge=1)
    small_upload_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    user_quota_bytes: int = Field(default=20 * 1024**3, ge=1)
    disk_low_pct: float = Field(default=10.0, ge=0.0, le=90.0)
    uploads_per_hour: int = Field(default=20, ge=1)
    upload_ttl_hours: int = Field(default=24, ge=1)
    asset_ttl_hours: int = Field(default=24, ge=1)
```

и свойства после `allowed_origin`:

```python
    @property
    def tmp_path(self) -> Path:
        return self.tmp_dir if self.tmp_dir is not None else self.data_dir / "tmp"

    @property
    def uploads_tmp_path(self) -> Path:
        return self.tmp_path / "uploads"
```

В `.env.example` перед `VIDEO_LOG_LEVEL` добавить:

```
# Хранение. VIDEO_TMP_DIR на том же разделе, что VIDEO_DATA_DIR (завершение загрузки переименовывает файл).
# На VM: VIDEO_TMP_DIR=/srv/video/tmp
# VIDEO_TMP_DIR=
VIDEO_CHUNK_SIZE=33554432
VIDEO_MAX_UPLOAD_BYTES=5368709120
VIDEO_SMALL_UPLOAD_MAX_BYTES=67108864
VIDEO_USER_QUOTA_BYTES=21474836480
VIDEO_DISK_LOW_PCT=10
VIDEO_UPLOADS_PER_HOUR=20
VIDEO_UPLOAD_TTL_HOURS=24
VIDEO_ASSET_TTL_HOURS=24
```

- [ ] **Step 4: Тест раскладки путей**

Создать `tests/test_storage.py`:

```python
from pathlib import Path

from server.app.config import Settings
from server.app.storage import (
    PUBLIC_FILES,
    asset_dir,
    file_url,
    kind_from_ext,
    parse_file_url,
    safe_ext,
    upload_path,
)


def test_safe_ext_lowercases_and_rejects_garbage():
    assert safe_ext("Clip.MP4") == "mp4"
    assert safe_ext("noext") == "bin"
    assert safe_ext("weird.tar.gz") == "gz"
    assert safe_ext("bad.ext with space") == "bin"
    assert safe_ext("x." + "a" * 9) == "bin"


def test_kind_from_ext():
    assert kind_from_ext("mov") == "video"
    assert kind_from_ext("mp3") == "audio"
    assert kind_from_ext("srt") == "subtitle"
    assert kind_from_ext("bin") is None


def test_paths_come_from_ids_only(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "d")
    assert asset_dir(s, "usr_0123456789ab", "ast_0123456789ab") == (
        tmp_path / "d" / "usr_0123456789ab" / "assets" / "ast_0123456789ab"
    )
    assert upload_path(s, "upl_0123456789ab") == tmp_path / "d" / "tmp" / "uploads" / "upl_0123456789ab"
    for bad in ("u", "../../etc", "usr_0123456789ab/x", "USR_0123456789AB"):
        with pytest.raises(ValueError):
            asset_dir(s, bad, "ast_0123456789ab")
    with pytest.raises(ValueError):
        upload_path(s, "../x")


def test_file_url_roundtrip():
    url = file_url("usr_0123456789ab", "ast_0123456789ab", "proxy.mp4")
    assert url == "/files/usr_0123456789ab/assets/ast_0123456789ab/proxy.mp4"
    assert parse_file_url(url) == ("usr_0123456789ab", "ast_0123456789ab", "proxy.mp4")


def test_parse_file_url_rejects_bad_shapes():
    assert parse_file_url("/files/usr_0123456789ab/assets/ast_0123456789ab/../x") is None
    assert parse_file_url("/files/usr_x/assets/ast_0123456789ab/proxy.mp4") is None
    assert parse_file_url("/api/v1/me") is None
    assert parse_file_url("/files/usr_0123456789ab/assets/ast_0123456789ab/") is None


def test_public_files_exclude_source():
    assert "proxy.mp4" in PUBLIC_FILES and "peaks.json" in PUBLIC_FILES
    assert not any(name.startswith("source") for name in PUBLIC_FILES)
```

- [ ] **Step 5: Модуль раскладки**

Создать `server/app/storage.py`:

```python
"""Раскладка файлов на диске и публичные ссылки. Пути выводятся только из идентификаторов,
имена исходных файлов в путях не участвуют (раздел 6.2 спеки).
"""
from __future__ import annotations

import re
from pathlib import Path

from server.app.config import Settings

KINDS = ("video", "audio", "subtitle")
VIDEO_EXTS = {"mp4", "mov", "m4v", "mkv", "webm", "avi", "mts", "m2ts", "mxf", "ts", "wmv", "flv", "3gp"}
AUDIO_EXTS = {"mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "aiff", "aif", "wma"}
SUBTITLE_EXTS = {"srt", "vtt"}
# Файлы ассета, которые отдаются наружу. source.* сюда не входит намеренно (раздел 11 спеки).
PUBLIC_FILES = ("proxy.mp4", "proxy.m4a", "thumbs.jpg", "thumbs.json", "peaks.json", "analysis.json")

ID_RE = re.compile(r"^[a-z]{3}_[0-9a-f]{12}$")
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")
_FILE_URL_RE = re.compile(r"^/files/([^/]+)/assets/([^/]+)/([^/]+)$")


def safe_ext(filename: str) -> str:
    """Расширение в нижнем регистре из букв и цифр (до 8 знаков), иначе bin."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext if _EXT_RE.match(ext) else "bin"


def kind_from_ext(ext: str) -> str | None:
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in SUBTITLE_EXTS:
        return "subtitle"
    return None


def asset_dir(settings: Settings, user_id: str, asset_id: str) -> Path:
    return settings.data_dir / user_id / "assets" / asset_id


def upload_path(settings: Settings, upload_id: str) -> Path:
    return settings.uploads_tmp_path / upload_id


def file_url(user_id: str, asset_id: str, name: str) -> str:
    return f"/files/{user_id}/assets/{asset_id}/{name}"


def parse_file_url(path: str) -> tuple[str, str, str] | None:
    """(user_id, asset_id, name) из пути /files/...; идентификаторы проверяются по форме."""
    m = _FILE_URL_RE.match(path)
    if not m:
        return None
    user_id, asset_id, name = m.groups()
    if not (ID_RE.match(user_id) and ID_RE.match(asset_id)):
        return None
    return user_id, asset_id, name
```

- [ ] **Step 6: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_config.py tests/test_storage.py && uv run ruff check .`
Expected: PASS, `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add server/app/config.py server/app/storage.py .env.example tests/test_config.py tests/test_storage.py
git commit -m "feat(storage): settings for uploads, quotas and ttl; path layout module"
```

---

### Task 2: Миграция 0004 и постановка заданий

**Files:**
- Create: `server/db/migrations/0004_assets.sql`
- Create: `server/app/jobs.py`
- Modify: `tests/test_db_migrate.py:29`
- Test: `tests/test_jobs.py`

- [ ] **Step 1: Обновить ожидание списка миграций**

В `tests/test_db_migrate.py` строка `assert migrate(conn) == [1, 2, 3]` → `assert migrate(conn) == [1, 2, 3, 4]`. Других мест с точным списком нет (проверить `grep -n "\[1, 2, 3\]" tests/`).

- [ ] **Step 2: Тест заданий**

Создать `tests/test_jobs.py`:

```python
import sqlite3

import pytest

from server.app.jobs import LANES, cancel_jobs_for_target, enqueue_job
from server.app.util import now_iso
from server.db.migrate import migrate


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"), isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    migrate(c)
    c.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES ('usr_000000000001', 'a@b.c', 'A', ?)",
        (now_iso(),),
    )
    yield c
    c.close()


def test_enqueue_sets_lane_and_defaults(conn):
    job_id = enqueue_job(conn, user_id="usr_000000000001", type_="analyze", target_id="ast_1", priority=10)
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job_id.startswith("job_")
    assert row["lane"] == "cpu" and row["status"] == "queued" and row["priority"] == 10
    assert row["params"] == "{}" and row["progress"] == 0 and row["attempts"] == 0
    assert LANES["transcribe"] == "net"


def test_enqueue_rejects_unknown_type(conn):
    with pytest.raises(ValueError):
        enqueue_job(conn, user_id="usr_000000000001", type_="explode", target_id="x")


def test_cancel_only_touches_open_jobs(conn):
    a = enqueue_job(conn, user_id="usr_000000000001", type_="analyze", target_id="ast_1")
    b = enqueue_job(conn, user_id="usr_000000000001", type_="proxy", target_id="ast_1")
    conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (b,))
    enqueue_job(conn, user_id="usr_000000000001", type_="analyze", target_id="ast_2")
    assert cancel_jobs_for_target(conn, "ast_1") == 1
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (a,)).fetchone()[0] == "canceled"
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (b,)).fetchone()[0] == "done"
    assert conn.execute("SELECT count(*) FROM jobs WHERE status = 'queued'").fetchone()[0] == 1
```

- [ ] **Step 3: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_jobs.py tests/test_db_migrate.py`
Expected: FAIL (нет модуля `server.app.jobs`, миграций три).

- [ ] **Step 4: Миграция**

Создать `server/db/migrations/0004_assets.sql`:

```sql
-- Загрузки по частям: файл живёт в tmp/uploads/{id}, path хранится для janitor.
CREATE TABLE uploads (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('video', 'audio', 'subtitle')),
    chunk_size INTEGER NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX uploads_user_idx ON uploads(user_id);
CREATE INDEX uploads_expires_idx ON uploads(expires_at);

CREATE TABLE upload_chunks (
    upload_id TEXT NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    PRIMARY KEY (upload_id, idx)
);

-- ext: расширение исходника (source.<ext>), чтобы находить файл без обращения к диску.
CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('video', 'audio', 'subtitle')),
    original_name TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('uploaded', 'analyzing', 'ready', 'proxy_ready', 'failed')),
    duration REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    has_audio INTEGER CHECK (has_audio IN (0, 1)),
    video_codec TEXT,
    audio_codec TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    last_access_at TEXT NOT NULL
);
CREATE INDEX assets_user_idx ON assets(user_id, created_at);
CREATE INDEX assets_last_access_idx ON assets(last_access_at);

-- attempts растёт при каждом взятии задания воркером; heartbeat_at обновляет воркер раз в 10 с.
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('analyze', 'proxy', 'render', 'transcribe')),
    lane TEXT NOT NULL CHECK (lane IN ('cpu', 'net')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'canceled')),
    priority INTEGER NOT NULL DEFAULT 0,
    target_id TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    heartbeat_at TEXT,
    worker_pid INTEGER
);
CREATE INDEX jobs_queue_idx ON jobs(status, lane, priority, created_at);
CREATE INDEX jobs_target_idx ON jobs(target_id);
```

- [ ] **Step 5: Модуль заданий**

Создать `server/app/jobs.py`:

```python
"""Постановка заданий в таблицу jobs. Воркер (план M1b) забирает их атомарным UPDATE ... RETURNING."""
from __future__ import annotations

import json
import sqlite3

from server.app.util import new_id, now_iso

LANES = {"analyze": "cpu", "proxy": "cpu", "render": "cpu", "transcribe": "net"}


def enqueue_job(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    type_: str,
    target_id: str,
    priority: int = 0,
    params: dict | None = None,
) -> str:
    """Вставляет задание в статусе queued и возвращает его id. Транзакцию открывает вызывающий, если нужна."""
    if type_ not in LANES:
        raise ValueError(f"unknown job type: {type_}")
    job_id = new_id("job")
    conn.execute(
        "INSERT INTO jobs (id, user_id, type, lane, status, priority, target_id, params, created_at) "
        "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
        (job_id, user_id, type_, LANES[type_], priority, target_id, json.dumps(params or {}), now_iso()),
    )
    return job_id


def cancel_jobs_for_target(conn: sqlite3.Connection, target_id: str) -> int:
    """Отменяет незавершённые задания цели (ассета, проекта). Выполняющееся задание воркер прервёт сам,
    увидев статус canceled при следующем пульсе (M1b)."""
    cur = conn.execute(
        "UPDATE jobs SET status = 'canceled', finished_at = ? "
        "WHERE target_id = ? AND status IN ('queued', 'running')",
        (now_iso(), target_id),
    )
    return cur.rowcount
```

- [ ] **Step 6: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_jobs.py tests/test_db_migrate.py && uv run ruff check .`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add server/db/migrations/0004_assets.sql server/app/jobs.py tests/test_jobs.py tests/test_db_migrate.py
git commit -m "feat(db): migration 0004 (uploads, assets, jobs) and job enqueue helper"
```

---

### Task 3: Хранилище загрузок: квота, части, завершение

**Files:**
- Create: `server/app/uploads/__init__.py` (пустой), `server/app/uploads/store.py`
- Test: `tests/test_uploads_store.py`

- [ ] **Step 1: Тесты хранилища**

Создать `tests/test_uploads_store.py`:

```python
import os
import sqlite3
from pathlib import Path

import pytest

from server.app.config import Settings
from server.app.storage import asset_dir
from server.app.uploads import store
from server.app.uploads.store import (
    ChunkWriter,
    UploadError,
    chunk_length,
    complete_upload,
    create_upload,
    delete_upload,
    finalize_file,
    get_upload,
    mark_chunk,
    received_chunks,
    total_chunks,
    used_bytes,
)
from server.app.util import now_iso
from server.db.migrate import migrate

USER = "usr_000000000001"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        chunk_size=1024,
        user_quota_bytes=10_000,
        max_upload_bytes=8_000,
    )


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = sqlite3.connect(str(settings.db_path), isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    migrate(c)
    c.execute("INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)", (USER, now_iso()))
    yield c
    c.close()


def test_chunk_arithmetic():
    up = {"size": 2500, "chunk_size": 1024}
    assert total_chunks(up) == 3
    assert chunk_length(up, 0) == 1024 and chunk_length(up, 2) == 452
    assert total_chunks({"size": 1024, "chunk_size": 1024}) == 1


def test_create_reserves_file_and_counts_quota(conn, settings):
    up = create_upload(conn, settings, USER, filename="Clip.MOV", size=2500, kind=None)
    assert up["id"].startswith("upl_") and up["kind"] == "video" and up["chunk_size"] == 1024
    assert Path(up["path"]).stat().st_size == 2500
    assert up["expires_at"] > up["created_at"]
    assert used_bytes(conn, USER) == 2500
    assert get_upload(conn, USER, up["id"])["filename"] == "Clip.MOV"
    assert get_upload(conn, "usr_000000000002", up["id"]) is None


def test_create_rejects_bad_input(conn, settings):
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="a.mp4", size=0, kind=None)
    assert e.value.code == "empty_file"
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="a.mp4", size=9_000, kind=None)
    assert e.value.code == "too_large"
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="a.mp4", size=100, kind="image")
    assert e.value.code == "bad_kind"
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="   ", size=100, kind=None)
    assert e.value.code == "bad_filename"


def test_quota_counts_assets_and_pending_uploads(conn, settings):
    create_upload(conn, settings, USER, filename="a.mp4", size=6_000, kind=None)
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="b.mp4", size=5_000, kind=None)
    assert e.value.code == "quota_exceeded"
    assert e.value.details == {"used_bytes": 6_000, "limit_bytes": 10_000}


def test_disk_low_blocks_new_uploads(conn, settings, monkeypatch):
    monkeypatch.setattr(store, "disk_free_pct_safe", lambda _path: 5.0)
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="a.mp4", size=100, kind=None)
    assert e.value.code == "disk_low"


def test_chunk_writer_writes_at_offset_and_guards_length(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"\0" * 10)
    w = ChunkWriter(path, offset=4, expected=3)
    w.write(b"ab")
    assert not w.done()
    w.write(b"c")
    assert w.done()
    w.close()
    assert path.read_bytes() == b"\0\0\0\0abc\0\0\0"
    w = ChunkWriter(path, offset=0, expected=2)
    with pytest.raises(UploadError) as e:
        w.write(b"xyz")
    w.close()
    assert e.value.code == "chunk_size_mismatch"


def test_complete_requires_all_chunks_and_exact_size(conn, settings):
    up = create_upload(conn, settings, USER, filename="a.mp4", size=2500, kind=None)
    mark_chunk(conn, up["id"], 0)
    mark_chunk(conn, up["id"], 0)  # повтор части допустим
    mark_chunk(conn, up["id"], 2)
    assert received_chunks(conn, up["id"]) == [0, 2]
    with pytest.raises(UploadError) as e:
        complete_upload(conn, settings, up)
    assert e.value.code == "incomplete" and e.value.details == {"missing": [1], "total": 3}
    mark_chunk(conn, up["id"], 1)
    os.truncate(up["path"], 2400)
    with pytest.raises(UploadError) as e:
        complete_upload(conn, settings, up)
    assert e.value.code == "size_mismatch"


def test_complete_moves_file_creates_asset_and_job(conn, settings):
    up = create_upload(conn, settings, USER, filename="a.mp4", size=2048, kind=None)
    Path(up["path"]).write_bytes(b"x" * 2048)
    for i in range(2):
        mark_chunk(conn, up["id"], i)
    asset = complete_upload(conn, settings, up)
    assert asset["id"].startswith("ast_") and asset["status"] == "uploaded" and asset["ext"] == "mp4"
    src = asset_dir(settings, USER, asset["id"]) / "source.mp4"
    assert src.read_bytes() == b"x" * 2048
    assert not Path(up["path"]).exists()
    assert conn.execute("SELECT count(*) FROM uploads").fetchone()[0] == 0
    job = conn.execute("SELECT type, status, priority, target_id FROM jobs").fetchone()
    assert tuple(job) == ("analyze", "queued", 10, asset["id"])
    assert used_bytes(conn, USER) == 2048


def test_subtitle_is_ready_without_job(conn, settings, tmp_path):
    src = tmp_path / "s.srt"
    src.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    asset = finalize_file(conn, settings, user_id=USER, src=src, filename="s.srt", size=src.stat().st_size, kind="subtitle")
    assert asset["status"] == "ready"
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_finalize_restores_file_when_db_insert_fails(conn, settings, tmp_path):
    src = tmp_path / "f.mp4"
    src.write_bytes(b"abc")
    with pytest.raises(sqlite3.IntegrityError):
        finalize_file(conn, settings, user_id="usr_0000000000ff", src=src, filename="f.mp4", size=3, kind="video")
    assert src.read_bytes() == b"abc"
    assert list((settings.data_dir / "usr_0000000000ff" / "assets").glob("ast_*")) == []


def test_delete_upload_removes_record_and_file(conn, settings):
    up = create_upload(conn, settings, USER, filename="a.mp4", size=100, kind=None)
    delete_upload(conn, up)
    assert not Path(up["path"]).exists()
    assert get_upload(conn, USER, up["id"]) is None
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_uploads_store.py`
Expected: FAIL, нет модуля `server.app.uploads.store`.

- [ ] **Step 3: Хранилище**

Создать пустой `server/app/uploads/__init__.py` и `server/app/uploads/store.py`:

```python
"""Загрузка по частям: запись в базе плюс файл в tmp/uploads; квота и свободный диск; завершение переносит
файл в папку ассета одним os.replace (тот же раздел диска) и ставит задание analyze.

Порядок «сначала база, потом файлы» при удалении и «сначала файл, потом база с откатом» при создании:
упавший процесс не оставляет записи без файла, а папку без записи подбирает janitor.
"""
from __future__ import annotations

import math
import os
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

from server.app.config import Settings
from server.app.health import disk_free_pct_safe
from server.app.jobs import enqueue_job
from server.app.storage import KINDS, asset_dir, kind_from_ext, safe_ext, upload_path
from server.app.util import iso, new_id, now_iso, utcnow
from server.db.core import transaction

ANALYZE_PRIORITY = 10  # выше рендера (0): раздел 7 спеки
MAX_FILENAME = 255


class UploadError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def total_chunks(upload: dict | sqlite3.Row) -> int:
    return max(1, math.ceil(upload["size"] / upload["chunk_size"]))


def chunk_length(upload: dict | sqlite3.Row, idx: int) -> int:
    """Все части ровно chunk_size, последняя короче."""
    last = total_chunks(upload) - 1
    return upload["chunk_size"] if idx < last else upload["size"] - last * upload["chunk_size"]


def used_bytes(conn: sqlite3.Connection, user_id: str) -> int:
    """Квота считает и готовые ассеты, и незавершённые загрузки: место под них уже занято."""
    assets = conn.execute("SELECT coalesce(sum(size), 0) FROM assets WHERE user_id = ?", (user_id,)).fetchone()[0]
    uploads = conn.execute("SELECT coalesce(sum(size), 0) FROM uploads WHERE user_id = ?", (user_id,)).fetchone()[0]
    return int(assets) + int(uploads)


def check_capacity(conn: sqlite3.Connection, settings: Settings, user_id: str, size: int) -> None:
    if size <= 0:
        raise UploadError(422, "empty_file", "Пустой файл")
    if size > settings.max_upload_bytes:
        raise UploadError(413, "too_large", "Файл больше допустимого", {"limit_bytes": settings.max_upload_bytes})
    used = used_bytes(conn, user_id)
    if used + size > settings.user_quota_bytes:
        raise UploadError(
            413, "quota_exceeded", "Квота исчерпана", {"used_bytes": used, "limit_bytes": settings.user_quota_bytes}
        )
    free = disk_free_pct_safe(settings.data_dir)
    if free < settings.disk_low_pct:
        raise UploadError(507, "disk_low", "На диске мало места, загрузки приостановлены", {"disk_free_pct": free})


def clean_filename(filename: str) -> str:
    name = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > MAX_FILENAME:
        raise UploadError(422, "bad_filename", "Имя файла пустое или длиннее 255 знаков")
    return name


def resolve_kind(filename: str, kind: str | None) -> str:
    if kind is not None:
        if kind not in KINDS:
            raise UploadError(422, "bad_kind", "kind: video, audio или subtitle")
        return kind
    return kind_from_ext(safe_ext(filename)) or "video"


def reserve_file(path: Path, size: int) -> None:
    """Файл нужного размера. posix_fallocate занимает место сразу (ENOSPC при создании, а не на последней
    части); на Windows его нет, там разреженный файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        if hasattr(os, "posix_fallocate"):
            os.posix_fallocate(fd, 0, size)
        else:
            os.ftruncate(fd, size)
    finally:
        os.close(fd)


def create_upload(
    conn: sqlite3.Connection, settings: Settings, user_id: str, *, filename: str, size: int, kind: str | None
) -> dict:
    filename = clean_filename(filename)
    kind = resolve_kind(filename, kind)
    check_capacity(conn, settings, user_id, size)
    upload_id = new_id("upl")
    path = upload_path(settings, upload_id)
    try:
        reserve_file(path, size)
    except OSError as exc:
        raise UploadError(507, "disk_low", "Не удалось зарезервировать место под файл") from exc
    now = utcnow()
    row = {
        "id": upload_id,
        "user_id": user_id,
        "filename": filename,
        "size": size,
        "kind": kind,
        "chunk_size": settings.chunk_size,
        "path": str(path),
        "created_at": iso(now),
        "expires_at": iso(now + timedelta(hours=settings.upload_ttl_hours)),
    }
    try:
        conn.execute(
            "INSERT INTO uploads (id, user_id, filename, size, kind, chunk_size, path, created_at, expires_at) "
            "VALUES (:id, :user_id, :filename, :size, :kind, :chunk_size, :path, :created_at, :expires_at)",
            row,
        )
    except sqlite3.Error:
        path.unlink(missing_ok=True)
        raise
    return row


def get_upload(conn: sqlite3.Connection, user_id: str, upload_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM uploads WHERE id = ? AND user_id = ?", (upload_id, user_id)).fetchone()


class ChunkWriter:
    """Пишет одну часть по смещению кусками. Свой дескриптор на запрос: параллельные части не мешают друг другу.
    Не os.pwrite: его нет на Windows, где идёт разработка."""

    def __init__(self, path: Path, *, offset: int, expected: int) -> None:
        self.expected = expected
        self.written = 0
        self._f = open(path, "r+b")  # закрывается в close(): живёт дольше одного блока with
        self._f.seek(offset)

    def write(self, data: bytes) -> None:
        if self.written + len(data) > self.expected:
            raise UploadError(422, "chunk_size_mismatch", "Часть длиннее ожидаемой", {"expected": self.expected})
        self._f.write(data)
        self.written += len(data)

    def done(self) -> bool:
        return self.written == self.expected

    def close(self) -> None:
        self._f.close()


def mark_chunk(conn: sqlite3.Connection, upload_id: str, idx: int) -> None:
    conn.execute("INSERT OR IGNORE INTO upload_chunks (upload_id, idx) VALUES (?, ?)", (upload_id, idx))


def received_chunks(conn: sqlite3.Connection, upload_id: str) -> list[int]:
    return [r[0] for r in conn.execute("SELECT idx FROM upload_chunks WHERE upload_id = ? ORDER BY idx", (upload_id,))]


def finalize_file(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    user_id: str,
    src: Path,
    filename: str,
    size: int,
    kind: str,
    upload_id: str | None = None,
) -> dict:
    """Переносит готовый файл в папку ассета, создаёт запись ассета и задание analyze (кроме субтитров).
    При ошибке базы файл возвращается на место, чтобы завершение можно было повторить."""
    asset_id = new_id("ast")
    ext = safe_ext(filename)
    target_dir = asset_dir(settings, user_id, asset_id)
    target_dir.mkdir(parents=True, exist_ok=False)
    dst = target_dir / f"source.{ext}"
    try:
        os.replace(src, dst)  # тот же раздел; EXDEV означает неверный VIDEO_TMP_DIR
    except FileNotFoundError as exc:
        # Второй complete той же загрузки наперегонки с первым: файл уже переехал.
        target_dir.rmdir()
        raise UploadError(410, "file_missing", "Файл загрузки пропал, начните заново") from exc
    now = now_iso()
    status = "ready" if kind == "subtitle" else "uploaded"
    row = {
        "id": asset_id,
        "user_id": user_id,
        "kind": kind,
        "original_name": filename,
        "ext": ext,
        "size": size,
        "status": status,
        "created_at": now,
        "last_access_at": now,
    }
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, created_at, last_access_at) "
                "VALUES (:id, :user_id, :kind, :original_name, :ext, :size, :status, :created_at, :last_access_at)",
                row,
            )
            if upload_id is not None:
                conn.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
            if kind != "subtitle":
                enqueue_job(conn, user_id=user_id, type_="analyze", target_id=asset_id, priority=ANALYZE_PRIORITY)
    except Exception:
        os.replace(dst, src)
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    return row


def complete_upload(conn: sqlite3.Connection, settings: Settings, upload: dict | sqlite3.Row) -> dict:
    total = total_chunks(upload)
    got = set(received_chunks(conn, upload["id"]))
    missing = [i for i in range(total) if i not in got]
    if missing:
        raise UploadError(409, "incomplete", "Дошли не все части", {"missing": missing[:100], "total": total})
    path = Path(upload["path"])
    try:
        actual = path.stat().st_size
    except OSError as exc:
        raise UploadError(410, "file_missing", "Файл загрузки пропал, начните заново") from exc
    if actual != upload["size"]:
        raise UploadError(409, "size_mismatch", "Размер файла не совпал с заявленным", {"actual": actual})
    return finalize_file(
        conn,
        settings,
        user_id=upload["user_id"],
        src=path,
        filename=upload["filename"],
        size=upload["size"],
        kind=upload["kind"],
        upload_id=upload["id"],
    )


def delete_upload(conn: sqlite3.Connection, upload: dict | sqlite3.Row) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM uploads WHERE id = ?", (upload["id"],))
    Path(upload["path"]).unlink(missing_ok=True)
```

- [ ] **Step 4: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_uploads_store.py && uv run ruff check .`
Expected: PASS, `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add server/app/uploads tests/test_uploads_store.py
git commit -m "feat(uploads): store with quota, chunk writer, completion and finalize"
```

---
### Task 4: Маршруты загрузки `/api/v1/uploads`

**Files:**
- Create: `server/app/uploads/routes.py`
- Modify: `server/app/main.py` (лимитер загрузок, подключение роутера), `tests/conftest.py`
- Test: `tests/test_uploads_api.py`

- [ ] **Step 1: Фикстуры**

В `tests/conftest.py` в фикстуре `settings` добавить аргументы (после `session_idle_days=7,`):

```python
        tmp_dir=tmp_path / "tmp",
        chunk_size=1024,
        user_quota_bytes=10 * 1024 * 1024,
        max_upload_bytes=8 * 1024 * 1024,
        small_upload_max_bytes=1024 * 1024,
        uploads_per_hour=1000,
```

и в конец файла фикстуру клиента с токеном:

```python
@pytest.fixture
def bearer_client(app, client, login_as):
    """Второй клиент без cookie и без Origin: агент с Bearer-токеном того же пользователя."""
    login_as()
    r = client.post("/api/v1/tokens", json={"name": "agent"})
    assert r.status_code == 201, r.text
    secret = r.json()["secret"]
    with TestClient(app, headers={"Authorization": f"Bearer {secret}"}) as c:
        yield c
```

- [ ] **Step 2: Тесты API загрузки**

Создать `tests/test_uploads_api.py`:

```python
import os

from server.app.ratelimit import FixedWindowLimiter
from server.app.uploads import store

OCTET = {"Content-Type": "application/octet-stream"}


def _create(client, size, filename="clip.mp4", kind=None):
    body = {"filename": filename, "size": size}
    if kind:
        body["kind"] = kind
    r = client.post("/api/v1/uploads", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _put(client, upload_id, idx, data):
    return client.put(f"/api/v1/uploads/{upload_id}/chunks/{idx}", content=data, headers=OCTET)


def _whitelist_and_login(client, login_as, email):
    login_as()  # админ
    assert client.post("/api/v1/admin/whitelist", json={"email": email}).status_code == 201
    return login_as(email, "Other")


def test_roundtrip_out_of_order_repeat_and_complete(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    data = os.urandom(2 * 1024 + 300)
    up = _create(client, len(data))
    assert up["chunk_size"] == 1024 and up["total_chunks"] == 3 and up["expires_at"].endswith("Z")
    uid = up["upload_id"]
    assert _put(client, uid, 2, data[2048:]).status_code == 204
    assert _put(client, uid, 0, data[:1024]).status_code == 204
    assert _put(client, uid, 0, data[:1024]).status_code == 204  # повтор части
    st = client.get(f"/api/v1/uploads/{uid}").json()
    assert st == {"upload_id": uid, "received": [0, 2], "total": 3, "size": len(data), "chunk_size": 1024}
    r = client.post(f"/api/v1/uploads/{uid}/complete")
    assert r.status_code == 409 and r.json()["error"]["code"] == "incomplete"
    assert r.json()["error"]["details"]["missing"] == [1]
    assert _put(client, uid, 1, data[1024:2048]).status_code == 204
    r = client.post(f"/api/v1/uploads/{uid}/complete")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "uploaded"
    asset_id = r.json()["asset_id"]
    source = settings.data_dir / me["id"] / "assets" / asset_id / "source.mp4"
    assert source.read_bytes() == data
    assert client.get(f"/api/v1/uploads/{uid}").status_code == 404
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 200


def test_chunk_length_is_checked(client, login_as):
    login_as()
    uid = _create(client, 2048)["upload_id"]
    r = _put(client, uid, 0, b"x" * 1000)
    assert r.status_code == 422 and r.json()["error"]["code"] == "chunk_size_mismatch"
    assert r.json()["error"]["details"] == {"expected": 1024, "received": 1000}
    r = _put(client, uid, 1, b"x" * 1025)
    assert r.status_code == 422
    r = _put(client, uid, 2, b"x")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_such_chunk"
    assert client.get(f"/api/v1/uploads/{uid}").json()["received"] == []


def test_create_validation_and_limits(client, login_as, monkeypatch):
    login_as()
    r = client.post("/api/v1/uploads", json={"filename": "a.mp4", "size": 0})
    assert r.status_code == 422
    r = client.post("/api/v1/uploads", json={"filename": "a.mp4", "size": 9 * 1024 * 1024})
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    r = client.post("/api/v1/uploads", json={"filename": "a.mp4", "size": 10, "kind": "image"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_kind"
    _create(client, 6 * 1024 * 1024)
    r = client.post("/api/v1/uploads", json={"filename": "b.mp4", "size": 5 * 1024 * 1024})
    assert r.status_code == 413 and r.json()["error"]["code"] == "quota_exceeded"
    monkeypatch.setattr(store, "disk_free_pct_safe", lambda _p: 3.0)
    r = client.post("/api/v1/uploads", json={"filename": "c.mp4", "size": 10})
    assert r.status_code == 507 and r.json()["error"]["code"] == "disk_low"


def test_upload_rate_limit_per_user(app, client, login_as):
    login_as()
    app.state.upload_limiter = FixedWindowLimiter(2, 3600)
    _create(client, 10)
    _create(client, 10)
    r = client.post("/api/v1/uploads", json={"filename": "a.mp4", "size": 10})
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


def test_foreign_upload_is_404(client, login_as):
    login_as()
    uid = _create(client, 10)["upload_id"]
    _whitelist_and_login(client, login_as, "other@ya.ru")
    assert client.get(f"/api/v1/uploads/{uid}").status_code == 404
    assert _put(client, uid, 0, b"x" * 10).status_code == 404
    assert client.post(f"/api/v1/uploads/{uid}/complete").status_code == 404
    assert client.delete(f"/api/v1/uploads/{uid}").status_code == 404


def test_delete_upload_frees_quota(client, login_as, settings):
    login_as()
    up = _create(client, 500)
    path = settings.uploads_tmp_path / up["upload_id"]
    assert path.stat().st_size == 500
    assert client.get("/api/v1/me").json()["quota"]["used_bytes"] == 500
    assert client.delete(f"/api/v1/uploads/{up['upload_id']}").status_code == 204
    assert not path.exists()
    assert client.get("/api/v1/me").json()["quota"]["used_bytes"] == 0


def test_agent_uploads_with_bearer_token(bearer_client):
    data = b"a" * 1024 + b"b" * 10
    up = _create(bearer_client, len(data), filename="talk.wav")
    assert _put(bearer_client, up["upload_id"], 0, data[:1024]).status_code == 204
    assert _put(bearer_client, up["upload_id"], 1, data[1024:]).status_code == 204
    r = bearer_client.post(f"/api/v1/uploads/{up['upload_id']}/complete")
    assert r.status_code == 200, r.text
    asset = bearer_client.get(f"/api/v1/assets/{r.json()['asset_id']}").json()
    assert asset["kind"] == "audio" and asset["original_name"] == "talk.wav"


def test_requires_auth(client):
    assert client.post("/api/v1/uploads", json={"filename": "a.mp4", "size": 10}).status_code == 401
    assert client.get("/api/v1/uploads/upl_000000000000").status_code == 401
```

- [ ] **Step 3: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_uploads_api.py`
Expected: FAIL, 404 на `/api/v1/uploads` (роутера нет). Тесты с `/api/v1/assets/...` и `quota` в `/me` заработают после Task 5: до него они падают, это ожидаемо.

- [ ] **Step 4: Роутер**

Создать `server/app/uploads/routes.py`:

```python
"""Загрузка по частям: /api/v1/uploads. Часть приходит сырыми байтами и пишется потоком по смещению."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from server.app.auth.deps import CurrentUser, current_user
from server.app.errors import ApiError
from server.app.uploads.store import (
    ChunkWriter,
    UploadError,
    chunk_length,
    complete_upload,
    create_upload,
    delete_upload,
    get_upload,
    mark_chunk,
    received_chunks,
    total_chunks,
)
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])

WRITE_BATCH = 4 * 1024 * 1024  # столько буферим в памяти между записями на диск


class UploadCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1)
    kind: str | None = None


class UploadCreated(BaseModel):
    upload_id: str
    chunk_size: int
    total_chunks: int
    expires_at: str


class UploadStatus(BaseModel):
    upload_id: str
    received: list[int]
    total: int
    size: int
    chunk_size: int


class UploadCompleted(BaseModel):
    asset_id: str
    status: str


def api_error(exc: UploadError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message, exc.details)


def _owned(conn: sqlite3.Connection, user: CurrentUser, upload_id: str) -> sqlite3.Row:
    row = get_upload(conn, user.id, upload_id)
    if row is None:
        raise ApiError(404, "not_found", "Загрузка не найдена")
    return row


def _mismatch(expected: int, received: int) -> ApiError:
    return ApiError(
        422,
        "chunk_size_mismatch",
        "Длина части не совпала с ожидаемой",
        {"expected": expected, "received": received},
    )


@router.post("", status_code=201, response_model=UploadCreated)
def create(
    body: UploadCreate,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> UploadCreated:
    if not request.app.state.upload_limiter.allow(user.id):
        raise ApiError(429, "rate_limited", "Слишком много новых загрузок, подождите час")
    try:
        row = create_upload(
            conn, request.app.state.settings, user.id, filename=body.filename, size=body.size, kind=body.kind
        )
    except UploadError as exc:
        raise api_error(exc) from exc
    return UploadCreated(
        upload_id=row["id"], chunk_size=row["chunk_size"], total_chunks=total_chunks(row), expires_at=row["expires_at"]
    )


@router.get("/{upload_id}", response_model=UploadStatus)
def status(
    upload_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> UploadStatus:
    row = _owned(conn, user, upload_id)
    return UploadStatus(
        upload_id=row["id"],
        received=received_chunks(conn, row["id"]),
        total=total_chunks(row),
        size=row["size"],
        chunk_size=row["chunk_size"],
    )


@router.put("/{upload_id}/chunks/{idx}", status_code=204)
async def put_chunk(
    upload_id: str,
    idx: int,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    row = _owned(conn, user, upload_id)
    total = total_chunks(row)
    if idx < 0 or idx >= total:
        raise ApiError(404, "no_such_chunk", "Нет части с таким номером", {"total": total})
    expected = chunk_length(row, idx)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) != expected:
        raise _mismatch(expected, int(declared))
    try:
        writer = ChunkWriter(Path(row["path"]), offset=idx * row["chunk_size"], expected=expected)
    except OSError as exc:
        raise ApiError(410, "file_missing", "Файл загрузки пропал, начните заново") from exc
    try:
        buf = bytearray()
        async for piece in request.stream():
            buf += piece
            if len(buf) >= WRITE_BATCH:
                await run_in_threadpool(writer.write, bytes(buf))
                buf.clear()
        if buf:
            await run_in_threadpool(writer.write, bytes(buf))
    except UploadError as exc:
        raise api_error(exc) from exc
    finally:
        writer.close()
    if not writer.done():
        raise _mismatch(expected, writer.written)
    mark_chunk(conn, row["id"], idx)
    return Response(status_code=204)


@router.post("/{upload_id}/complete", response_model=UploadCompleted)
def complete(
    upload_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> UploadCompleted:
    row = _owned(conn, user, upload_id)
    try:
        asset = complete_upload(conn, request.app.state.settings, row)
    except UploadError as exc:
        raise api_error(exc) from exc
    return UploadCompleted(asset_id=asset["id"], status=asset["status"])


@router.delete("/{upload_id}", status_code=204)
def cancel(
    upload_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    delete_upload(conn, _owned(conn, user, upload_id))
    return Response(status_code=204)
```

- [ ] **Step 5: Подключить в приложении**

В `server/app/main.py`:

```python
from server.app.uploads.routes import router as uploads_router
```

после `app.state.login_limiter = ...`:

```python
    app.state.upload_limiter = FixedWindowLimiter(settings.uploads_per_hour, 3600)
```

в lifespan после `settings.data_dir.mkdir(...)`:

```python
        settings.uploads_tmp_path.mkdir(parents=True, exist_ok=True)
        if settings.uploads_tmp_path.stat().st_dev != settings.data_dir.stat().st_dev:
            log.warning("tmp_dir %s и data_dir %s на разных разделах: завершение загрузки будет падать", settings.tmp_path, settings.data_dir)
```

(строку `log.warning` разбить, чтобы уложиться в 110 знаков) и `app.include_router(uploads_router)` перед комментарием «Роутеры API из следующих задач подключаются ВЫШЕ этой строки».

- [ ] **Step 6: Прогнать тесты**

Run: `uv run python -m pytest tests/test_uploads_api.py && uv run ruff check .`
Expected: PASS всё, кроме проверок `/api/v1/assets/...` и `quota` (три теста: roundtrip, bearer, delete_upload_frees_quota) — они зелёные после Task 5. Остальной набор `uv run python -m pytest` зелёный.

- [ ] **Step 7: Commit**

```bash
git add server/app/uploads/routes.py server/app/main.py tests/conftest.py tests/test_uploads_api.py
git commit -m "feat(uploads): chunked upload API with resume, quota and per-user rate limit"
```

---

### Task 5: Ассеты: карточки, список, удаление, мелкая загрузка, квота в `/me`

**Files:**
- Create: `server/app/assets/__init__.py` (пустой), `server/app/assets/views.py`, `server/app/assets/routes.py`
- Modify: `server/app/auth/routes.py` (`/me`), `server/app/main.py`
- Test: `tests/test_assets_api.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_assets_api.py`:

```python
import sqlite3

from server.app.assets.views import asset_view
from server.app.util import now_iso

SRT = b"1\n00:00:00,000 --> 00:00:01,000\nhi\n"


def _upload_small(client, name="s.srt", data=SRT, kind=None):
    files = {"file": (name, data, "application/octet-stream")}
    form = {"kind": kind} if kind else {}
    return client.post("/api/v1/assets/upload", files=files, data=form)


def _row(**over):
    base = {
        "id": "ast_0123456789ab", "user_id": "usr_0123456789ab", "kind": "video", "original_name": "a.mp4",
        "ext": "mp4", "size": 1, "status": "uploaded", "duration": None, "width": None, "height": None,
        "fps": None, "has_audio": None, "video_codec": None, "audio_codec": None, "error": None,
        "created_at": "2026-09-04T00:00:00.000Z", "last_access_at": "2026-09-04T00:00:00.000Z",
    }
    return {**base, **over}


def test_view_links_follow_status():
    v = asset_view(_row())
    assert v.files.model_dump() == {"proxy": None, "thumbs": None, "thumbs_meta": None, "peaks": None, "analysis": None}
    v = asset_view(_row(status="ready", has_audio=1))
    assert v.has_audio is True and v.files.proxy is None
    assert v.files.peaks == "/files/usr_0123456789ab/assets/ast_0123456789ab/peaks.json"
    assert v.files.thumbs == "/files/usr_0123456789ab/assets/ast_0123456789ab/thumbs.jpg"
    v = asset_view(_row(status="proxy_ready"))
    assert v.files.proxy.endswith("/proxy.mp4")
    v = asset_view(_row(kind="audio", status="proxy_ready"))
    assert v.files.proxy.endswith("/proxy.m4a") and v.files.thumbs is None
    v = asset_view(_row(kind="subtitle", status="ready"))
    assert v.files.peaks is None and v.files.proxy is None


def test_small_upload_list_get_delete(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    r = _upload_small(client)
    assert r.status_code == 201, r.text
    asset = r.json()
    assert asset["kind"] == "subtitle" and asset["status"] == "ready" and asset["size"] == len(SRT)
    assert asset["original_name"] == "s.srt"
    source = settings.data_dir / me["id"] / "assets" / asset["id"] / "source.srt"
    assert source.read_bytes() == SRT
    listing = client.get("/api/v1/assets").json()["assets"]
    assert [a["id"] for a in listing] == [asset["id"]]
    assert client.get(f"/api/v1/assets/{asset['id']}").json()["id"] == asset["id"]
    assert client.get("/api/v1/me").json()["quota"] == {"used_bytes": len(SRT), "limit_bytes": 10 * 1024 * 1024}
    assert client.delete(f"/api/v1/assets/{asset['id']}").status_code == 204
    assert not source.parent.exists()
    assert client.get(f"/api/v1/assets/{asset['id']}").status_code == 404
    assert client.get("/api/v1/assets").json()["assets"] == []


def test_small_upload_of_video_queues_analyze(client, login_as, settings):
    login_as()
    r = _upload_small(client, name="c.mp4", data=b"\0" * 100)
    assert r.status_code == 201 and r.json()["status"] == "uploaded"
    conn = sqlite3.connect(str(settings.db_path))
    job = conn.execute("SELECT type, target_id FROM jobs").fetchone()
    conn.close()
    assert job == ("analyze", r.json()["id"])


def test_small_upload_limits(client, login_as):
    login_as()
    r = _upload_small(client, name="big.mp3", data=b"\0" * (1024 * 1024 + 1))
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    r = _upload_small(client, name="e.srt", data=b"")
    assert r.status_code == 422 and r.json()["error"]["code"] == "empty_file"
    r = _upload_small(client, kind="image")
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_kind"
    assert client.get("/api/v1/assets").json()["assets"] == []


def test_delete_cancels_open_jobs(client, login_as, settings):
    login_as()
    asset_id = _upload_small(client, name="c.mp4", data=b"\0" * 10).json()["id"]
    assert client.delete(f"/api/v1/assets/{asset_id}").status_code == 204
    conn = sqlite3.connect(str(settings.db_path))
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "canceled"
    conn.close()


def test_foreign_asset_is_404(client, login_as):
    login_as()
    asset_id = _upload_small(client).json()["id"]
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 404
    assert client.delete(f"/api/v1/assets/{asset_id}").status_code == 404
    assert client.get("/api/v1/assets").json()["assets"] == []


def test_bearer_can_list_and_delete(bearer_client):
    r = _upload_small(bearer_client)
    assert r.status_code == 201, r.text
    assert len(bearer_client.get("/api/v1/assets").json()["assets"]) == 1
    assert bearer_client.delete(f"/api/v1/assets/{r.json()['id']}").status_code == 204


def test_me_has_quota_and_requires_auth(client, login_as):
    assert client.get("/api/v1/me").status_code == 401
    login_as()
    me = client.get("/api/v1/me").json()
    assert me["quota"] == {"used_bytes": 0, "limit_bytes": 10 * 1024 * 1024}
    assert me["role"] == "admin" and me["auth"] == "cookie" and now_iso().endswith("Z")
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_assets_api.py`
Expected: FAIL, нет модуля `server.app.assets`.

- [ ] **Step 3: Карточка**

Создать пустой `server/app/assets/__init__.py` и `server/app/assets/views.py`:

```python
"""Карточка ассета для API: метаданные и относительные ссылки на производные файлы.

Ссылки выводятся из статуса, а не из наличия файлов на диске: список ассетов не должен ходить на диск.
"""
from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from server.app.storage import file_url


class AssetFiles(BaseModel):
    proxy: str | None = None
    thumbs: str | None = None
    thumbs_meta: str | None = None
    peaks: str | None = None
    analysis: str | None = None


class AssetView(BaseModel):
    id: str
    kind: str
    original_name: str
    size: int
    status: str
    duration: float | None
    width: int | None
    height: int | None
    fps: float | None
    has_audio: bool | None
    video_codec: str | None
    audio_codec: str | None
    error: str | None
    created_at: str
    last_access_at: str
    files: AssetFiles


def asset_files(row: dict | sqlite3.Row) -> AssetFiles:
    user_id, asset_id, kind, status = row["user_id"], row["id"], row["kind"], row["status"]
    files = AssetFiles()
    if kind == "subtitle":
        return files
    if status in ("ready", "proxy_ready"):
        files.peaks = file_url(user_id, asset_id, "peaks.json")
        files.analysis = file_url(user_id, asset_id, "analysis.json")
        if kind == "video":
            files.thumbs = file_url(user_id, asset_id, "thumbs.jpg")
            files.thumbs_meta = file_url(user_id, asset_id, "thumbs.json")
    if status == "proxy_ready":
        files.proxy = file_url(user_id, asset_id, "proxy.mp4" if kind == "video" else "proxy.m4a")
    return files


def asset_view(row: dict | sqlite3.Row) -> AssetView:
    has_audio = row["has_audio"]
    return AssetView(
        id=row["id"],
        kind=row["kind"],
        original_name=row["original_name"],
        size=row["size"],
        status=row["status"],
        duration=row["duration"],
        width=row["width"],
        height=row["height"],
        fps=row["fps"],
        has_audio=None if has_audio is None else bool(has_audio),
        video_codec=row["video_codec"],
        audio_codec=row["audio_codec"],
        error=row["error"],
        created_at=row["created_at"],
        last_access_at=row["last_access_at"],
        files=asset_files(row),
    )
```

- [ ] **Step 4: Маршруты ассетов**

Создать `server/app/assets/routes.py`:

```python
"""Ассеты: список, карточка, удаление, одноразовая загрузка мелких файлов (SRT, музыка до 64 МБ)."""
from __future__ import annotations

import shutil
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, UploadFile
from pydantic import BaseModel

from server.app.assets.views import AssetView, asset_view
from server.app.auth.deps import CurrentUser, current_user
from server.app.errors import ApiError
from server.app.jobs import cancel_jobs_for_target
from server.app.storage import asset_dir, upload_path
from server.app.uploads.routes import api_error
from server.app.uploads.store import UploadError, check_capacity, clean_filename, finalize_file, resolve_kind
from server.app.util import new_id
from server.db.core import get_db, transaction

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])

READ_PIECE = 1024 * 1024


class AssetList(BaseModel):
    assets: list[AssetView]


def get_asset(conn: sqlite3.Connection, user_id: str, asset_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM assets WHERE id = ? AND user_id = ?", (asset_id, user_id)).fetchone()


def _owned(conn: sqlite3.Connection, user: CurrentUser, asset_id: str) -> sqlite3.Row:
    row = get_asset(conn, user.id, asset_id)
    if row is None:
        raise ApiError(404, "not_found", "Ассет не найден")
    return row


@router.get("", response_model=AssetList)
def list_(
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AssetList:
    rows = conn.execute("SELECT * FROM assets WHERE user_id = ? ORDER BY created_at DESC, id", (user.id,))
    return AssetList(assets=[asset_view(r) for r in rows])


@router.post("/upload", status_code=201, response_model=AssetView)
async def upload_small(
    request: Request,
    file: UploadFile,
    kind: Annotated[str | None, Form()] = None,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AssetView:
    """Файл целиком одним запросом. Пишется во временный файл рядом с загрузками, дальше тот же finalize_file."""
    settings = request.app.state.settings
    if not request.app.state.upload_limiter.allow(user.id):
        raise ApiError(429, "rate_limited", "Слишком много новых загрузок, подождите час")
    tmp = upload_path(settings, new_id("tmp"))
    tmp.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        filename = clean_filename(file.filename or "")
        resolved = resolve_kind(filename, kind)
        with open(tmp, "wb") as out:
            while piece := await file.read(READ_PIECE):
                size += len(piece)
                if size > settings.small_upload_max_bytes:
                    raise UploadError(
                        413,
                        "too_large",
                        "Файл больше допустимого для одноразовой загрузки",
                        {"limit_bytes": settings.small_upload_max_bytes},
                    )
                out.write(piece)
        check_capacity(conn, settings, user.id, size)
        row = finalize_file(conn, settings, user_id=user.id, src=tmp, filename=filename, size=size, kind=resolved)
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
    return asset_view(_owned(conn, user, asset_id))


@router.delete("/{asset_id}", status_code=204)
def delete(
    asset_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    """Сначала запись, потом файлы: упавший процесс не оставит запись без файлов, папку подберёт janitor.
    Проверка «ассет стоит в незавершённом проекте» появится в M2 вместе с таблицей projects."""
    with transaction(conn):
        cur = conn.execute("DELETE FROM assets WHERE id = ? AND user_id = ?", (asset_id, user.id))
        if cur.rowcount == 0:
            raise ApiError(404, "not_found", "Ассет не найден")
        cancel_jobs_for_target(conn, asset_id)
    shutil.rmtree(asset_dir(request.app.state.settings, user.id, asset_id), ignore_errors=True)
    return Response(status_code=204)
```

- [ ] **Step 5: `/me` с квотой**

В `server/app/auth/routes.py` добавить импорты `from pydantic import BaseModel` и `from server.app.uploads.store import used_bytes`, модели перед `@me_router.get`:

```python
class Quota(BaseModel):
    used_bytes: int
    limit_bytes: int


class MeView(CurrentUser):
    quota: Quota
```

и заменить обработчик `me`:

```python
@me_router.get("/me", response_model=MeView)
def me(
    request: Request,
    response: Response,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> MeView:
    response.headers["Cache-Control"] = "no-store"
    limit = request.app.state.settings.user_quota_bytes
    return MeView(**user.model_dump(), quota=Quota(used_bytes=used_bytes(conn, user.id), limit_bytes=limit))
```

В `server/app/main.py`: `from server.app.assets.routes import router as assets_router` и `app.include_router(assets_router)` рядом с `uploads_router`.

- [ ] **Step 6: Прогнать всё**

Run: `uv run python -m pytest && uv run ruff check .`
Expected: PASS весь набор, включая три отложенных теста из Task 4. Если существующий тест `/me` сравнивает тело целиком, добавить в ожидание `quota`.

- [ ] **Step 7: Commit**

```bash
git add server/app/assets server/app/auth/routes.py server/app/main.py tests/test_assets_api.py
git commit -m "feat(assets): asset cards, list/delete, small upload, quota in /me"
```

---
### Task 6: Раздача файлов: `/files/...`, `/internal/authz`, Caddy `forward_auth`

**Files:**
- Create: `server/app/files.py`
- Modify: `server/app/main.py`, `deploy/Caddyfile`, `deploy/bootstrap.sh`, `deploy/deploy.sh`
- Test: `tests/test_files.py`, `tests/test_deploy_files.py`

- [ ] **Step 1: Тесты раздачи**

Создать `tests/test_files.py`:

```python
import sqlite3

OLD = "2026-01-01T00:00:00.000Z"


def _ready_video_asset(client, settings, user_id, peaks=b'{"rate": 50, "peaks": []}'):
    """Ассет-видео в статусе ready с peaks.json на диске (анализ в этом плане не запускается)."""
    r = client.post("/api/v1/assets/upload", files={"file": ("c.mp4", b"\0" * 10, "application/octet-stream")})
    assert r.status_code == 201, r.text
    asset_id = r.json()["id"]
    (settings.data_dir / user_id / "assets" / asset_id / "peaks.json").write_bytes(peaks)
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute("UPDATE assets SET status = 'ready', last_access_at = ? WHERE id = ?", (OLD, asset_id))
    conn.commit()
    conn.close()
    return asset_id


def _url(user_id, asset_id, name):
    return f"/files/{user_id}/assets/{asset_id}/{name}"


def test_serves_public_file_and_touches_last_access(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    asset_id = _ready_video_asset(client, settings, me["id"])
    url = client.get(f"/api/v1/assets/{asset_id}").json()["files"]["peaks"]
    assert url == _url(me["id"], asset_id, "peaks.json")
    r = client.get(url)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, max-age=3600"
    assert r.json() == {"rate": 50, "peaks": []}
    assert client.get(f"/api/v1/assets/{asset_id}").json()["last_access_at"] > OLD


def test_range_requests(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    asset_id = _ready_video_asset(client, settings, me["id"], peaks=b"0123456789")
    r = client.get(_url(me["id"], asset_id, "peaks.json"), headers={"Range": "bytes=2-4"})
    assert r.status_code == 206 and r.content == b"234"


def test_source_and_unknown_names_are_forbidden(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    asset_id = _ready_video_asset(client, settings, me["id"])
    for name in ("source.mp4", "evil.txt", "audio16k.wav"):
        r = client.get(_url(me["id"], asset_id, name))
        assert r.status_code == 403, name
        assert r.json()["error"]["code"] == "forbidden"


def test_missing_foreign_and_unknown_are_404(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    asset_id = _ready_video_asset(client, settings, me["id"])
    assert client.get(_url(me["id"], asset_id, "thumbs.jpg")).status_code == 404
    assert client.get(_url(me["id"], "ast_000000000000", "peaks.json")).status_code == 404
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(_url(me["id"], asset_id, "peaks.json")).status_code == 404


def test_files_require_auth(client):
    r = client.get(_url("usr_000000000000", "ast_000000000000", "peaks.json"))
    assert r.status_code == 401


def test_authz_for_caddy(client, login_as, settings, bearer_client):
    me = client.get("/api/v1/me").json()  # bearer_client уже выполнил login_as() для client
    asset_id = _ready_video_asset(client, settings, me["id"])
    ok = _url(me["id"], asset_id, "peaks.json")
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": ok}).status_code == 204
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": ok + "?t=1"}).status_code == 204
    assert bearer_client.get("/internal/authz", headers={"X-Forwarded-Uri": ok}).status_code == 204
    r = client.get("/internal/authz", headers={"X-Forwarded-Uri": _url(me["id"], asset_id, "source.mp4")})
    assert r.status_code == 403
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": "/files/x/y"}).status_code == 404
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": "/api/v1/me"}).status_code == 404
    assert client.get("/internal/authz").status_code == 404
    other = _url("usr_000000000000", asset_id, "peaks.json")
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": other}).status_code == 404
    assert client.get(f"/api/v1/assets/{asset_id}").json()["last_access_at"] > OLD


def test_authz_requires_auth(app):
    from starlette.testclient import TestClient

    with TestClient(app) as anon:
        r = anon.get("/internal/authz", headers={"X-Forwarded-Uri": "/files/a/assets/b/peaks.json"})
        assert r.status_code == 401
```

Добавить в `tests/test_deploy_files.py`:

```python
def test_caddyfile_serves_files_after_forward_auth_with_body_limits():
    caddy = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "handle /internal/*" in caddy and "respond 404" in caddy
    assert "forward_auth 127.0.0.1:8010" in caddy and "uri /internal/authz" in caddy
    assert caddy.index("forward_auth") < caddy.index("uri strip_prefix /files") < caddy.index("file_server")
    assert "root * /srv/video/data" in caddy
    assert "handle /api/v1/uploads/*/chunks/*" in caddy and "max_size 34MB" in caddy
    assert "handle /api/v1/assets/upload" in caddy and "max_size 68MB" in caddy


def test_caddy_user_joins_video_group():
    for name in ("bootstrap.sh", "deploy.sh"):
        assert "usermod -a -G video caddy" in (DEPLOY / name).read_text(encoding="utf-8"), name
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_files.py tests/test_deploy_files.py`
Expected: FAIL (404 на `/files/...`, нет `forward_auth` в Caddyfile).

- [ ] **Step 3: Модуль раздачи**

Создать `server/app/files.py`:

```python
"""Файлы ассетов наружу.

GET /files/... отдаёт само приложение (локально и в тестах). На VM тот же путь перехватывает Caddy:
forward_auth спрашивает GET /internal/authz, а файл отдаёт file_server с диска (Range и большие файлы
идут мимо Python). Правила доступа в обоих случаях одни: authorize_file.
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from server.app.auth.deps import CurrentUser, current_user
from server.app.config import Settings
from server.app.errors import ApiError
from server.app.storage import PUBLIC_FILES, asset_dir, parse_file_url
from server.app.util import iso, utcnow
from server.db.core import get_db

router = APIRouter(tags=["files"])

TOUCH_MIN_INTERVAL = timedelta(minutes=1)
FILE_CACHE = "private, max-age=3600"


def authorize_file(
    conn: sqlite3.Connection, settings: Settings, user: CurrentUser, user_id: str, asset_id: str, name: str
) -> Path:
    """Путь к файлу или ApiError: 403 для непубличных имён (source.*), 404 для чужого и несуществующего."""
    if name not in PUBLIC_FILES:
        raise ApiError(403, "forbidden", "Этот файл наружу не отдаётся")
    if user_id != user.id:
        raise ApiError(404, "not_found", "Файл не найден")
    row = conn.execute("SELECT id FROM assets WHERE id = ? AND user_id = ?", (asset_id, user_id)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Файл не найден")
    return asset_dir(settings, user_id, asset_id) / name


def touch_last_access(conn: sqlite3.Connection, asset_id: str) -> None:
    """Не чаще раза в минуту (раздел 3 спеки); сравнение в SQL, чтобы не писать в WAL на каждый запрос."""
    now = utcnow()
    conn.execute(
        "UPDATE assets SET last_access_at = ? WHERE id = ? AND last_access_at < ?",
        (iso(now), asset_id, iso(now - TOUCH_MIN_INTERVAL)),
    )


@router.get("/files/{user_id}/assets/{asset_id}/{name}", include_in_schema=False)
def serve_file(
    request: Request,
    user_id: str,
    asset_id: str,
    name: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> FileResponse:
    path = authorize_file(conn, request.app.state.settings, user, user_id, asset_id, name)
    if not path.is_file():
        raise ApiError(404, "not_found", "Файл ещё не готов")
    touch_last_access(conn, asset_id)
    return FileResponse(path, headers={"Cache-Control": FILE_CACHE})


@router.get("/internal/authz", include_in_schema=False)
def authz(
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    """Для Caddy forward_auth: любой 2xx разрешает отдать файл с диска. Путь приходит в X-Forwarded-Uri."""
    uri = request.headers.get("x-forwarded-uri", "").split("?", 1)[0]
    parsed = parse_file_url(uri)
    if parsed is None:
        raise ApiError(404, "not_found", "Файл не найден")
    user_id, asset_id, name = parsed
    authorize_file(conn, request.app.state.settings, user, user_id, asset_id, name)
    touch_last_access(conn, asset_id)
    return Response(status_code=204)
```

В `server/app/main.py`: `from server.app.files import router as files_router` и `app.include_router(files_router)` рядом с остальными (до catch-all и до статики).

- [ ] **Step 4: Caddyfile**

Заменить `deploy/Caddyfile` целиком:

```caddyfile
# Соседние сервисы на этой же VM (VideoBoard и другие) держат свои site-блоки в /etc/caddy/conf.d/*.caddy.
# Этот файл целиком переписывается нашим deploy.sh, импорт сохраняет чужие блоки. Пустой каталог не ошибка.
import /etc/caddy/conf.d/*.caddy

VIDEO_DOMAIN_PLACEHOLDER {
	encode zstd gzip
	header {
		X-Content-Type-Options nosniff
		X-Frame-Options DENY
		Referrer-Policy strict-origin-when-cross-origin
		-Server
		Strict-Transport-Security "max-age=31536000"
	}
	header /index.html Cache-Control no-cache
	header / Cache-Control no-cache

	# Служебные маршруты снаружи не видны: forward_auth ходит в 127.0.0.1:8010 напрямую, минуя этот блок.
	handle /internal/* {
		respond 404
	}

	# Файлы ассетов: право доступа проверяет API (/internal/authz), отдаёт с диска сам Caddy (Range, большие файлы).
	# route фиксирует порядок: сначала forward_auth с исходным путём, потом срез префикса и отдача с диска.
	# Caddy читает /srv/video/data как член группы video (bootstrap.sh, deploy.sh).
	handle /files/* {
		route {
			forward_auth 127.0.0.1:8010 {
				uri /internal/authz
			}
			uri strip_prefix /files
			root * /srv/video/data
			header Cache-Control "private, max-age=3600"
			file_server
		}
	}

	# Части загрузки: тело чуть больше части (32 МиБ = 33 554 432 байт).
	handle /api/v1/uploads/*/chunks/* {
		request_body {
			max_size 34MB
		}
		reverse_proxy 127.0.0.1:8010
	}

	# Одноразовая загрузка мелких файлов: 64 МиБ плюс обвязка multipart.
	handle /api/v1/assets/upload {
		request_body {
			max_size 68MB
		}
		reverse_proxy 127.0.0.1:8010
	}

	handle {
		request_body {
			max_size 1MB
		}
		reverse_proxy 127.0.0.1:8010
	}
}
```

- [ ] **Step 5: Группа video для Caddy**

В `deploy/bootstrap.sh` после блока создания пользователя `video` (после `chmod 750 ...`):

```bash
# Caddy отдаёт файлы ассетов с диска сам (file_server после forward_auth): читает /srv/video/data через группу video.
usermod -a -G video caddy
```

В `deploy/deploy.sh` перед блоком «Конфиги Caddy и systemd»:

```bash
# Существующие установки: Caddy должен состоять в группе video (bootstrap делает то же на чистой VM).
if ! id -nG caddy | tr ' ' '\n' | grep -qx video; then
  usermod -a -G video caddy
  systemctl restart caddy
fi
```

- [ ] **Step 6: Прогнать тесты**

Run: `uv run python -m pytest tests/test_files.py tests/test_deploy_files.py && uv run ruff check .`
Expected: PASS. Если `test_range_requests` даёт 200 вместо 206, значит установленный Starlette не режет по Range: записать в «Поправки» и заменить проверку на `r.status_code in (200, 206)`, на VM Range обслуживает Caddy.

- [ ] **Step 7: Commit**

```bash
git add server/app/files.py server/app/main.py deploy/Caddyfile deploy/bootstrap.sh deploy/deploy.sh tests/test_files.py tests/test_deploy_files.py
git commit -m "feat(files): serve asset files with ownership check; Caddy forward_auth + file_server; body limits"
```

---

### Task 7: Janitor: сроки жизни, сироты, зависшие задания, сессии, бэкап

**Files:**
- Create: `server/janitor/__init__.py` (пустой), `server/janitor/rules.py`, `server/janitor/__main__.py`
- Create: `deploy/video-janitor.service`, `deploy/video-janitor.timer`
- Modify: `deploy/deploy.sh`, `deploy/bootstrap.sh`
- Test: `tests/test_janitor.py`, `tests/test_deploy_files.py`

- [ ] **Step 1: Тесты правил**

Создать `tests/test_janitor.py`:

```python
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.storage import asset_dir
from server.app.uploads.store import create_upload, finalize_file
from server.app.util import iso, now_iso, utcnow
from server.db.migrate import migrate
from server.janitor import rules
from server.janitor.__main__ import run

USER = "usr_000000000001"
# Настоящее «сейчас», а не константа: mtime свежесозданных папок должен быть моложе часа относительно NOW.
NOW = utcnow()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", chunk_size=1024, session_idle_days=7)


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = sqlite3.connect(str(settings.db_path), isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    migrate(c)
    c.execute("INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)", (USER, now_iso()))
    yield c
    c.close()


def _asset(conn, settings, tmp_path, name="a.mp4", last_access=None):
    src = tmp_path / f"src-{name}"
    src.write_bytes(b"x" * 10)
    row = finalize_file(conn, settings, user_id=USER, src=src, filename=name, size=10, kind="video")
    if last_access:
        conn.execute("UPDATE assets SET last_access_at = ? WHERE id = ?", (iso(last_access), row["id"]))
    return row["id"]


def _age(path: Path, hours: float) -> None:
    ts = (NOW - timedelta(hours=hours)).timestamp()
    os.utime(path, (ts, ts))


def test_expired_uploads_are_deleted_with_files(conn, settings):
    old = create_upload(conn, settings, USER, filename="old.mp4", size=100, kind=None)
    fresh = create_upload(conn, settings, USER, filename="new.mp4", size=100, kind=None)
    conn.execute("UPDATE uploads SET expires_at = ? WHERE id = ?", (iso(NOW - timedelta(minutes=1)), old["id"]))
    assert rules.delete_expired_uploads(conn, NOW) == 1
    assert not Path(old["path"]).exists() and Path(fresh["path"]).exists()
    assert [r[0] for r in conn.execute("SELECT id FROM uploads")] == [fresh["id"]]


def test_expired_assets_are_deleted_and_jobs_canceled(conn, settings, tmp_path):
    old = _asset(conn, settings, tmp_path, "old.mp4", last_access=NOW - timedelta(hours=25))
    fresh = _asset(conn, settings, tmp_path, "new.mp4", last_access=NOW - timedelta(hours=23))
    assert rules.delete_expired_assets(conn, settings, NOW) == 1
    assert not asset_dir(settings, USER, old).exists() and asset_dir(settings, USER, fresh).exists()
    statuses = dict(conn.execute("SELECT target_id, status FROM jobs").fetchall())
    assert statuses == {old: "canceled", fresh: "queued"}


def test_orphan_dirs_and_files_older_than_an_hour(conn, settings, tmp_path):
    kept = _asset(conn, settings, tmp_path)
    orphan_old = settings.data_dir / USER / "assets" / "ast_00000000dead"
    orphan_old.mkdir(parents=True)
    (orphan_old / "source.mp4").write_bytes(b"x")
    _age(orphan_old, 2)
    orphan_young = settings.data_dir / USER / "assets" / "ast_0000000young"
    orphan_young.mkdir()
    settings.uploads_tmp_path.mkdir(parents=True, exist_ok=True)
    stray = settings.uploads_tmp_path / "upl_000000000bad"
    stray.write_bytes(b"x")
    _age(stray, 2)
    live = create_upload(conn, settings, USER, filename="live.mp4", size=10, kind=None)
    _age(Path(live["path"]), 2)
    assert rules.delete_orphans(conn, settings, NOW) == 2
    assert not orphan_old.exists() and orphan_young.exists() and not stray.exists()
    assert asset_dir(settings, USER, kept).exists() and Path(live["path"]).exists()


def test_stale_running_jobs_requeue_once_then_fail(conn, settings, tmp_path):
    asset = _asset(conn, settings, tmp_path)
    first = conn.execute("SELECT id FROM jobs").fetchone()[0]
    stale = iso(NOW - timedelta(minutes=3))
    conn.execute(
        "UPDATE jobs SET status = 'running', attempts = 1, started_at = ?, heartbeat_at = ? WHERE id = ?",
        (stale, stale, first),
    )
    conn.execute("UPDATE assets SET status = 'analyzing' WHERE id = ?", (asset,))
    second = enqueue_job(conn, user_id=USER, type_="proxy", target_id=asset)
    conn.execute(
        "UPDATE jobs SET status = 'running', attempts = 2, started_at = ?, heartbeat_at = ? WHERE id = ?",
        (stale, stale, second),
    )
    alive = enqueue_job(conn, user_id=USER, type_="proxy", target_id=asset)
    conn.execute(
        "UPDATE jobs SET status = 'running', attempts = 1, started_at = ?, heartbeat_at = ? WHERE id = ?",
        (stale, iso(NOW - timedelta(seconds=30)), alive),
    )
    assert rules.requeue_stale_jobs(conn, NOW) == (1, 1)
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM jobs")}
    assert rows[first]["status"] == "queued" and rows[first]["heartbeat_at"] is None
    assert rows[second]["status"] == "failed" and "воркер" in rows[second]["error"]
    assert rows[alive]["status"] == "running"
    assert conn.execute("SELECT status FROM assets WHERE id = ?", (asset,)).fetchone()[0] == "analyzing"


def test_failed_analyze_marks_asset_failed(conn, settings, tmp_path):
    asset = _asset(conn, settings, tmp_path)
    job = conn.execute("SELECT id FROM jobs").fetchone()[0]
    stale = iso(NOW - timedelta(minutes=3))
    conn.execute(
        "UPDATE jobs SET status = 'running', attempts = 2, started_at = ?, heartbeat_at = ? WHERE id = ?",
        (stale, stale, job),
    )
    conn.execute("UPDATE assets SET status = 'analyzing' WHERE id = ?", (asset,))
    assert rules.requeue_stale_jobs(conn, NOW) == (0, 1)
    row = conn.execute("SELECT status, error FROM assets WHERE id = ?", (asset,)).fetchone()
    assert row["status"] == "failed" and row["error"]


def test_expired_sessions(conn, settings):
    def add(sid, last_seen, absolute):
        conn.execute(
            "INSERT INTO sessions (id, user_id, created_at, last_seen_at, absolute_expires_at) VALUES (?, ?, ?, ?, ?)",
            (sid, USER, iso(NOW - timedelta(days=40)), iso(last_seen), iso(absolute)),
        )

    add("s_idle", NOW - timedelta(days=8), NOW + timedelta(days=1))
    add("s_absolute", NOW - timedelta(hours=1), NOW - timedelta(minutes=1))
    add("s_live", NOW - timedelta(hours=1), NOW + timedelta(days=1))
    assert rules.delete_expired_sessions(conn, settings, NOW) == 2
    assert [r[0] for r in conn.execute("SELECT id FROM sessions")] == ["s_live"]


def test_backup_once_a_day_keeps_seven(conn, settings):
    backups = settings.data_dir / "backups"
    backups.mkdir()
    for d in range(1, 10):
        (backups / f"video-202608{d:02d}.db").write_bytes(b"")
    made = rules.backup_if_due(settings, NOW)
    assert made == backups / f"video-{NOW:%Y%m%d}.db"
    check = sqlite3.connect(str(made))
    assert check.execute("SELECT count(*) FROM users").fetchone()[0] == 1
    check.close()
    assert rules.backup_if_due(settings, NOW) is None
    assert len(list(backups.glob("video-*.db"))) == 7
    assert made.exists() and not (backups / "video-20260801.db").exists()


def test_run_returns_stats(conn, settings):
    conn.close()
    stats = run(settings, NOW)
    assert stats == {
        "uploads_expired": 0,
        "assets_expired": 0,
        "orphans": 0,
        "sessions_expired": 0,
        "jobs_requeued": 0,
        "jobs_failed": 0,
        "backup": 1,
    }
```

Добавить в `tests/test_deploy_files.py`:

```python
def test_janitor_units_and_install():
    unit = (DEPLOY / "video-janitor.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in unit and "python -m server.janitor" in unit and "User=video" in unit
    assert "ProtectSystem=strict" in unit and "ReadWritePaths=/srv/video" in unit
    timer = (DEPLOY / "video-janitor.timer").read_text(encoding="utf-8")
    assert "OnCalendar=hourly" in timer and "Persistent=true" in timer
    for name in ("bootstrap.sh", "deploy.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "video-janitor.timer" in text and "video-janitor.service" in text, name
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_janitor.py tests/test_deploy_files.py`
Expected: FAIL, нет модуля `server.janitor`.

- [ ] **Step 3: Правила**

Создать пустой `server/janitor/__init__.py` и `server/janitor/rules.py`:

```python
"""Правила очистки. Каждая функция делает одно действие и возвращает счётчик; журнал пишет вызывающий.
Порядок «сначала запись, потом файлы» (раздел 6.3 спеки): упавший процесс не оставляет записи без файлов.
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.config import Settings
from server.app.jobs import cancel_jobs_for_target
from server.app.storage import asset_dir
from server.app.util import iso
from server.db.core import connect, transaction

ORPHAN_MIN_AGE_SEC = 3600  # моложе часа не трогаем: загрузка может завершаться прямо сейчас
JOB_STALE_AFTER_SEC = 120
JOB_MAX_ATTEMPTS = 2
BACKUP_KEEP = 7
WORKER_LOST = "воркер пропал без вести (нет пульса дольше 2 минут)"


def delete_expired_uploads(conn: sqlite3.Connection, now: datetime) -> int:
    rows = conn.execute("SELECT id, path FROM uploads WHERE expires_at < ?", (iso(now),)).fetchall()
    for row in rows:
        with transaction(conn):
            conn.execute("DELETE FROM uploads WHERE id = ?", (row["id"],))
        Path(row["path"]).unlink(missing_ok=True)
    return len(rows)


def delete_expired_assets(conn: sqlite3.Connection, settings: Settings, now: datetime) -> int:
    cutoff = iso(now - timedelta(hours=settings.asset_ttl_hours))
    rows = conn.execute("SELECT id, user_id FROM assets WHERE last_access_at < ?", (cutoff,)).fetchall()
    for row in rows:
        with transaction(conn):
            conn.execute("DELETE FROM assets WHERE id = ?", (row["id"],))
            cancel_jobs_for_target(conn, row["id"])
        shutil.rmtree(asset_dir(settings, row["user_id"], row["id"]), ignore_errors=True)
    return len(rows)


def _older_than(path: Path, now: datetime, seconds: int) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return False
    return (now - mtime).total_seconds() > seconds


def delete_orphans(conn: sqlite3.Connection, settings: Settings, now: datetime) -> int:
    """Папки ассетов без записи и файлы загрузок без записи, старше часа."""
    count = 0
    known_assets = {r[0] for r in conn.execute("SELECT id FROM assets")}
    for assets_root in settings.data_dir.glob("usr_*/assets"):
        for d in assets_root.iterdir():
            if d.is_dir() and d.name not in known_assets and _older_than(d, now, ORPHAN_MIN_AGE_SEC):
                shutil.rmtree(d, ignore_errors=True)
                count += 1
    known_uploads = {r[0] for r in conn.execute("SELECT id FROM uploads")}
    if settings.uploads_tmp_path.is_dir():
        for f in settings.uploads_tmp_path.iterdir():
            if f.is_file() and f.name not in known_uploads and _older_than(f, now, ORPHAN_MIN_AGE_SEC):
                f.unlink(missing_ok=True)
                count += 1
    return count


def requeue_stale_jobs(conn: sqlite3.Connection, now: datetime) -> tuple[int, int]:
    """Задание в running без пульса дольше 2 минут: один раз назад в очередь, затем failed.
    Упавший analyze переводит ассет в failed, чтобы он не висел в analyzing вечно."""
    cutoff = iso(now - timedelta(seconds=JOB_STALE_AFTER_SEC))
    rows = conn.execute(
        "SELECT id, type, target_id, attempts FROM jobs "
        "WHERE status = 'running' AND coalesce(heartbeat_at, started_at, created_at) < ?",
        (cutoff,),
    ).fetchall()
    requeued = failed = 0
    with transaction(conn):
        for row in rows:
            if row["attempts"] < JOB_MAX_ATTEMPTS:
                conn.execute(
                    "UPDATE jobs SET status = 'queued', worker_pid = NULL, heartbeat_at = NULL, "
                    "started_at = NULL, progress = 0 WHERE id = ?",
                    (row["id"],),
                )
                requeued += 1
            else:
                conn.execute(
                    "UPDATE jobs SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
                    (iso(now), WORKER_LOST, row["id"]),
                )
                if row["type"] == "analyze":
                    conn.execute(
                        "UPDATE assets SET status = 'failed', error = ? "
                        "WHERE id = ? AND status IN ('uploaded', 'analyzing')",
                        (WORKER_LOST, row["target_id"]),
                    )
                failed += 1
    return requeued, failed


def delete_expired_sessions(conn: sqlite3.Connection, settings: Settings, now: datetime) -> int:
    idle_cutoff = iso(now - timedelta(days=settings.session_idle_days))
    cur = conn.execute(
        "DELETE FROM sessions WHERE absolute_expires_at < ? OR last_seen_at < ?", (iso(now), idle_cutoff)
    )
    return cur.rowcount


def backup_if_due(settings: Settings, now: datetime, keep: int = BACKUP_KEEP) -> Path | None:
    """Копия базы раз в сутки в data/backups/video-YYYYMMDD.db через sqlite backup API; хранится keep штук.
    Копия вне VM (scp на ПК) остаётся ручной операцией."""
    backups = settings.data_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    target = backups / f"video-{now:%Y%m%d}.db"
    if target.exists():
        return None
    src = connect(settings.db_path)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(backups.glob("video-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return target
```

- [ ] **Step 4: Точка входа**

Создать `server/janitor/__main__.py`:

```python
"""Janitor: python -m server.janitor. Раз в час по systemd-таймеру (deploy/video-janitor.timer):
сроки жизни загрузок и ассетов, сироты на диске, зависшие задания, просроченные сессии, суточный бэкап базы.
Миграции не применяет: это делает deploy.sh до перезапуска сервисов.
"""
from __future__ import annotations

import logging
from datetime import datetime

from server.app.config import Settings
from server.app.main import configure_logging
from server.app.util import utcnow
from server.db.core import connect
from server.janitor import rules

log = logging.getLogger("video.janitor")


def run(settings: Settings, now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    conn = connect(settings.db_path)
    try:
        stats = {
            "uploads_expired": rules.delete_expired_uploads(conn, now),
            "assets_expired": rules.delete_expired_assets(conn, settings, now),
            "orphans": rules.delete_orphans(conn, settings, now),
            "sessions_expired": rules.delete_expired_sessions(conn, settings, now),
        }
        stats["jobs_requeued"], stats["jobs_failed"] = rules.requeue_stale_jobs(conn, now)
    finally:
        conn.close()
    stats["backup"] = 1 if rules.backup_if_due(settings, now) else 0
    return stats


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    stats = run(settings)
    log.info("janitor: %s", " ".join(f"{k}={v}" for k, v in stats.items()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Юниты и установка**

Создать `deploy/video-janitor.service`:

```ini
[Unit]
Description=Editing site janitor (сроки жизни файлов, зависшие задания, бэкап базы)
After=network-online.target

[Service]
Type=oneshot
User=video
Group=video
WorkingDirectory=/opt/editing-site
EnvironmentFile=/opt/editing-site/.env
ExecStart=/opt/editing-site/.venv/bin/python -m server.janitor
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/video
```

Создать `deploy/video-janitor.timer`:

```ini
[Unit]
Description=Editing site janitor, раз в час

[Timer]
OnCalendar=hourly
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
```

В `deploy/deploy.sh` заменить установку юнита:

```bash
install -m 644 "$APP_DIR/deploy/video-api.service" /etc/systemd/system/video-api.service
install -m 644 "$APP_DIR/deploy/video-janitor.service" /etc/systemd/system/video-janitor.service
install -m 644 "$APP_DIR/deploy/video-janitor.timer" /etc/systemd/system/video-janitor.timer
systemctl daemon-reload
systemctl enable --now video-janitor.timer
systemctl restart video-api
```

В `deploy/bootstrap.sh` после `install -m 644 ... video-api.service`:

```bash
install -m 644 "$APP_DIR/deploy/video-janitor.service" /etc/systemd/system/video-janitor.service
install -m 644 "$APP_DIR/deploy/video-janitor.timer" /etc/systemd/system/video-janitor.timer
```

и в `systemctl enable caddy video-api` добавить `video-janitor.timer`.

- [ ] **Step 6: Прогнать всё**

Run: `uv run python -m pytest && uv run ruff check .`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add server/janitor deploy/video-janitor.service deploy/video-janitor.timer deploy/deploy.sh deploy/bootstrap.sh tests/test_janitor.py tests/test_deploy_files.py
git commit -m "feat(janitor): ttl cleanup, orphans, stale jobs, expired sessions, daily db backup; hourly timer"
```

---
### Task 8: Фронтенд: загрузка с докачкой и панель ассетов

**Files:**
- Create: `web/src/upload.ts`, `web/src/upload.test.ts`, `web/src/assets.ts`, `web/src/assets.test.ts`
- Modify: `web/src/main.ts`, `web/src/style.css`

- [ ] **Step 1: Тесты загрузчика**

Создать `web/src/upload.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { ApiError } from './api'
import { backoffMs, chunkCount, fingerprint, isRetryable, missingChunks, uploadFile } from './upload'

describe('chunk math', () => {
  it('counts chunks', () => {
    expect(chunkCount(2500, 1024)).toBe(3)
    expect(chunkCount(1024, 1024)).toBe(1)
    expect(chunkCount(1, 1024)).toBe(1)
  })
  it('lists missing chunks in order', () => {
    expect(missingChunks(4, [0, 2])).toEqual([1, 3])
    expect(missingChunks(2, [])).toEqual([0, 1])
    expect(missingChunks(2, [1, 0])).toEqual([])
  })
  it('fingerprint and backoff', () => {
    expect(fingerprint({ name: 'a.mp4', size: 5, lastModified: 7 })).toBe('upload:a.mp4:5:7')
    expect(backoffMs(0)).toBe(1000)
    expect(backoffMs(2)).toBe(4000)
  })
  it('retries only on network errors, 5xx and 429', () => {
    expect(isRetryable(new Error('net'))).toBe(true)
    expect(isRetryable(new ApiError(503, 'x', 'x'))).toBe(true)
    expect(isRetryable(new ApiError(429, 'x', 'x'))).toBe(true)
    expect(isRetryable(new ApiError(422, 'x', 'x'))).toBe(false)
    expect(isRetryable(new ApiError(401, 'x', 'x'))).toBe(false)
  })
})

function fakeFile(size: number) {
  const bytes = new Uint8Array(size).map((_, i) => i % 251)
  return { name: 'f.bin', size, lastModified: 1, slice: (s: number, e: number) => new Blob([bytes.slice(s, e)]) }
}

function memStorage() {
  const map = new Map<string, string>()
  return {
    map,
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
  }
}

const noSleep = async () => {}

describe('uploadFile', () => {
  it('creates, sends every chunk, completes and clears the resume key', async () => {
    const calls: string[] = []
    const request = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      calls.push(`${init.method ?? 'GET'} ${path}`)
      if (path === '/api/v1/uploads' && init.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({ filename: 'f.bin', size: 10 })
        return { upload_id: 'upl_1', chunk_size: 4, total_chunks: 3, expires_at: 'x' } as T
      }
      if (path.endsWith('/complete')) return { asset_id: 'ast_1', status: 'uploaded' } as T
      return undefined as T
    }
    const storage = memStorage()
    const progress: number[] = []
    const res = await uploadFile(fakeFile(10), { request, storage, sleep: noSleep, onProgress: d => progress.push(d) })
    expect(res.asset_id).toBe('ast_1')
    expect(calls.filter(c => c.startsWith('PUT')).sort()).toEqual([
      'PUT /api/v1/uploads/upl_1/chunks/0',
      'PUT /api/v1/uploads/upl_1/chunks/1',
      'PUT /api/v1/uploads/upl_1/chunks/2',
    ])
    expect(progress[0]).toBe(0)
    expect(progress.at(-1)).toBe(10)
    expect(storage.map.size).toBe(0)
  })

  it('resumes a saved upload and sends only the missing chunks', async () => {
    const storage = memStorage()
    storage.setItem('upload:f.bin:10:1', 'upl_9')
    const puts: string[] = []
    const request = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      if (path === '/api/v1/uploads/upl_9' && !init.method) {
        return { upload_id: 'upl_9', received: [0, 2], total: 3, size: 10, chunk_size: 4 } as T
      }
      if (init.method === 'PUT') {
        puts.push(path)
        return undefined as T
      }
      if (path.endsWith('/complete')) return { asset_id: 'ast_2', status: 'uploaded' } as T
      throw new Error('unexpected ' + path)
    }
    await uploadFile(fakeFile(10), { request, storage, sleep: noSleep })
    expect(puts).toEqual(['/api/v1/uploads/upl_9/chunks/1'])
  })

  it('starts over when the saved upload is gone and retries a failing chunk', async () => {
    const storage = memStorage()
    storage.setItem('upload:f.bin:10:1', 'upl_old')
    let failures = 0
    const request = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      if (path === '/api/v1/uploads/upl_old') throw new ApiError(404, 'not_found', 'нет')
      if (path === '/api/v1/uploads' && init.method === 'POST') {
        return { upload_id: 'upl_new', chunk_size: 4, total_chunks: 3, expires_at: 'x' } as T
      }
      if (init.method === 'PUT' && path.endsWith('/chunks/1') && failures++ < 2) throw new ApiError(503, 'x', 'busy')
      if (path.endsWith('/complete')) return { asset_id: 'ast_3', status: 'uploaded' } as T
      return undefined as T
    }
    const res = await uploadFile(fakeFile(10), { request, storage, sleep: noSleep })
    expect(res.asset_id).toBe('ast_3')
    expect(failures).toBe(2)
  })

  it('gives up on a 4xx and keeps the resume key', async () => {
    const storage = memStorage()
    const request = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      if (init.method === 'POST' && path === '/api/v1/uploads') {
        return { upload_id: 'u', chunk_size: 4, total_chunks: 3, expires_at: 'x' } as T
      }
      if (init.method === 'PUT') throw new ApiError(422, 'chunk_size_mismatch', 'bad')
      return undefined as T
    }
    await expect(uploadFile(fakeFile(10), { request, storage, sleep: noSleep })).rejects.toThrow('bad')
    expect(storage.map.get('upload:f.bin:10:1')).toBe('u')
  })

  it('rejects an empty file before touching the network', async () => {
    const request = async <T,>(): Promise<T> => {
      throw new Error('should not be called')
    }
    await expect(uploadFile(fakeFile(0), { request, storage: memStorage(), sleep: noSleep })).rejects.toThrow('Пустой')
  })
})
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `cd web && npm test`
Expected: FAIL, нет модуля `./upload`.

- [ ] **Step 3: Загрузчик**

Создать `web/src/upload.ts`:

```ts
import { api, ApiError } from './api'

export type UploadCreated = { upload_id: string; chunk_size: number; total_chunks: number; expires_at: string }
export type UploadStatus = { upload_id: string; received: number[]; total: number; size: number; chunk_size: number }
export type UploadResult = { asset_id: string; status: string }
export type FileLike = { name: string; size: number; lastModified: number; slice(start: number, end: number): Blob }
type Storage = { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void }
export type UploadOptions = {
  onProgress?: (doneBytes: number, totalBytes: number) => void
  parallel?: number
  retries?: number
  storage?: Storage
  request?: typeof api
  sleep?: (ms: number) => Promise<void>
}

export const PARALLEL = 3 // спека, раздел 6.1
export const RETRIES = 3

export function chunkCount(size: number, chunkSize: number): number {
  return Math.max(1, Math.ceil(size / chunkSize))
}

export function missingChunks(total: number, received: number[]): number[] {
  const got = new Set(received)
  const out: number[] = []
  for (let i = 0; i < total; i++) if (!got.has(i)) out.push(i)
  return out
}

/** Ключ докачки в localStorage: имя, размер и дата изменения файла. */
export function fingerprint(f: { name: string; size: number; lastModified: number }): string {
  return `upload:${f.name}:${f.size}:${f.lastModified}`
}

export function backoffMs(attempt: number): number {
  return 1000 * 2 ** attempt
}

/** Повторяем только сбои сети, 5xx и 429: ошибка 4xx не исправится сама. */
export function isRetryable(e: unknown): boolean {
  if (e instanceof ApiError) return e.status >= 500 || e.status === 429
  return true
}

function defaultStorage(): Storage | undefined {
  try {
    return localStorage
  } catch {
    return undefined
  }
}

type Session = { id: string; chunkSize: number; total: number; received: number[] }

async function resumeOrCreate(file: FileLike, request: typeof api, storage?: Storage): Promise<Session> {
  const key = fingerprint(file)
  const saved = storage?.getItem(key)
  if (saved) {
    try {
      const st = await request<UploadStatus>(`/api/v1/uploads/${saved}`)
      if (st.size === file.size) return { id: st.upload_id, chunkSize: st.chunk_size, total: st.total, received: st.received }
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) throw e
    }
    storage?.removeItem(key)
  }
  const created = await request<UploadCreated>('/api/v1/uploads', {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, size: file.size }),
  })
  storage?.setItem(key, created.upload_id)
  return { id: created.upload_id, chunkSize: created.chunk_size, total: created.total_chunks, received: [] }
}

/** Загрузка по частям с докачкой: до PARALLEL частей одновременно, повтор с задержкой, продолжение после перезагрузки. */
export async function uploadFile(file: FileLike, opts: UploadOptions = {}): Promise<UploadResult> {
  if (file.size <= 0) throw new Error('Пустой файл')
  const request = opts.request ?? api
  const sleep = opts.sleep ?? (ms => new Promise<void>(r => setTimeout(r, ms)))
  const retries = opts.retries ?? RETRIES
  const storage = opts.storage ?? defaultStorage()
  const up = await resumeOrCreate(file, request, storage)
  const queue = missingChunks(up.total, up.received)
  let done = up.received.length
  const report = () => opts.onProgress?.(Math.min(file.size, done * up.chunkSize), file.size)
  report()

  const worker = async () => {
    for (let idx = queue.shift(); idx !== undefined; idx = queue.shift()) {
      const body = file.slice(idx * up.chunkSize, Math.min(file.size, (idx + 1) * up.chunkSize))
      for (let attempt = 0; ; attempt++) {
        try {
          await request(`/api/v1/uploads/${up.id}/chunks/${idx}`, {
            method: 'PUT',
            body,
            headers: { 'Content-Type': 'application/octet-stream' },
          })
          break
        } catch (e) {
          if (attempt >= retries || !isRetryable(e)) throw e
          await sleep(backoffMs(attempt))
        }
      }
      done++
      report()
    }
  }
  const workers = Math.min(opts.parallel ?? PARALLEL, Math.max(1, queue.length))
  await Promise.all(Array.from({ length: workers }, worker))

  const result = await request<UploadResult>(`/api/v1/uploads/${up.id}/complete`, { method: 'POST' })
  storage?.removeItem(fingerprint(file))
  return result
}
```

- [ ] **Step 4: Тесты панели**

Создать `web/src/assets.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { fmtDuration, fmtSize, needsPolling, statusText } from './assets'

describe('assets helpers', () => {
  it('formats sizes', () => {
    expect(fmtSize(0)).toBe('0 Б')
    expect(fmtSize(1536)).toBe('1.5 КБ')
    expect(fmtSize(5 * 1024 ** 3)).toBe('5.0 ГБ')
  })
  it('formats durations', () => {
    expect(fmtDuration(null)).toBe('—')
    expect(fmtDuration(65.4)).toBe('1:05')
    expect(fmtDuration(3725)).toBe('1:02:05')
  })
  it('names statuses in russian and knows which are final', () => {
    expect(statusText('uploaded')).toBe('загружен, ждёт анализа')
    expect(statusText('proxy_ready')).toBe('готов')
    expect(statusText('weird')).toBe('weird')
    expect(needsPolling([{ status: 'ready' }, { status: 'proxy_ready' }, { status: 'failed' }])).toBe(false)
    expect(needsPolling([{ status: 'ready' }, { status: 'analyzing' }])).toBe(true)
  })
})
```

- [ ] **Step 5: Панель ассетов**

Создать `web/src/assets.ts`:

```ts
import { api, ApiError } from './api'
import { escapeHtml } from './html'
import { uploadFile } from './upload'

export type Asset = {
  id: string
  kind: string
  original_name: string
  size: number
  status: string
  duration: number | null
  error: string | null
  files: { proxy: string | null }
}

const STATUS: Record<string, string> = {
  uploaded: 'загружен, ждёт анализа',
  analyzing: 'анализ',
  ready: 'звук и полоска готовы, прокси в работе',
  proxy_ready: 'готов',
  failed: 'ошибка',
}
const FINAL = new Set(['ready', 'proxy_ready', 'failed'])
const POLL_MS = 3000

export function fmtSize(bytes: number): string {
  const units = ['Б', 'КБ', 'МБ', 'ГБ']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return i === 0 ? `${v} ${units[i]}` : `${v.toFixed(1)} ${units[i]}`
}

export function fmtDuration(sec: number | null): string {
  if (sec === null || !Number.isFinite(sec)) return '—'
  const s = Math.floor(sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  const mm = h ? String(m).padStart(2, '0') : String(m)
  return `${h ? h + ':' : ''}${mm}:${String(r).padStart(2, '0')}`
}

export function statusText(status: string): string {
  return STATUS[status] ?? status
}

export function needsPolling(assets: { status: string }[]): boolean {
  return assets.some(a => !FINAL.has(a.status))
}

function row(a: Asset): string {
  const cls = a.status === 'failed' ? ' class="status-failed"' : ''
  const err = a.error ? ` <span class="muted">${escapeHtml(a.error)}</span>` : ''
  return `<tr>
    <td>${escapeHtml(a.original_name)}</td><td>${escapeHtml(a.kind)}</td><td>${fmtSize(a.size)}</td>
    <td>${fmtDuration(a.duration)}</td><td${cls}>${escapeHtml(statusText(a.status))}${err}</td>
    <td><button data-delete="${escapeHtml(a.id)}">Удалить</button></td></tr>`
}

/** Панель ассетов: загрузка файлов, список со статусами (опрос раз в 3 с, пока идёт обработка), удаление. */
export function mountAssets(el: HTMLElement): { refresh: () => Promise<void> } {
  el.innerHTML = `
    <main class="card">
      <h2>Файлы</h2>
      <p class="muted">До 5 ГБ на файл. Загрузка продолжится с места разрыва, если выбрать тот же файл снова.</p>
      <input id="asset-files" type="file" multiple />
      <div id="asset-progress"></div>
      <table>
        <thead><tr><th>Имя</th><th>Вид</th><th>Размер</th><th>Длина</th><th>Статус</th><th></th></tr></thead>
        <tbody id="asset-rows"><tr><td colspan="6">Пока пусто</td></tr></tbody>
      </table>
      <pre id="assets-error" hidden></pre>
    </main>`
  const rows = el.querySelector('#asset-rows') as HTMLElement
  const progress = el.querySelector('#asset-progress') as HTMLElement
  const errorBox = el.querySelector('#assets-error') as HTMLPreElement
  let timer: number | undefined

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  const refresh = async () => {
    const { assets } = await api<{ assets: Asset[] }>('/api/v1/assets')
    rows.innerHTML = assets.map(row).join('') || '<tr><td colspan="6">Пока пусто</td></tr>'
    rows.querySelectorAll<HTMLButtonElement>('button[data-delete]').forEach(b =>
      b.addEventListener('click', async () => {
        try {
          await api(`/api/v1/assets/${b.dataset.delete}`, { method: 'DELETE' })
          await refresh()
        } catch (e) {
          showError(e)
        }
      }),
    )
    window.clearTimeout(timer)
    if (needsPolling(assets)) timer = window.setTimeout(() => void refresh().catch(showError), POLL_MS)
  }

  const input = el.querySelector('#asset-files') as HTMLInputElement
  input.addEventListener('change', async () => {
    const files = Array.from(input.files ?? [])
    input.value = ''
    for (const file of files) {
      const line = document.createElement('div')
      line.innerHTML = `<span>${escapeHtml(file.name)}</span><div class="progress"><i style="width:0%"></i></div>`
      progress.appendChild(line)
      const bar = line.querySelector('i') as HTMLElement
      try {
        await uploadFile(file, { onProgress: (d, t) => (bar.style.width = `${Math.round((d / t) * 100)}%`) })
        line.remove()
        await refresh()
      } catch (e) {
        line.querySelector('span')!.textContent = `${file.name}: не загружен`
        showError(e)
      }
    }
  })

  void refresh().catch(showError)
  return { refresh }
}
```

- [ ] **Step 6: Подключить в `main.ts` и стили**

В `web/src/main.ts`: импорт `import { mountAssets } from './assets'`; тип `Me` дополнить полем `quota: { used_bytes: number; limit_bytes: number }`; в `renderSettings` перед `<main class="card">` с токенами вставить `<section id="assets"></section>`, в шапке после почты показать квоту:

```ts
<span>${escapeHtml(me.email)} · ${fmtSize(me.quota.used_bytes)} из ${fmtSize(me.quota.limit_bytes)}</span>
```

(`fmtSize` импортировать из `./assets`), а после установки `root.innerHTML` вызвать `mountAssets(document.getElementById('assets') as HTMLElement)`.

В `web/src/style.css` добавить:

```css
.progress { height: 6px; margin: 4px 0 8px; background: #8883; border-radius: 3px; }
.progress > i { display: block; height: 100%; background: #3a7d5c; border-radius: 3px; transition: width .2s; }
.status-failed { color: #b3261e; }
.muted { opacity: .7; }
```

- [ ] **Step 7: Тесты и сборка**

Run: `cd web && npm test && npm run build`
Expected: vitest зелёный (было 5 тестов, станет 13), `tsc` и `vite build` без ошибок.

- [ ] **Step 8: Commit**

```bash
git add web/src/upload.ts web/src/upload.test.ts web/src/assets.ts web/src/assets.test.ts web/src/main.ts web/src/style.css
git commit -m "feat(web): resumable chunked upload and assets panel"
```

---

### Task 9: Документация, справочный клиент загрузки, деплой и живая проверка

**Files:**
- Create: `tools/upload_file.py`
- Modify: `README.md`, `docs/superpowers/plans/2026-09-04-m1a-uploads-assets.md` (раздел «Поправки»)
- Живая проверка на VM (руками из основной сессии, не субагентом)

- [ ] **Step 1: Справочный клиент протокола**

Создать `tools/upload_file.py` (stdlib, без зависимостей; тот же протокол, что и браузер, для агентов и смоука):

```python
"""Загрузка файла по частям через API: python tools/upload_file.py <base_url> <token> <file>
Докачка: при повторном запуске с тем же файлом передайте --upload-id из прошлого вывода.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def call(base: str, token: str, method: str, path: str, body: bytes | None = None, ctype: str | None = None) -> dict:
    req = urllib.request.Request(base + path, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {path}: HTTP {exc.code} {detail}") from exc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("token")
    ap.add_argument("file", type=Path)
    ap.add_argument("--upload-id", help="продолжить незавершённую загрузку")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    size = args.file.stat().st_size
    if args.upload_id:
        st = call(base, args.token, "GET", f"/api/v1/uploads/{args.upload_id}")
        upload_id, chunk_size, total, received = st["upload_id"], st["chunk_size"], st["total"], set(st["received"])
    else:
        created = call(
            base, args.token, "POST", "/api/v1/uploads",
            json.dumps({"filename": args.file.name, "size": size}).encode(), "application/json",
        )
        upload_id, chunk_size, total, received = created["upload_id"], created["chunk_size"], created["total_chunks"], set()
    print(f"upload_id={upload_id} chunks={total} chunk_size={chunk_size}", file=sys.stderr)
    with open(args.file, "rb") as f:
        for idx in range(total):
            if idx in received:
                continue
            f.seek(idx * chunk_size)
            data = f.read(chunk_size)
            call(base, args.token, "PUT", f"/api/v1/uploads/{upload_id}/chunks/{idx}", data, "application/octet-stream")
            print(f"chunk {idx + 1}/{total}", file=sys.stderr)
    done = call(base, args.token, "POST", f"/api/v1/uploads/{upload_id}/complete")
    print(json.dumps(done))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: README**

Добавить в `README.md` раздел «Загрузка и файлы» после раздела про тесты:

```markdown
## Загрузка и файлы (M1a)

- `POST /api/v1/uploads` → `PUT /api/v1/uploads/{id}/chunks/{n}` (сырые байты, 32 МиБ) → `GET /api/v1/uploads/{id}` (докачка) → `POST /api/v1/uploads/{id}/complete` → `asset_id`. Справочный клиент: `python tools/upload_file.py https://video.cloudrudesign.ru $TOKEN clip.mp4`.
- Мелкие файлы (SRT, музыка до 64 МиБ): `POST /api/v1/assets/upload` (multipart `file`, необязательно `kind`).
- `GET /api/v1/assets`, `GET /api/v1/assets/{id}` (ссылки на `proxy`, `thumbs`, `peaks`, `analysis` появляются по статусу), `DELETE /api/v1/assets/{id}`. Квота и использование в `GET /api/v1/me`.
- Файлы: `/files/{user_id}/assets/{asset_id}/<имя>`; на VM отдаёт Caddy после `forward_auth` в `/internal/authz`, локально само приложение. `source.*` наружу не отдаётся.
- Пределы: 5 ГБ на файл, 20 ГБ на человека, 20 новых загрузок в час, отказ при свободном диске меньше 10 %.
- Janitor (`python -m server.janitor`, таймер раз в час): загрузки старше 24 ч, ассеты без обращений старше 24 ч, сироты на диске старше часа, зависшие задания, просроченные сессии, суточный бэкап базы в `data/backups/` (7 копий).
- Локальный запуск: `VIDEO_TMP_DIR` по умолчанию `data/tmp`; на VM `/srv/video/tmp` (тот же раздел, что `/srv/video/data`).
```

- [ ] **Step 3: Прогнать всё и закоммитить**

Run: `uv run python -m pytest && uv run ruff check . && cd web && npm test && npm run build`
Expected: всё зелёное.

```bash
git add tools/upload_file.py README.md
git commit -m "docs: uploads/files API and reference upload client"
```

- [ ] **Step 4: Слить в main и выкатить**

Из основной сессии (не субагентом): финальное ревью ветки, `git checkout main && git merge --ff-only m1a-uploads-assets && git push origin main m1a-uploads-assets`. На VM добавить в `/opt/editing-site/.env` строку `VIDEO_TMP_DIR=/srv/video/tmp` (через `sudo tee -a`, значение не секретное), затем:

```bash
ssh -o UserKnownHostsFile=/c/sshkh/known_hosts -i secrets/id_rsa.id_rsa admin@176.109.109.251 'sudo bash /opt/editing-site/deploy/deploy.sh; systemctl list-timers video-janitor.timer --no-pager; id -nG caddy; sudo systemctl start video-janitor.service; journalctl -u video-janitor -n 3 --no-pager'
```

Expected: `deploy ok: <sha>`, таймер в списке, `caddy` в группе `video`, в журнале строка `janitor: uploads_expired=0 ... backup=1`.

- [ ] **Step 5: Живая проверка**

Токен агента лежит в `secrets/.env` строкой `AGENT_TOKEN=...` (значение не печатать; читать `awk -F= '/^AGENT_TOKEN=/{print $2}' secrets/.env | tr -d '\r'`). С этого ПК DNS может не резолвиться (перехват локальным резолвером): добавлять `--resolve video.cloudrudesign.ru:443:176.109.109.251` к каждому `curl`.

1. `python tools/upload_file.py https://video.cloudrudesign.ru $TOK <файл 100+ МБ>` → в ответе `asset_id`, в `GET /api/v1/assets/{id}` статус `uploaded` (анализ появится в M1b). Если на ПК DNS не работает, гнать с VM: `scp` файла и запуск скрипта там с `http://127.0.0.1:8010`.
2. Докачка: прервать скрипт по Ctrl+C на середине, запустить снова с `--upload-id` → дошлёт только недостающие части.
3. Файлы через Caddy: `curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOK" https://video.cloudrudesign.ru/files/<user_id>/assets/<asset_id>/source.mp4` → `403`; `.../peaks.json` → `404` (файла ещё нет); без заголовка → `401`; `https://video.cloudrudesign.ru/internal/authz` → `404`.
4. Браузер: страница показывает панель «Файлы» и квоту в шапке; загрузка мелкого файла через input показывает прогресс и строку в таблице; кнопка «Удалить» убирает строку.
5. `GET /api/v1/me` → `quota.used_bytes` равен сумме размеров.
6. Записать результаты в «Поправки по ходу выполнения» ниже и в vault (folder note проекта, хронология «Экосистемы Claude»).

---

## Поправки по ходу выполнения

- **Task 1** (`95765bf` + fix-коммит): ревью качества: `asset_dir`/`upload_path` проверяют идентификаторы по `ID_RE` и бросают `ValueError` (путь не может выйти за каталог данных; тест `isinstance(...)` заменён на `pytest.raises`), `parse_file_url` отвергает имя `.`/`..`, пустая `VIDEO_TMP_DIR=` означает значение по умолчанию (валидатор `mode="before"`), `kind_from_ext` приводит к нижнему регистру. Следствие для Task 3: несуществующий пользователь в тесте отката `finalize_file` должен иметь шестнадцатеричный id (`usr_0000000000ff`), иначе сработает `ValueError`, а не `IntegrityError`. Комментарий к полям хранения в `config.py` сокращён ради лимита 110 знаков.
- **Task 2** (`ecaadb5`): в `tests/test_db_migrate.py` точный список версий встречался дважды: строка 29 (`[1, 2, 3]`) и строка 157 (`[2, 3]` в тесте апгрейда базы версии 1); grep из Step 1 второе место не ловил. Обе строки обновлены. Правило на будущее: при добавлении миграции искать `migrate(conn) ==`.
  Ревью качества: индекс очереди стал `jobs(status, lane, priority DESC, created_at)` (запрос воркера `ORDER BY priority DESC, created_at` без досортировки; проверено `EXPLAIN QUERY PLAN`), `progress` получил `CHECK (progress BETWEEN 0 AND 1)`, добавлены тесты на CHECK и каскад удаления пользователя, `TABLES` в `test_db_migrate.py` дополнен новыми таблицами. Воркер в M1b должен использовать именно этот порядок сортировки.
- **Task 3** (`0fcfe6d` + fix-коммит): ревью качества: в `create_upload` проверка квоты, резерв файла и INSERT идут внутри одной `transaction()` (`BEGIN IMMEDIATE` сериализует параллельные `POST /uploads` одного пользователя, иначе два запроса проходят квоту по одному и тому же `used`); при ошибке резерва пустой файл удаляется (`posix_fallocate` падает уже после `O_CREAT`); `chunk_length` бросает `UploadError(404, "no_such_chunk")` вне диапазона, маршрут в Task 4 может на это опираться. Ruff в проекте (0.16, `extend-select` без `select`) включает гораздо больше правил, чем «дефолт + E501/B/DTZ», в частности SIM115: на долгоживущий `open()` в `ChunkWriter` стоит `# noqa: SIM115`. Строки плана длиннее 110 знаков переносились без изменения смысла.

## Что остаётся на M1b

Воркер `video-worker` (полоса `cpu`, `UPDATE ... RETURNING`, пульс в `heartbeats`, отмена по статусу), `ffprobe`, `audio16k.wav`, пики и карта пауз в `peaks.json` / `analysis.json`, полоска кадров `thumbs.jpg` / `thumbs.json`, прокси 640 px, статусы `analyzing` → `ready` → `proxy_ready`, `/healthz` считает отсутствие пульса деградацией, unit `video-worker.service` в `deploy.sh` и `bootstrap.sh`, плеер прокси в панели ассетов, интеграционные тесты на синтетическом медиа (`testsrc`, `sine`).
