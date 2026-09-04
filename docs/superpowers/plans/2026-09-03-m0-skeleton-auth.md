# M0: скелет, вход, токены, деплой, замер — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Развёрнутый на VM за Caddy сервис с входом через Yandex OAuth по whitelist, серверными сессиями, токенами для агента, `/healthz`, минимальной страницей настроек, скриптами bootstrap/deploy и отчётом о скорости ffmpeg на этой машине.

**Architecture:** FastAPI-приложение `server/app` с фабрикой `create_app(settings)`, SQLite через stdlib `sqlite3` (WAL, миграции из SQL-файлов), авторизация двумя путями (cookie-сессия для браузера, `Authorization: Bearer vt_…` для агента), единый формат ошибок `{"error": {code, message, details}}`. Фронтенд `web/` на Vite + TypeScript без фреймворков, собирается в `web/dist` и раздаётся тем же приложением. Деплой: systemd-юнит `video-api`, Caddy с TLS, скрипты в `deploy/`.

**Tech Stack:** Python 3.12, uv, FastAPI, uvicorn, pydantic-settings, httpx, sqlite3; TypeScript, Vite 5, vitest; Caddy 2, systemd, Ubuntu 24.04, ffmpeg 6.

**Спека:** `docs/superpowers/specs/2026-09-03-video-editor-design.md` (разделы 5, 11, 12, 13, 14, 15).

**Не входит в M0 (по спеке — следующие этапы):** загрузка и ассеты, воркер и janitor, `/files/*` с forward_auth, egress-allowlist (появится в M4, когда известен хост провайдера транскрипции), квота в `/me`.

## Поправки по ходу выполнения

Код-блоки задач ниже — исходная редакция. Ревью после каждой задачи внесло правки, они в репозитории; здесь список, чтобы читать план вместе с ним.

- **Python.** Локально `uv` не смог поставить управляемый 3.12 (кириллица в пути + виртуализация AppData); `.python-version` не создаём, локально venv на 3.14, `requires-python >= 3.12` остаётся, VM на системном 3.12. Тесты запускать `uv run python -m pytest` (без второго `-q`: `addopts` уже содержит `-q`).
- **Task 1** (`cfc75f6`): `.gitattributes` с `* text=auto eol=lf`, OS-мусор в `.gitignore`, `noqa` только на второй строке smoke-теста.
- **Task 2** (`6bbfee8`): `iso()` отвергает наивные datetime; `Settings.public_base_url` валидируется (схема http/https + хост, хвостовой `/` срезается).
- **Task 3** (`1b258e3`, `1d1dbad`): `journal_mode=WAL` включается один раз в `migrate()` через `enable_wal()` с повторами при чужой блокировке; `connect()` ставит только `foreign_keys` и `synchronous=NORMAL`; миграции: `discover()` сортирует по номеру и отвергает дубликаты, запись номера идёт первым statement'ом внутри `BEGIN IMMEDIATE` (одновременный старт API и воркера безопасен), откат при ошибке; `COLLATE NOCASE` на email, `CHECK` на `disabled`.
- **Task 4** (`7051bca`): `trust_proxy` удалён из настроек, `client_ip(request)` берёт только адрес пира, разбор `X-Forwarded-For` делает uvicorn (`--proxy-headers --forwarded-allow-ips=127.0.0.1` в юните); проверка Origin пропускается только для `Authorization: Bearer`, без обоих заголовков запрос считается чужим, `same-site` не проходит; лимитер с блокировкой и пределом ключей; JSON-конверт и для необработанных 500; `ApiError` умеет заголовки; 422 не эхоит присланные значения; `allowed_origin` без порта по умолчанию. Следствия для задач ниже: в Task 5 тестовый клиент шлёт `Origin: http://testserver` по умолчанию; в Task 6 `sessions.py` импортирует `SESSION_COOKIE` из `security.py` (а не наоборот); в Task 7 вызов `client_ip(request)` без второго аргумента; в Task 11 из подсказки bootstrap убрать `VIDEO_TRUST_PROXY`.
- **Task 5** (`da1f148`, `7d8a7f8`): в lifespan долгоживущее соединение `app.state.db_keeper` (файлы WAL не пересоздаются на каждый запрос; из обработчиков им не пользоваться), `configure_logging` (корень WARNING, логгер `video` по `VIDEO_LOG_LEVEL`), `create_app(settings, web_dist=None)` с тестом монтирования статики, объект `app` создаётся лениво через `__getattr__` модуля; `/healthz` не отвечает 500: проверка живой таблицы `schema_migrations`, перехват ошибок диска и пульса, типизированная схема ответа `Health`; валидаторы `log_level` и порта/хоста в `public_base_url`; тесты `enable_wal`. Следствие для Task 11: `deploy.sh` читает поле `status` в ответе `/healthz`, потому что degraded тоже отвечает 200. Следствие для M1: отсутствие пульса воркера должно стать degraded, как только воркер появится.
- **Task 5, follow-up** (`97274ba`): `/healthz` сам открывает соединение внутри перехвата (переполненный диск больше не даёт 500), catch-all `/api/{rest:path}` отвечает JSON `not_found` раньше статики (даже при `404.html` в сборке), в ruff включён E501. Следствие: неверный метод на существующий маршрут API даёт 404, а не 405, это осознанно; в Task 7 добавить `HEAD` в список методов catch-all.
- **Task 6** (`4153955`): cookie сессии хранится в базе как sha256 (`sha256_hex` в `util.py`), сырое значение живёт только в cookie; `sessions.py` не знает имени cookie, `SESSION_COOKIE` берётся из `security.py` (в Task 7 `deps.py` и `routes.py` импортируют его оттуда, а не из `sessions.py`); `hash_token` через `sha256_hex`; добавлен тест на отключённого пользователя. Fix `8f532f2`: границы настроек сессий и лимитера через `Field` (`max_sessions_per_user=0` больше не выкидывает всех), `expires_in_days` только 1..3650 (0 не означает «вечный»), пустой email отклоняется, битый `last_seen_at` инвалидирует сессию; тесты на интервал касания, изоляцию пользователей, `last_used_at`. Следствия: в Task 7 callback отвечает 403 `account_disabled` отключённому пользователю; в Task 8 модель запроса ограничивает `expires_in_days` теми же границами; в Task 9 помнить, что роль перезаписывается при каждом входе по `admin_email`.
- **Task 7** (`ff5499f`, fix `4a3e87b`): `deps.py` и `routes.py` берут `SESSION_COOKIE` и `is_bearer` из `security.py`; 401 несут `WWW-Authenticate: Bearer`; callback отвечает 403 `account_disabled` отключённому пользователю; callback под тем же лимитом, что и login, и любой его исход удаляет state-cookie (state одноразовый), ошибки callback возвращаются JSON-ответом через `_callback_failure`, а не исключением; обмен кода ловит и `ValueError`/`KeyError` → 502; текст ошибки провайдера обрезан до 64 символов; login требует и client id, и secret до проверки лимита; `/me` с `response_model=CurrentUser` и `Cache-Control: no-store`; в `users` добавлен `yandex_id` миграцией `0002_users_yandex_id.sql` (правка уже применённой `0001` ломала существующие базы: версия записана, колонка не появлялась; правило: применённые миграции не редактируются), уникальный индекс из `0002` снят миграцией `0003`: один аккаунт Яндекса может входить под двумя почтами из whitelist, это две строки `users` с одним `yandex_id`; `upsert_user` принимает `yandex_id`; 429 в callback не тратит state; `HEAD /healthz` зарегистрирован отдельным маршрутом без схемы; catch-all `/api/*` обрабатывает `HEAD` и `OPTIONS`; `/healthz` принимает `HEAD`. Следствие для Task 8: зарегистрировать `HEAD /healthz` отдельным маршрутом без схемы, чтобы убрать предупреждение FastAPI о дубликате operation id; модель запроса токена ограничивает `expires_in_days` 1..3650. Следствие для Task 11: в Caddyfile заголовки `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`.
- **Task 8** (`97da7dd` + полировка): модель запроса ограничивает `expires_in_days` через `MAX_TOKEN_DAYS`, имя токена обрезается и пустое отклоняется; ответы типизированы (`TokenList`, `TokenCreated`); потолок `MAX_ACTIVE_TOKENS = 20` живых токенов на пользователя, превышение отвечает 409 `too_many_tokens`; тесты на DELETE с чужим Origin, пробельное имя, потолок и чужой токен (404).
- **Task 9** (`197aa33`): ответы типизированы (`WhitelistList`, `WhitelistEntry`, `Stats`); удаление адреса из whitelist отключает учётную запись (`users.disabled = 1`, сессии удаляются, токены перестают приниматься), повторное добавление включает обратно, иначе живые сессии и бессрочные токены агента продолжали бы работать после удаления; в тесте отключения одна дополнительная строка `admin.cookies.delete("vsid")` из-за дубликата cookie в jar httpx (ручной `cookies.set` кладёт запись под другим доменом). Fix: администратора из конфигурации нельзя удалить из whitelist (409 `cannot_remove_admin`), и его вход всегда снимает `disabled` (иначе единственный админ мог отключить сам себя без пути восстановления); статистика читает диск через `disk_free_pct_safe`.
- **Task 10** (`8115f56` + fix): в README раздел «Разработка интерфейса» (сборка + один сервер, либо Vite dev-сервер с `VIDEO_PUBLIC_BASE_URL=http://localhost:5173` из-за проверки Origin); обработка ошибок у выхода, отзыва токена и действий администратора через отдельные слоты `#tokens-error` и `#admin-error`, перерисовка только при успехе; `parseError` терпит `error: null` и отсутствие `code`; `data-revoke` экранируется. Известно: `npm audit` ругается на esbuild dev-сервера через vite 5 (только dev-зависимость), обновление до Vite 8 отложено.
- **Task 11** (`62f939d`): в Caddyfile заголовки `X-Content-Type-Options nosniff`, `X-Frame-Options DENY`, `Referrer-Policy strict-origin-when-cross-origin`, `-Server`; `deploy.sh` читает поле `status` в теле `/healthz` (200 приходит и при degraded); из подсказки bootstrap убран `VIDEO_TRUST_PROXY`; добавлен `tests/test_deploy_files.py` против дрейфа конфигов (флаги uvicorn в юните, заголовки и лимит тела в Caddyfile, shebang и strict mode скриптов, отсутствие TRUST_PROXY). Fix: все git-команды в `deploy.sh` от пользователя `video` (от root git 2.43 отвечает «dubious ownership», а `set -e` внутри `$(...)` этого не видит); готовность ждётся опросом `/healthz` до 20 с с хвостом journalctl при провале; поддержка приватного репозитория через deploy key `/etc/editing-site/deploy_key` и `GIT_SSH_COMMAND` (known_hosts тоже в `/etc/editing-site`, чтобы не засорять каталог приложения); юнит с `ProtectSystem=strict`, `ProtectHome=true`, `ReadWritePaths=/srv/video`.
- **Task 12** (`cc2c451`): скрипт по плану; `# noqa: DTZ011` на `date.today()`. Локальный замер на ПК разработчика (16 потоков, образец 10 с 4K): прокси 11×, draft 11×, final 8× реального времени. Ловушка Windows: консоль cp1251 не печатает `×`, отчёт при этом уже записан в UTF-8. Fix: `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` в начале `main()`, `probe_duration` даёт понятную ошибку на `N/A` от ffprobe, прокси пинит `-pix_fmt yuv420p`, из генерации образца убран лишний `-shortest`, имя хоста в имени файла отчёта санируется; тесты на `N/A`, код выхода 2 без ffmpeg и pixel format.
- **Task 13, живой прогон 2026-09-04** (VM `176.109.109.251`, `video.cloudrudesign.ru`): bootstrap упал дважды по причинам, которых не было в плане: `unattended-upgrades` держал блокировку apt на свежей машине больше 30 минут (fix: `apt-get -o DPkg::Lock::Timeout=600`, при затяжных обновлениях остановить `unattended-upgrades` штатно через systemd), и в Ubuntu уже есть системная группа `video` (fix: `useradd --gid video`). После этого bootstrap и deploy прошли: миграции 1–3, Caddy перечитал конфиг, `/healthz` `ok`. Замер: proxy 1.28×, draft 1.36×, final 1.04× (отчёт `docs/benchmarks/2026-09-04-editing.md`); по правилу шага 7 прокси снижен до 640 px, `final` остаётся `veryfast`. DNS-запись `video` на reg.ru добавлена, но зона публиковалась дольше часа; до её появления Caddy не может получить сертификат, вход через Яндекс не проверен.
- **Сосед на VM, 2026-09-04** (`VideoBoard/docs/NEIGHBOR-NOTICE.md`): на этой же VM разворачивается VideoBoard (`/opt/videoboard`, `board`, порт 8020, `/srv/board`). По их просьбе в `deploy/Caddyfile` добавлен top-level `import /etc/caddy/conf.d/*.caddy` (проверено на VM: Caddy 2.11.4 валидирует конфиг и без каталога, и с пустым), тест на дрейф, раздел «Соседи на той же VM» в README. Следствия: ротация секрета общего Yandex OAuth-приложения кладёт оба сервиса; диск общий (их потолок 30 ГБ, TTL 30 дней, порог 10 % как у нас); наши auth-модули скопированы к ним без обратной связи.
- **Финальное ревью ветки** (`6d7f230`, `37161e2`): правки whitelist только из браузера (`require_admin_cookie`; токен агента админа мог завести чужой постоянный вход), роль вычисляется из `VIDEO_ADMIN_EMAIL` на каждом запросе (смена адреса сразу понижает старого админа), многошаговые правки whitelist и выпуск токена в явной транзакции (`transaction()` в `db/core.py`), `/healthz` отвечает 503 при degraded (внешний пинг по коду), `deploy.sh` переустанавливает Caddyfile (домен из `/etc/editing-site/domain`) и юнит при каждом деплое, ошибки OAuth редиректят на `/?error=<code>` и страница входа показывает текст, выход с Bearer отвечает 400, ruff `extend-select = ["E501", "B", "DTZ"]`, uvicorn `>=0.30.2`, HSTS и `no-cache` для index, `.npm/` в gitignore, README с шагами деплоя. Отложено в бэклог: дубль `TOUCH_INTERVAL`, `httpx2` для TestClient, права `/srv/video` под Caddy (M1), чистка просроченных сессий и отозванных токенов (janitor, M1).

---

## Карта файлов

| Файл | Ответственность |
|---|---|
| `pyproject.toml`, `.gitignore`, `.env.example`, `README.md` | Тулчейн, зависимости, конфиг-образец, быстрый старт |
| `server/app/config.py` | `Settings` из окружения (`VIDEO_*`), производные `db_path`, `yandex_redirect_uri`, `allowed_origin` |
| `server/app/util.py` | Время в ISO-8601 UTC с миллисекундами, парсинг, генерация идентификаторов |
| `server/db/core.py` | Подключение к SQLite с прагмами, зависимость `get_db` |
| `server/db/migrate.py`, `server/db/migrations/0001_auth.sql` | Миграции по номерам, таблицы users / whitelist / sessions / api_tokens / heartbeats |
| `server/app/errors.py` | `ApiError` и обработчики, единый JSON-формат ошибок |
| `server/app/ratelimit.py` | Лимитер с фиксированным окном, инъекция часов для тестов |
| `server/app/security.py` | Проверка Origin для cookie-запросов, определение IP клиента |
| `server/app/auth/oauth.py` | Три функции Yandex OAuth с инъекцией httpx-клиента |
| `server/app/auth/users.py` | Whitelist-проверка, upsert пользователя, роль администратора |
| `server/app/auth/sessions.py` | Серверные сессии: создание с вытеснением, разрешение с TTL, удаление |
| `server/app/auth/tokens.py` | Токены агента: выпуск, список, отзыв, разрешение по хешу |
| `server/app/auth/deps.py` | `current_user` (cookie или Bearer), `require_admin`, `require_cookie` |
| `server/app/auth/routes.py` | `/api/v1/auth/login`, `/callback`, `/logout`, `/api/v1/me` |
| `server/app/auth/token_routes.py` | `/api/v1/tokens` |
| `server/app/admin/routes.py` | `/api/v1/admin/whitelist`, `/api/v1/admin/stats` |
| `server/app/health.py` | `/healthz` |
| `server/app/main.py` | `create_app`, lifespan с миграциями, монтирование `web/dist` |
| `web/*` | Страница входа и настроек: токены, whitelist для администратора |
| `deploy/*` | Caddyfile, `video-api.service`, `bootstrap.sh`, `deploy.sh` |
| `tools/bench_ffmpeg.py` | Замер прокси / draft / final, отчёт в `docs/benchmarks/` |
| `tests/*` | pytest: unit на чистые функции и модули с БД, API через TestClient |

Соглашения кода: время везде строкой ISO-8601 UTC с миллисекундами (`2026-09-03T10:00:00.000Z`), сравнения строк корректны; идентификаторы `usr_…`, `tok_…`; SQL без `DEFAULT` для времени, времена ставит Python; в SQLite autocommit (`isolation_level=None`), транзакции явные.

---

### Task 1: Скелет репозитория и тулчейн

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `README.md`, `server/__init__.py`, `server/app/__init__.py`, `server/app/auth/__init__.py`, `server/app/admin/__init__.py`, `server/db/__init__.py`, `tests/__init__.py`, `tests/test_smoke.py`

- [ ] **Step 1: Проверить uv**

Run: `uv --version`
Expected: `uv 0.4.27` или новее (раньше нет поддержки `[dependency-groups]`). Если команды нет: `pip install uv`, повторить. На этой машине тесты запускать через `uv run python -m pytest -q`: exe-обёртка `uv run pytest` стартует около минуты.

- [ ] **Step 2: Создать `pyproject.toml`**

```toml
[project]
name = "editing-site"
version = "0.1.0"
description = "Онлайн-редактор видео на VM с API для агента"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115,<1",
    "uvicorn[standard]>=0.30,<1",
    "pydantic-settings>=2.5,<3",
    "httpx>=0.27,<1",
    "python-multipart>=0.0.9",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "ruff>=0.6",
]

[tool.uv]
package = false

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"

[tool.ruff]
line-length = 110
target-version = "py312"
```

- [ ] **Step 3: Создать `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.cache/
data/
.env
web/node_modules/
web/dist/
```

- [ ] **Step 4: Создать `.env.example`**

```
# Все переменные с префиксом VIDEO_. Копия этого файла с реальными значениями: .env (0600, вне git).

# Каталог данных: база и файлы. На VM: /srv/video/data
VIDEO_DATA_DIR=./data

# Публичный адрес сервиса без завершающего слэша. От него считаются redirect_uri и разрешённый Origin.
VIDEO_PUBLIC_BASE_URL=http://localhost:8010

# Yandex OAuth: приложение на https://oauth.yandex.ru/client/new,
# redirect URI = ${VIDEO_PUBLIC_BASE_URL}/api/v1/auth/callback, права login:email login:info
VIDEO_YANDEX_CLIENT_ID=
VIDEO_YANDEX_CLIENT_SECRET=

# Почта первого администратора: всегда в whitelist, роль admin
VIDEO_ADMIN_EMAIL=

# За HTTPS обязательно true (cookie только по HTTPS). IP клиента за Caddy восстанавливает сам uvicorn:
# флаги --proxy-headers --forwarded-allow-ips=127.0.0.1 в systemd-юните, в коде X-Forwarded-For не разбирается.
VIDEO_COOKIE_SECURE=false

# Сессии и лимиты
VIDEO_SESSION_ABSOLUTE_DAYS=30
VIDEO_SESSION_IDLE_DAYS=7
VIDEO_MAX_SESSIONS_PER_USER=5
VIDEO_LOGIN_RATE_MAX=10
VIDEO_LOGIN_RATE_WINDOW_SEC=60

VIDEO_LOG_LEVEL=INFO
```

- [ ] **Step 5: Создать `README.md`**

```markdown
# Editing site

Онлайн-редактор видео на VM с API для внешнего агента. Дизайн: `docs/superpowers/specs/2026-09-03-video-editor-design.md`.

## Локальный запуск

```bash
uv sync
cp .env.example .env            # заполнить VIDEO_ADMIN_EMAIL и ключи Yandex
uv run uvicorn server.app.main:app --reload --port 8010
cd web && npm install && npm run dev   # интерфейс на http://localhost:5173, /api проксируется на 8010
```

## Тесты

```bash
uv run pytest
cd web && npm test
```

## Деплой

`deploy/bootstrap.sh` один раз на чистой Ubuntu 24.04, затем `deploy/deploy.sh` на каждый релиз. Подробности в спеке, раздел 12.
```

- [ ] **Step 6: Создать пустые пакеты и smoke-тест**

Файлы `server/__init__.py`, `server/app/__init__.py`, `server/app/auth/__init__.py`, `server/app/admin/__init__.py`, `server/db/__init__.py`, `tests/__init__.py` — пустые.

`tests/test_smoke.py`:

```python
def test_packages_import():
    import server.app  # noqa: F401
    import server.db  # noqa: F401
```

- [ ] **Step 7: Установить зависимости и прогнать тест**

Run: `uv sync && uv run pytest`
Expected: `1 passed`

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .env.example README.md server tests
git commit -m "chore: project skeleton with uv, pytest and env example

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Настройки и утилиты

**Files:**
- Create: `server/app/config.py`, `server/app/util.py`
- Test: `tests/test_config.py`, `tests/test_util.py`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_config.py`:

```python
from server.app.config import Settings


def test_settings_from_env_and_derived_values(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VIDEO_PUBLIC_BASE_URL", "https://video.example.ru/")
    s = Settings(_env_file=None)
    assert s.data_dir == tmp_path
    assert s.db_path == tmp_path / "video.db"
    assert s.yandex_redirect_uri == "https://video.example.ru/api/v1/auth/callback"
    assert s.allowed_origin == "https://video.example.ru"
    assert s.session_absolute_days == 30
    assert s.cookie_secure is False


def test_settings_kwargs_override_env(monkeypatch):
    monkeypatch.setenv("VIDEO_ADMIN_EMAIL", "env@ya.ru")
    s = Settings(_env_file=None, admin_email="kw@ya.ru")
    assert s.admin_email == "kw@ya.ru"
```

`tests/test_util.py`:

```python
from datetime import datetime, timedelta, timezone

from server.app.util import iso, new_id, now_iso, parse_iso, utcnow


def test_iso_roundtrip_is_utc_with_milliseconds():
    dt = datetime(2026, 9, 3, 10, 0, 0, 123456, tzinfo=timezone.utc)
    s = iso(dt)
    assert s == "2026-09-03T10:00:00.123Z"
    assert parse_iso(s) == datetime(2026, 9, 3, 10, 0, 0, 123000, tzinfo=timezone.utc)


def test_iso_strings_compare_chronologically():
    a = utcnow()
    assert iso(a) < iso(a + timedelta(seconds=1))
    assert now_iso().endswith("Z")


def test_new_id_has_prefix_and_is_unique():
    a, b = new_id("usr"), new_id("usr")
    assert a.startswith("usr_") and len(a) == 4 + 12
    assert a != b
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_config.py tests/test_util.py`
Expected: `ModuleNotFoundError: No module named 'server.app.config'`

- [ ] **Step 3: Написать `server/app/config.py`**

```python
"""Настройки сервиса из окружения и .env (префикс VIDEO_)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="VIDEO_", extra="ignore")

    data_dir: Path = Path("./data")
    public_base_url: str = "http://localhost:8010"
    yandex_client_id: str = ""
    yandex_client_secret: str = ""
    admin_email: str = ""
    cookie_secure: bool = False
    session_absolute_days: int = 30
    session_idle_days: int = 7
    max_sessions_per_user: int = 5
    login_rate_max: int = 10
    login_rate_window_sec: int = 60
    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "video.db"

    @property
    def yandex_redirect_uri(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/api/v1/auth/callback"

    @property
    def allowed_origin(self) -> str:
        u = urlsplit(self.public_base_url)
        return f"{u.scheme}://{u.netloc}"
```

- [ ] **Step 4: Написать `server/app/util.py`**

```python
"""Время и идентификаторы. Время везде: ISO-8601 UTC с миллисекундами и суффиксом Z."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_iso() -> str:
    return iso(utcnow())


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"
```

- [ ] **Step 5: Прогнать тесты**

Run: `uv run pytest tests/test_config.py tests/test_util.py`
Expected: `5 passed`

- [ ] **Step 6: Commit**

```bash
git add server/app/config.py server/app/util.py tests/test_config.py tests/test_util.py
git commit -m "feat: settings from env and time/id helpers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: База и миграции

**Files:**
- Create: `server/db/core.py`, `server/db/migrate.py`, `server/db/migrations/0001_auth.sql`
- Test: `tests/test_db_migrate.py`

- [ ] **Step 1: Написать падающий тест**

`tests/test_db_migrate.py`:

```python
import sqlite3

from server.db.core import connect
from server.db.migrate import migrate


def test_migrate_creates_tables_and_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        assert migrate(conn) == [1]
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"users", "whitelist", "sessions", "api_tokens", "heartbeats", "schema_migrations"} <= names
        assert migrate(conn) == []
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_foreign_keys_are_enforced(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        migrate(conn)
        try:
            conn.execute(
                "INSERT INTO sessions (id, user_id, created_at, last_seen_at, absolute_expires_at, user_agent) "
                "VALUES ('s', 'no_such_user', 'x', 'x', 'x', '')"
            )
            raise AssertionError("expected IntegrityError")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `uv run pytest tests/test_db_migrate.py`
Expected: `ModuleNotFoundError: No module named 'server.db.core'`

- [ ] **Step 3: Написать `server/db/core.py`**

```python
"""Подключение к SQLite. Autocommit (isolation_level=None): транзакции явные, где нужны."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """Зависимость FastAPI: соединение на запрос, закрывается после ответа."""
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()
```

- [ ] **Step 4: Написать `server/db/migrations/0001_auth.sql`**

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE whitelist (
    email TEXT PRIMARY KEY,
    added_by TEXT,
    added_at TEXT NOT NULL
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    absolute_expires_at TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX sessions_user_idx ON sessions(user_id);

CREATE TABLE api_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    expires_at TEXT,
    revoked_at TEXT
);
CREATE INDEX api_tokens_user_idx ON api_tokens(user_id);

CREATE TABLE heartbeats (
    name TEXT PRIMARY KEY,
    at TEXT NOT NULL
);
```

- [ ] **Step 5: Написать `server/db/migrate.py`**

```python
"""Миграции: файлы server/db/migrations/NNNN_name.sql применяются по возрастанию номера, каждый в транзакции."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def pending(conn: sqlite3.Connection) -> list[tuple[int, Path]]:
    done = applied_versions(conn)
    out: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = re.match(r"^(\d+)_", path.name)
        if not m:
            raise ValueError(f"bad migration file name: {path.name}")
        version = int(m.group(1))
        if version not in done:
            out.append((version, path))
    return out


def migrate(conn: sqlite3.Connection) -> list[int]:
    applied: list[int] = []
    for version, path in pending(conn):
        sql = path.read_text(encoding="utf-8")
        conn.executescript(
            "BEGIN;\n"
            f"{sql}\n"
            f"INSERT INTO schema_migrations (version, applied_at) VALUES ({version}, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));\n"
            "COMMIT;"
        )
        applied.append(version)
    return applied


def main() -> None:
    from server.app.config import Settings
    from server.db.core import connect

    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.db_path)
    try:
        applied = migrate(conn)
    finally:
        conn.close()
    print("migrations applied:", applied or "none")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Прогнать тесты**

Run: `uv run pytest tests/test_db_migrate.py`
Expected: `2 passed`

- [ ] **Step 7: Commit**

```bash
git add server/db tests/test_db_migrate.py
git commit -m "feat: sqlite connection helper and sql migrations with auth tables

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Ошибки, лимитер, проверка Origin

**Files:**
- Create: `server/app/errors.py`, `server/app/ratelimit.py`, `server/app/security.py`
- Test: `tests/test_ratelimit.py`, `tests/test_security.py`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_ratelimit.py`:

```python
from server.app.ratelimit import FixedWindowLimiter


def test_limiter_allows_up_to_max_then_blocks_until_window_passes():
    clock = {"t": 100.0}
    limiter = FixedWindowLimiter(max_hits=2, window_sec=60, clock=lambda: clock["t"])
    assert limiter.allow("ip1") is True
    assert limiter.allow("ip1") is True
    assert limiter.allow("ip1") is False
    assert limiter.allow("ip2") is True
    clock["t"] += 61
    assert limiter.allow("ip1") is True
```

`tests/test_security.py`:

```python
from server.app.security import is_cross_site


def test_origin_header_decides_when_present():
    assert is_cross_site({"origin": "https://evil.example"}, "https://video.example.ru") is True
    assert is_cross_site({"origin": "https://video.example.ru"}, "https://video.example.ru") is False
    assert is_cross_site({"origin": "https://VIDEO.example.ru/"}, "https://video.example.ru") is False


def test_sec_fetch_site_used_without_origin():
    assert is_cross_site({"sec-fetch-site": "cross-site"}, "https://video.example.ru") is True
    assert is_cross_site({"sec-fetch-site": "same-origin"}, "https://video.example.ru") is False


def test_no_headers_means_not_cross_site():
    assert is_cross_site({}, "https://video.example.ru") is False
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_ratelimit.py tests/test_security.py`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Написать `server/app/errors.py`**

```python
"""Единый формат ошибок API: {"error": {"code", "message", "details"}}."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "Некорректный запрос", {"errors": jsonable_encoder(exc.errors())}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body("http_error", str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )
```

- [ ] **Step 4: Написать `server/app/ratelimit.py`**

```python
"""Лимитер с фиксированным окном в памяти (один процесс API, Redis не нужен)."""
from __future__ import annotations

import time
from collections.abc import Callable


class FixedWindowLimiter:
    def __init__(self, max_hits: int, window_sec: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.max = max_hits
        self.window = window_sec
        self._clock = clock
        self._buckets: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        start, count = self._buckets.get(key, (now, 0))
        if now - start >= self.window:
            start, count = now, 0
        count += 1
        self._buckets[key] = (start, count)
        return count <= self.max
```

- [ ] **Step 5: Написать `server/app/security.py`**

```python
"""Защита cookie-сессий от запросов с чужих сайтов и определение IP клиента."""
from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.app.errors import error_body

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SESSION_COOKIE = "vsid"


def is_cross_site(headers: Mapping[str, str], allowed_origin: str) -> bool:
    origin = headers.get("origin")
    if origin is not None:
        return origin.rstrip("/").lower() != allowed_origin.rstrip("/").lower()
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site.lower() == "cross-site"
    return False


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def install_origin_check(app: FastAPI) -> None:
    @app.middleware("http")
    async def _origin_check(request: Request, call_next):
        if (
            request.method in UNSAFE_METHODS
            and SESSION_COOKIE in request.cookies
            and not request.headers.get("authorization")
            and is_cross_site(request.headers, request.app.state.settings.allowed_origin)
        ):
            return JSONResponse(status_code=403, content=error_body("cross_site", "Запрос с чужого сайта отклонён"))
        return await call_next(request)
```

- [ ] **Step 6: Прогнать тесты**

Run: `uv run pytest tests/test_ratelimit.py tests/test_security.py`
Expected: `4 passed`

- [ ] **Step 7: Commit**

```bash
git add server/app/errors.py server/app/ratelimit.py server/app/security.py tests/test_ratelimit.py tests/test_security.py
git commit -m "feat: api error format, fixed-window limiter, origin check

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Приложение и `/healthz`

**Files:**
- Create: `server/app/health.py`, `server/app/main.py`, `tests/conftest.py`
- Test: `tests/test_health.py`

- [ ] **Step 1: Написать `tests/conftest.py`**

Фикстуры `login_as` пригодятся с Task 7, но `import server.app.auth.routes` появится только там, поэтому импорт внутри функции.

```python
import pytest
from starlette.testclient import TestClient

from server.app.config import Settings
from server.app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        public_base_url="http://testserver",
        yandex_client_id="cid",
        yandex_client_secret="sec",
        admin_email="admin@ya.ru",
        login_rate_max=1000,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    # Origin по умолчанию: проверка cross-site считает запрос без Origin чужим (Task 4), а браузер шлёт его всегда.
    with TestClient(app, headers={"Origin": "http://testserver"}) as c:
        yield c


@pytest.fixture
def login_as(client, monkeypatch):
    """Логин через подменённые OAuth-функции. Возвращает функцию login(email, name)."""

    def _login(email: str = "admin@ya.ru", name: str = "Admin"):
        import server.app.auth.routes as routes

        async def fake_exchange(client_, **kwargs):
            return "ACCESS"

        async def fake_userinfo(client_, token):
            return {"id": "1", "default_email": email, "real_name": name}

        monkeypatch.setattr(routes, "exchange_code", fake_exchange)
        monkeypatch.setattr(routes, "fetch_userinfo", fake_userinfo)
        r = client.get("/api/v1/auth/login", follow_redirects=False)
        assert r.status_code == 302, r.text
        state = client.cookies.get("oauth_state")
        r = client.get("/api/v1/auth/callback", params={"code": "x", "state": state}, follow_redirects=False)
        assert r.status_code == 302, r.text
        return client

    return _login
```

- [ ] **Step 2: Написать падающий тест**

`tests/test_health.py`:

```python
from server.app.util import iso, utcnow
from server.db.core import connect
from datetime import timedelta


def test_healthz_ok_without_worker(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert 0 <= body["disk_free_pct"] <= 100
    assert body["worker_seen_sec_ago"] is None


def test_healthz_degraded_when_worker_stale(client, settings):
    conn = connect(settings.db_path)
    conn.execute(
        "INSERT INTO heartbeats (name, at) VALUES ('worker', ?)", (iso(utcnow() - timedelta(seconds=600)),)
    )
    conn.close()
    body = client.get("/healthz").json()
    assert body["status"] == "degraded"
    assert body["worker_seen_sec_ago"] >= 600


def test_unknown_route_returns_json_error(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "http_error"
```

- [ ] **Step 3: Убедиться, что тест падает**

Run: `uv run pytest tests/test_health.py`
Expected: `ModuleNotFoundError: No module named 'server.app.main'`

- [ ] **Step 4: Написать `server/app/health.py`**

```python
"""GET /healthz: база, свободный диск, пульс воркера. Без авторизации и без подробностей."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from server.app.util import parse_iso, utcnow
from server.db.core import get_db

router = APIRouter(tags=["health"])

WORKER_STALE_AFTER_SEC = 120
DISK_LOW_PCT = 10.0


def disk_free_pct(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return round(usage.free / usage.total * 100, 1)


@router.get("/healthz")
def healthz(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    settings = request.app.state.settings
    db_ok = conn.execute("SELECT 1").fetchone()[0] == 1
    free = disk_free_pct(settings.data_dir)
    row = conn.execute("SELECT at FROM heartbeats WHERE name = 'worker'").fetchone()
    worker_age = int((utcnow() - parse_iso(row["at"])).total_seconds()) if row else None
    degraded = (not db_ok) or free < DISK_LOW_PCT or (worker_age is not None and worker_age > WORKER_STALE_AFTER_SEC)
    return {
        "status": "degraded" if degraded else "ok",
        "db": db_ok,
        "disk_free_pct": free,
        "worker_seen_sec_ago": worker_age,
    }
```

- [ ] **Step 5: Написать `server/app/main.py`** (роутеры auth/tokens/admin подключатся в Task 7–9; сейчас только health)

```python
"""Фабрика приложения. uvicorn server.app.main:app"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.app.config import Settings
from server.app.errors import install_error_handlers
from server.app.health import router as health_router
from server.app.ratelimit import FixedWindowLimiter
from server.app.security import install_origin_check
from server.db.core import connect
from server.db.migrate import migrate

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        conn = connect(settings.db_path)
        try:
            migrate(conn)
        finally:
            conn.close()
        yield

    app = FastAPI(
        title="Editing site",
        version="0.1.0",
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.login_limiter = FixedWindowLimiter(settings.login_rate_max, settings.login_rate_window_sec)
    install_error_handlers(app)
    install_origin_check(app)
    app.include_router(health_router)
    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
    return app


app = create_app()
```

- [ ] **Step 6: Прогнать тесты**

Run: `uv run pytest tests/test_health.py`
Expected: `3 passed`

- [ ] **Step 7: Проверить запуск сервера вручную**

Run: `uv run uvicorn server.app.main:app --port 8010` и в другом терминале `curl -s http://127.0.0.1:8010/healthz`
Expected: `{"status":"ok","db":true,"disk_free_pct":...,"worker_seen_sec_ago":null}`. Остановить сервер (Ctrl+C).

- [ ] **Step 8: Commit**

```bash
git add server/app/health.py server/app/main.py tests/conftest.py tests/test_health.py
git commit -m "feat: app factory with migrations on startup and /healthz

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Пользователи, сессии, токены (модули над БД)

**Files:**
- Create: `server/app/auth/users.py`, `server/app/auth/sessions.py`, `server/app/auth/tokens.py`
- Test: `tests/test_auth_modules.py`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_auth_modules.py`:

```python
from datetime import timedelta

import pytest

from server.app.auth.sessions import create_session, delete_session, resolve_session
from server.app.auth.tokens import TOKEN_PREFIX, create_token, list_tokens, resolve_token, revoke_token
from server.app.auth.users import is_whitelisted, upsert_user
from server.app.util import iso, utcnow
from server.db.core import connect
from server.db.migrate import migrate


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    yield conn
    conn.close()


def test_whitelist_admin_email_always_allowed_and_table_lookup(db):
    assert is_whitelisted(db, "Admin@YA.ru", "admin@ya.ru") is True
    assert is_whitelisted(db, "user@ya.ru", "admin@ya.ru") is False
    db.execute("INSERT INTO whitelist (email, added_by, added_at) VALUES ('user@ya.ru', NULL, '2026-01-01T00:00:00.000Z')")
    assert is_whitelisted(db, " user@ya.ru ", "admin@ya.ru") is True
    assert is_whitelisted(db, "", "admin@ya.ru") is False


def test_upsert_user_sets_role_and_updates_name(db):
    a = upsert_user(db, email="Admin@ya.ru", name="A", admin_email="admin@ya.ru")
    assert a["email"] == "admin@ya.ru" and a["role"] == "admin"
    u = upsert_user(db, email="user@ya.ru", name="U1", admin_email="admin@ya.ru")
    assert u["role"] == "user"
    u2 = upsert_user(db, email="user@ya.ru", name="U2", admin_email="admin@ya.ru")
    assert u2["id"] == u["id"] and u2["name"] == "U2"


def test_session_limit_evicts_oldest(db, settings):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    sids = [create_session(db, user_id=uid, user_agent="ua", settings=settings) for _ in range(6)]
    alive = {r[0] for r in db.execute("SELECT id FROM sessions WHERE user_id = ?", (uid,))}
    assert len(alive) == 5
    assert sids[0] not in alive and sids[-1] in alive
    row = resolve_session(db, sids[-1], settings)
    assert row["email"] == "u@ya.ru" and row["session_id"] == sids[-1]


def test_session_expires_by_idle_and_absolute_ttl(db, settings):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    idle = create_session(db, user_id=uid, user_agent="", settings=settings)
    db.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (iso(utcnow() - timedelta(days=8)), idle))
    assert resolve_session(db, idle, settings) is None
    old = create_session(db, user_id=uid, user_agent="", settings=settings)
    db.execute("UPDATE sessions SET absolute_expires_at = ? WHERE id = ?", (iso(utcnow() - timedelta(seconds=1)), old))
    assert resolve_session(db, old, settings) is None
    assert resolve_session(db, None, settings) is None
    assert resolve_session(db, "no-such", settings) is None


def test_delete_session(db, settings):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    sid = create_session(db, user_id=uid, user_agent="", settings=settings)
    delete_session(db, sid)
    assert resolve_session(db, sid, settings) is None


def test_token_lifecycle(db):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    view, secret = create_token(db, user_id=uid, name="agent", expires_in_days=None)
    assert secret.startswith(TOKEN_PREFIX) and "secret" not in view
    assert [t["id"] for t in list_tokens(db, uid)] == [view["id"]]
    row = resolve_token(db, secret)
    assert row["email"] == "u@ya.ru" and row["token_id"] == view["id"]
    assert resolve_token(db, "vt_wrong") is None
    assert resolve_token(db, "not-a-token") is None
    assert revoke_token(db, user_id=uid, token_id=view["id"]) is True
    assert revoke_token(db, user_id=uid, token_id=view["id"]) is False
    assert resolve_token(db, secret) is None
    assert list_tokens(db, uid) == []


def test_token_expiry(db):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    view, secret = create_token(db, user_id=uid, name="short", expires_in_days=1)
    assert view["expires_at"] > iso(utcnow())
    db.execute("UPDATE api_tokens SET expires_at = ? WHERE id = ?", (iso(utcnow() - timedelta(seconds=1)), view["id"]))
    assert resolve_token(db, secret) is None
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_auth_modules.py`
Expected: `ModuleNotFoundError: No module named 'server.app.auth.sessions'`

- [ ] **Step 3: Написать `server/app/auth/users.py`**

```python
"""Whitelist и пользователи."""
from __future__ import annotations

import sqlite3

from server.app.util import new_id, now_iso


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_whitelisted(conn: sqlite3.Connection, email: str, admin_email: str) -> bool:
    e = normalize_email(email)
    if not e:
        return False
    if admin_email and e == normalize_email(admin_email):
        return True
    return conn.execute("SELECT 1 FROM whitelist WHERE email = ?", (e,)).fetchone() is not None


def upsert_user(conn: sqlite3.Connection, *, email: str, name: str, admin_email: str) -> sqlite3.Row:
    e = normalize_email(email)
    role = "admin" if admin_email and e == normalize_email(admin_email) else "user"
    row = conn.execute("SELECT id FROM users WHERE email = ?", (e,)).fetchone()
    if row is None:
        uid = new_id("usr")
        conn.execute(
            "INSERT INTO users (id, email, name, role, disabled, created_at) VALUES (?, ?, ?, ?, 0, ?)",
            (uid, e, name[:100], role, now_iso()),
        )
    else:
        uid = row["id"]
        conn.execute("UPDATE users SET name = ?, role = ? WHERE id = ?", (name[:100], role, uid))
    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
```

- [ ] **Step 4: Написать `server/app/auth/sessions.py`**

```python
"""Серверные сессии. В cookie только случайный идентификатор, сроки и лимиты здесь."""
from __future__ import annotations

import secrets
import sqlite3
from datetime import timedelta

from server.app.config import Settings
from server.app.security import SESSION_COOKIE
from server.app.util import iso, parse_iso, utcnow

TOUCH_INTERVAL = timedelta(minutes=1)

_USER_COLUMNS = "u.id, u.email, u.name, u.role, u.disabled"


def create_session(conn: sqlite3.Connection, *, user_id: str, user_agent: str, settings: Settings) -> str:
    now = utcnow()
    sid = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, last_seen_at, absolute_expires_at, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sid, user_id, iso(now), iso(now), iso(now + timedelta(days=settings.session_absolute_days)), user_agent[:200]),
    )
    # Оставляем max_sessions_per_user самых новых (rowid растёт с каждой вставкой, время может совпасть).
    conn.execute(
        "DELETE FROM sessions WHERE user_id = ? AND id NOT IN "
        "(SELECT id FROM sessions WHERE user_id = ? ORDER BY rowid DESC LIMIT ?)",
        (user_id, user_id, settings.max_sessions_per_user),
    )
    return sid


def resolve_session(conn: sqlite3.Connection, sid: str | None, settings: Settings) -> sqlite3.Row | None:
    """Строка с полями пользователя + session_id, либо None. Просроченные сессии удаляются."""
    if not sid:
        return None
    row = conn.execute(
        f"SELECT s.id AS session_id, s.last_seen_at, s.absolute_expires_at, {_USER_COLUMNS} "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?",
        (sid,),
    ).fetchone()
    if row is None:
        return None
    now = utcnow()
    idle_for = now - parse_iso(row["last_seen_at"])
    expired = iso(now) > row["absolute_expires_at"] or idle_for > timedelta(days=settings.session_idle_days)
    if expired or row["disabled"]:
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        return None
    if idle_for > TOUCH_INTERVAL:
        conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (iso(now), sid))
    return row


def delete_session(conn: sqlite3.Connection, sid: str | None) -> None:
    if sid:
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
```

- [ ] **Step 5: Написать `server/app/auth/tokens.py`**

```python
"""Токены агента: секрет показывается один раз, в базе только sha256."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import timedelta

from server.app.util import iso, new_id, now_iso, parse_iso, utcnow

TOKEN_PREFIX = "vt_"
TOUCH_INTERVAL = timedelta(minutes=1)


def hash_token(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def token_view(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "expires_at": row["expires_at"],
    }


def create_token(
    conn: sqlite3.Connection, *, user_id: str, name: str, expires_in_days: int | None
) -> tuple[dict, str]:
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    tid = new_id("tok")
    now = utcnow()
    expires_at = iso(now + timedelta(days=expires_in_days)) if expires_in_days else None
    conn.execute(
        "INSERT INTO api_tokens (id, user_id, name, token_hash, created_at, last_used_at, expires_at, revoked_at) "
        "VALUES (?, ?, ?, ?, ?, NULL, ?, NULL)",
        (tid, user_id, name[:100], hash_token(secret), iso(now), expires_at),
    )
    row = conn.execute("SELECT * FROM api_tokens WHERE id = ?", (tid,)).fetchone()
    return token_view(row), secret


def list_tokens(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM api_tokens WHERE user_id = ? AND revoked_at IS NULL ORDER BY created_at DESC, rowid DESC",
        (user_id,),
    )
    return [token_view(r) for r in rows]


def revoke_token(conn: sqlite3.Connection, *, user_id: str, token_id: str) -> bool:
    cur = conn.execute(
        "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
        (now_iso(), token_id, user_id),
    )
    return cur.rowcount == 1


def resolve_token(conn: sqlite3.Connection, secret: str | None) -> sqlite3.Row | None:
    """Строка с полями пользователя + token_id, либо None."""
    if not secret or not secret.startswith(TOKEN_PREFIX):
        return None
    row = conn.execute(
        "SELECT t.id AS token_id, t.last_used_at, t.expires_at, t.revoked_at, "
        "u.id, u.email, u.name, u.role, u.disabled "
        "FROM api_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?",
        (hash_token(secret),),
    ).fetchone()
    if row is None or row["revoked_at"] or row["disabled"]:
        return None
    now = utcnow()
    if row["expires_at"] and row["expires_at"] < iso(now):
        return None
    if not row["last_used_at"] or now - parse_iso(row["last_used_at"]) > TOUCH_INTERVAL:
        conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (iso(now), row["token_id"]))
    return row
```

- [ ] **Step 6: Прогнать тесты**

Run: `uv run pytest tests/test_auth_modules.py`
Expected: `7 passed`

- [ ] **Step 7: Commit**

```bash
git add server/app/auth/users.py server/app/auth/sessions.py server/app/auth/tokens.py tests/test_auth_modules.py
git commit -m "feat: whitelist users, server-side sessions and agent tokens over sqlite

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Вход через Yandex OAuth, `/me`, зависимости авторизации

**Files:**
- Create: `server/app/auth/oauth.py`, `server/app/auth/deps.py`, `server/app/auth/routes.py`
- Modify: `server/app/main.py` (подключить роутеры), `server/app/security.py` (импорт имени cookie из sessions)
- Test: `tests/test_auth_login.py`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_auth_login.py`:

```python
from server.app.config import Settings
from server.app.main import create_app
from starlette.testclient import TestClient


def test_login_redirects_to_yandex_with_state_cookie(client):
    r = client.get("/api/v1/auth/login", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("https://oauth.yandex.ru/authorize?")
    assert "client_id=cid" in loc and "redirect_uri=http%3A%2F%2Ftestserver%2Fapi%2Fv1%2Fauth%2Fcallback" in loc
    assert client.cookies.get("oauth_state")


def test_callback_sets_session_and_me_works(login_as):
    c = login_as("admin@ya.ru", "Admin")
    me = c.get("/api/v1/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "admin@ya.ru" and body["role"] == "admin" and body["auth"] == "cookie"


def test_callback_rejects_bad_state(client):
    client.get("/api/v1/auth/login", follow_redirects=False)
    r = client.get("/api/v1/auth/callback", params={"code": "x", "state": "wrong"}, follow_redirects=False)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_state"


def test_callback_rejects_non_whitelisted(client, monkeypatch):
    import server.app.auth.routes as routes

    async def fake_exchange(client_, **kwargs):
        return "ACCESS"

    async def stranger(client_, token):
        return {"id": "9", "default_email": "stranger@ya.ru", "real_name": "S"}

    monkeypatch.setattr(routes, "exchange_code", fake_exchange)
    monkeypatch.setattr(routes, "fetch_userinfo", stranger)
    client.get("/api/v1/auth/login", follow_redirects=False)
    state = client.cookies.get("oauth_state")
    r = client.get("/api/v1/auth/callback", params={"code": "x", "state": state}, follow_redirects=False)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_allowed"
    assert client.get("/api/v1/me").status_code == 401


def test_me_without_session_is_401(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_logout_clears_session(login_as):
    c = login_as()
    assert c.post("/api/v1/auth/logout").status_code == 200
    assert c.get("/api/v1/me").status_code == 401


def test_login_rate_limited_per_ip(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, yandex_client_id="cid", login_rate_max=2)
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/v1/auth/login", follow_redirects=False).status_code == 302
        assert c.get("/api/v1/auth/login", follow_redirects=False).status_code == 302
        r = c.get("/api/v1/auth/login", follow_redirects=False)
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limited"


def test_login_without_oauth_config_is_503(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, yandex_client_id="")
    with TestClient(create_app(settings)) as c:
        r = c.get("/api/v1/auth/login", follow_redirects=False)
        assert r.status_code == 503
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_auth_login.py`
Expected: первый тест падает с `assert 404 == 302` (маршрута ещё нет), остальные с ошибками импорта.

- [ ] **Step 3: Написать `server/app/auth/oauth.py`** (перенос из шлюза платформы)

```python
"""Yandex OAuth: три чистые функции, httpx-клиент передаётся снаружи (в тестах подменяются целиком)."""
from __future__ import annotations

from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
USERINFO_URL = "https://login.yandex.ru/info"


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        # Яндекс каждый раз показывает выбор аккаунта вместо тихого SSO.
        "force_confirm": "yes",
    })
    return f"{AUTHORIZE_URL}?{query}"


async def exchange_code(
    client: httpx.AsyncClient, *, code: str, client_id: str, client_secret: str, redirect_uri: str
) -> str:
    resp = await client.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


async def fetch_userinfo(client: httpx.AsyncClient, access_token: str) -> dict:
    resp = await client.get(
        USERINFO_URL, params={"format": "json"}, headers={"Authorization": f"OAuth {access_token}"}
    )
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 4: Написать `server/app/auth/deps.py`**

```python
"""Текущий пользователь: Bearer-токен агента или cookie-сессия браузера."""
from __future__ import annotations

import sqlite3

from fastapi import Depends, Request
from pydantic import BaseModel

from server.app.auth.sessions import SESSION_COOKIE, resolve_session
from server.app.auth.tokens import resolve_token
from server.app.errors import ApiError
from server.db.core import get_db


class CurrentUser(BaseModel):
    id: str
    email: str
    name: str
    role: str
    auth: str  # "cookie" | "token"


def _user(row: sqlite3.Row, auth: str) -> CurrentUser:
    return CurrentUser(id=row["id"], email=row["email"], name=row["name"], role=row["role"], auth=auth)


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> CurrentUser:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        row = resolve_token(conn, header[7:].strip())
        if row is None:
            raise ApiError(401, "invalid_token", "Токен недействителен")
        return _user(row, "token")
    row = resolve_session(conn, request.cookies.get(SESSION_COOKIE), request.app.state.settings)
    if row is None:
        raise ApiError(401, "unauthorized", "Требуется вход")
    return _user(row, "cookie")


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if user.role != "admin":
        raise ApiError(403, "admin_only", "Только для администратора")
    return user


def require_cookie(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """Управление токенами и настройками только из браузера: токен не должен плодить токены."""
    if user.auth != "cookie":
        raise ApiError(403, "cookie_required", "Доступно только из браузера после входа")
    return user
```

- [ ] **Step 5: Написать `server/app/auth/routes.py`**

```python
"""Вход через Yandex OAuth, выход, текущий пользователь."""
from __future__ import annotations

import secrets
import sqlite3

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from server.app.auth.deps import CurrentUser, current_user
from server.app.auth.oauth import build_authorize_url, exchange_code, fetch_userinfo
from server.app.auth.sessions import SESSION_COOKIE, create_session, delete_session
from server.app.auth.users import is_whitelisted, upsert_user
from server.app.errors import ApiError
from server.app.security import client_ip
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
me_router = APIRouter(prefix="/api/v1", tags=["me"])

STATE_COOKIE = "oauth_state"
STATE_COOKIE_PATH = "/api/v1/auth"


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    settings = request.app.state.settings
    if not request.app.state.login_limiter.allow(client_ip(request)):
        raise ApiError(429, "rate_limited", "Слишком много попыток входа, подождите минуту")
    if not settings.yandex_client_id:
        raise ApiError(503, "oauth_not_configured", "Yandex OAuth не настроен")
    state = secrets.token_urlsafe(24)
    url = build_authorize_url(
        client_id=settings.yandex_client_id, redirect_uri=settings.yandex_redirect_uri, state=state
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        STATE_COOKIE, state, max_age=600, httponly=True, secure=settings.cookie_secure,
        samesite="lax", path=STATE_COOKIE_PATH,
    )
    return resp


@router.get("/callback")
async def callback(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    settings = request.app.state.settings
    if error:
        raise ApiError(400, "oauth_error", f"Яндекс вернул ошибку: {error}")
    if not code or not state or state != request.cookies.get(STATE_COOKIE):
        raise ApiError(400, "bad_state", "Сессия входа не совпадает, начните вход заново")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            access_token = await exchange_code(
                client, code=code, client_id=settings.yandex_client_id,
                client_secret=settings.yandex_client_secret, redirect_uri=settings.yandex_redirect_uri,
            )
            info = await fetch_userinfo(client, access_token)
    except httpx.HTTPError as exc:
        raise ApiError(502, "oauth_upstream", f"Яндекс недоступен: {exc.__class__.__name__}") from exc
    email = str(info.get("default_email") or "").strip().lower()
    if not is_whitelisted(conn, email, settings.admin_email):
        raise ApiError(403, "not_allowed", "Этот адрес не в списке разрешённых")
    name = str(info.get("real_name") or info.get("display_name") or email)
    user = upsert_user(conn, email=email, name=name, admin_email=settings.admin_email)
    delete_session(conn, request.cookies.get(SESSION_COOKIE))
    sid = create_session(
        conn, user_id=user["id"], user_agent=request.headers.get("user-agent", ""), settings=settings
    )
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE, sid, max_age=settings.session_absolute_days * 86400, httponly=True,
        secure=settings.cookie_secure, samesite="lax", path="/",
    )
    resp.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    return resp


@router.post("/logout")
def logout(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> JSONResponse:
    delete_session(conn, request.cookies.get(SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@me_router.get("/me")
def me(user: CurrentUser = Depends(current_user)) -> dict:
    return user.model_dump()
```

- [ ] **Step 6: Имя cookie**

Ничего не менять: `SESSION_COOKIE` живёт в `server/app/security.py`, а `server/app/auth/sessions.py` импортирует его оттуда (см. Task 6). Направление зависимостей: низкоуровневый `security.py` ничего не знает про сессии.

- [ ] **Step 7: Подключить роутеры в `server/app/main.py`**

Добавить импорты после `from server.app.health import router as health_router`:

```python
from server.app.auth.routes import me_router
from server.app.auth.routes import router as auth_router
```

После `app.include_router(health_router)` добавить:

```python
    app.include_router(auth_router)
    app.include_router(me_router)
```

- [ ] **Step 8: Прогнать все тесты**

Run: `uv run pytest`
Expected: все зелёные, в том числе 8 в `tests/test_auth_login.py`.

- [ ] **Step 9: Commit**

```bash
git add server/app/auth/oauth.py server/app/auth/deps.py server/app/auth/routes.py server/app/security.py server/app/main.py tests/test_auth_login.py
git commit -m "feat: yandex oauth login with whitelist, sessions, /me and logout

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Маршруты токенов

**Files:**
- Create: `server/app/auth/token_routes.py`
- Modify: `server/app/main.py`
- Test: `tests/test_tokens_api.py`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_tokens_api.py`:

```python
def test_create_list_use_and_revoke_token(login_as):
    c = login_as()
    r = c.post("/api/v1/tokens", json={"name": "agent"})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["secret"].startswith("vt_") and created["name"] == "agent"

    listed = c.get("/api/v1/tokens").json()["tokens"]
    assert [t["id"] for t in listed] == [created["id"]]
    assert "secret" not in listed[0]

    headers = {"Authorization": f"Bearer {created['secret']}"}
    me = c.get("/api/v1/me", headers=headers)
    assert me.status_code == 200 and me.json()["auth"] == "token"

    assert c.delete(f"/api/v1/tokens/{created['id']}").status_code == 204
    assert c.get("/api/v1/me", headers=headers).status_code == 401
    assert c.delete(f"/api/v1/tokens/{created['id']}").status_code == 404


def test_token_cannot_manage_tokens(login_as):
    c = login_as()
    secret = c.post("/api/v1/tokens", json={"name": "agent"}).json()["secret"]
    r = c.post("/api/v1/tokens", json={"name": "child"}, headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "cookie_required"


def test_invalid_bearer_is_401(client):
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer vt_nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_token"


def test_token_validation_errors(login_as):
    c = login_as()
    assert c.post("/api/v1/tokens", json={"name": ""}).status_code == 422
    assert c.post("/api/v1/tokens", json={"name": "x", "expires_in_days": 0}).status_code == 422


def test_cross_site_post_with_cookie_is_rejected(login_as):
    c = login_as()
    r = c.post("/api/v1/tokens", json={"name": "t"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "cross_site"
    r = c.post("/api/v1/tokens", json={"name": "t"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 201
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_tokens_api.py`
Expected: `assert 404 == 201`

- [ ] **Step 3: Написать `server/app/auth/token_routes.py`**

```python
"""Токены агента: выпуск (секрет один раз), список, отзыв. Только из браузера."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from server.app.auth.deps import CurrentUser, require_cookie
from server.app.auth.tokens import create_token, list_tokens, revoke_token
from server.app.errors import ApiError
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


@router.get("")
def list_(user: CurrentUser = Depends(require_cookie), conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"tokens": list_tokens(conn, user.id)}


@router.post("", status_code=201)
def create(
    body: TokenCreate, user: CurrentUser = Depends(require_cookie), conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    view, secret = create_token(conn, user_id=user.id, name=body.name, expires_in_days=body.expires_in_days)
    return {**view, "secret": secret}


@router.delete("/{token_id}", status_code=204)
def revoke(
    token_id: str, user: CurrentUser = Depends(require_cookie), conn: sqlite3.Connection = Depends(get_db)
) -> Response:
    if not revoke_token(conn, user_id=user.id, token_id=token_id):
        raise ApiError(404, "not_found", "Токен не найден")
    return Response(status_code=204)
```

- [ ] **Step 4: Подключить роутер в `server/app/main.py`**

Импорт: `from server.app.auth.token_routes import router as tokens_router`. После `app.include_router(me_router)` добавить `app.include_router(tokens_router)`.

- [ ] **Step 5: Прогнать тесты**

Run: `uv run pytest tests/test_tokens_api.py`
Expected: `5 passed`

- [ ] **Step 6: Commit**

```bash
git add server/app/auth/token_routes.py server/app/main.py tests/test_tokens_api.py
git commit -m "feat: agent token endpoints with cookie-only management

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Администратор: whitelist и статистика

**Files:**
- Create: `server/app/admin/routes.py`
- Modify: `server/app/main.py`
- Test: `tests/test_admin_api.py`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_admin_api.py`:

```python
def test_admin_manages_whitelist_and_user_can_login(login_as):
    admin = login_as("admin@ya.ru")
    assert admin.get("/api/v1/admin/whitelist").json() == {"emails": []}
    r = admin.post("/api/v1/admin/whitelist", json={"email": " User@YA.ru "})
    assert r.status_code == 201 and r.json()["email"] == "user@ya.ru"
    assert admin.post("/api/v1/admin/whitelist", json={"email": "user@ya.ru"}).status_code == 201
    entries = admin.get("/api/v1/admin/whitelist").json()["emails"]
    assert [e["email"] for e in entries] == ["user@ya.ru"] and entries[0]["added_by"] == "admin@ya.ru"

    user = login_as("user@ya.ru", "User")
    assert user.get("/api/v1/me").json()["role"] == "user"
    assert user.get("/api/v1/admin/stats").status_code == 403

    admin = login_as("admin@ya.ru")
    assert admin.delete("/api/v1/admin/whitelist/user@ya.ru").status_code == 204
    assert admin.delete("/api/v1/admin/whitelist/user@ya.ru").status_code == 404
    assert admin.get("/api/v1/admin/whitelist").json() == {"emails": []}


def test_admin_rejects_invalid_email(login_as):
    admin = login_as("admin@ya.ru")
    r = admin.post("/api/v1/admin/whitelist", json={"email": "not-an-email"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_email"


def test_admin_stats(login_as):
    admin = login_as("admin@ya.ru")
    admin.post("/api/v1/tokens", json={"name": "t"})
    body = admin.get("/api/v1/admin/stats").json()
    assert body["users"] == 1 and body["sessions"] == 1 and body["tokens"] == 1
    assert 0 <= body["disk_free_pct"] <= 100


def test_admin_routes_need_login(client):
    assert client.get("/api/v1/admin/stats").status_code == 401
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_admin_api.py`
Expected: `assert {'error': ...} == {'emails': []}` (404 на несуществующий маршрут)

- [ ] **Step 3: Написать `server/app/admin/routes.py`**

```python
"""Администратор: whitelist почт и общая статистика. Чужие проекты администратор не видит."""
from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from server.app.auth.deps import CurrentUser, require_admin
from server.app.auth.users import normalize_email
from server.app.errors import ApiError
from server.app.health import disk_free_pct
from server.app.util import now_iso
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class WhitelistAdd(BaseModel):
    email: str


@router.get("/whitelist")
def whitelist_list(_: CurrentUser = Depends(require_admin), conn: sqlite3.Connection = Depends(get_db)) -> dict:
    rows = conn.execute("SELECT email, added_by, added_at FROM whitelist ORDER BY added_at, email")
    return {"emails": [dict(r) for r in rows]}


@router.post("/whitelist", status_code=201)
def whitelist_add(
    body: WhitelistAdd, admin: CurrentUser = Depends(require_admin), conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    email = normalize_email(body.email)
    if not EMAIL_RE.match(email):
        raise ApiError(422, "invalid_email", "Это не похоже на адрес почты")
    conn.execute(
        "INSERT OR IGNORE INTO whitelist (email, added_by, added_at) VALUES (?, ?, ?)",
        (email, admin.email, now_iso()),
    )
    return {"email": email}


@router.delete("/whitelist/{email}", status_code=204)
def whitelist_remove(
    email: str, _: CurrentUser = Depends(require_admin), conn: sqlite3.Connection = Depends(get_db)
) -> Response:
    cur = conn.execute("DELETE FROM whitelist WHERE email = ?", (normalize_email(email),))
    if cur.rowcount == 0:
        raise ApiError(404, "not_found", "Адреса нет в списке")
    return Response(status_code=204)


@router.get("/stats")
def stats(
    request: Request, _: CurrentUser = Depends(require_admin), conn: sqlite3.Connection = Depends(get_db)
) -> dict:
    def count(table: str, where: str = "") -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]

    return {
        "users": count("users"),
        "sessions": count("sessions"),
        "tokens": count("api_tokens", "WHERE revoked_at IS NULL"),
        "disk_free_pct": disk_free_pct(request.app.state.settings.data_dir),
    }
```

- [ ] **Step 4: Подключить роутер в `server/app/main.py`**

Импорт: `from server.app.admin.routes import router as admin_router`. После `app.include_router(tokens_router)` добавить `app.include_router(admin_router)`.

- [ ] **Step 5: Прогнать все тесты**

Run: `uv run pytest`
Expected: все зелёные (около 36 тестов).

- [ ] **Step 6: Commit**

```bash
git add server/app/admin/routes.py server/app/main.py tests/test_admin_api.py
git commit -m "feat: admin whitelist and stats endpoints

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Фронтенд-скелет: вход и настройки

**Files:**
- Create: `web/package.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/index.html`, `web/src/style.css`, `web/src/html.ts`, `web/src/html.test.ts`, `web/src/api.ts`, `web/src/api.test.ts`, `web/src/main.ts`

- [ ] **Step 1: Создать `web/package.json`, `web/tsconfig.json`, `web/vite.config.ts`**

`web/package.json`:

```json
{
  "name": "editing-site-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "test": "vitest run"
  },
  "devDependencies": {
    "typescript": "^5.6.0",
    "vite": "^5.4.0",
    "vitest": "^2.1.0"
  }
}
```

`web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "lib": ["ES2022", "DOM"],
    "types": ["vite/client"],
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

`web/vite.config.ts`:

```ts
import { defineConfig } from 'vite'

export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8010',
      '/healthz': 'http://127.0.0.1:8010',
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
```

- [ ] **Step 2: Написать падающие тесты**

`web/src/html.test.ts`:

```ts
import { expect, test } from 'vitest'
import { escapeHtml } from './html'

test('escapeHtml escapes the five special characters', () => {
  expect(escapeHtml(`<a href="x">Tom & 'Jerry'</a>`)).toBe(
    '&lt;a href=&quot;x&quot;&gt;Tom &amp; &#39;Jerry&#39;&lt;/a&gt;',
  )
})
```

`web/src/api.test.ts`:

```ts
import { expect, test } from 'vitest'
import { ApiError, parseError } from './api'

test('parseError reads the api error envelope', () => {
  const e = parseError(403, { error: { code: 'cross_site', message: 'Отклонено', details: { a: 1 } } })
  expect(e).toBeInstanceOf(ApiError)
  expect(e.status).toBe(403)
  expect(e.code).toBe('cross_site')
  expect(e.message).toBe('Отклонено')
  expect(e.details).toEqual({ a: 1 })
})

test('parseError falls back for non-json bodies', () => {
  const e = parseError(502, '<html>Bad gateway</html>')
  expect(e.code).toBe('http_error')
  expect(e.message).toBe('<html>Bad gateway</html>')
  expect(parseError(500, null).message).toBe('HTTP 500')
})
```

- [ ] **Step 3: Установить зависимости и убедиться, что тесты падают**

Run: `cd web && npm install && npm test`
Expected: `Failed to resolve import "./html"` / `"./api"`.

- [ ] **Step 4: Написать `web/src/html.ts` и `web/src/api.ts`**

`web/src/html.ts`:

```ts
const MAP: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, c => MAP[c])
}
```

`web/src/api.ts`:

```ts
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: unknown = null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function parseError(status: number, body: unknown): ApiError {
  if (body && typeof body === 'object' && 'error' in body) {
    const err = (body as { error: { code?: string; message?: string; details?: unknown } }).error
    return new ApiError(status, err.code ?? 'error', err.message ?? `HTTP ${status}`, err.details ?? null)
  }
  const text = typeof body === 'string' && body ? body.slice(0, 200) : `HTTP ${status}`
  return new ApiError(status, 'http_error', text)
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const res = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (res.status === 204) return undefined as T
  const text = await res.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }
  if (!res.ok) throw parseError(res.status, body)
  return body as T
}
```

- [ ] **Step 5: Прогнать тесты**

Run: `npm test` (в `web/`)
Expected: `3 passed`

- [ ] **Step 6: Написать `web/index.html`, `web/src/style.css`, `web/src/main.ts`**

`web/index.html`:

```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Editing site</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

`web/src/style.css`:

```css
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
body { margin: 0; background: #f4f4f2; color: #1c1c1c; }
@media (prefers-color-scheme: dark) { body { background: #161716; color: #e6e6e3; } }
.bar { display: flex; gap: 16px; align-items: center; padding: 12px 20px; border-bottom: 1px solid #8884; }
.bar span { flex: 1; }
.card { max-width: 720px; margin: 24px auto; padding: 20px 24px; border: 1px solid #8884; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #8883; }
form { display: flex; gap: 8px; margin-top: 12px; }
input { flex: 1; padding: 6px 8px; }
button, .button { padding: 6px 12px; cursor: pointer; }
pre { white-space: pre-wrap; word-break: break-all; padding: 12px; background: #8882; border-radius: 6px; }
```

`web/src/main.ts`:

```ts
import './style.css'
import { api, ApiError } from './api'
import { escapeHtml } from './html'

type Me = { id: string; email: string; name: string; role: 'admin' | 'user'; auth: 'cookie' | 'token' }
type Token = { id: string; name: string; created_at: string; last_used_at: string | null; expires_at: string | null }
type WhitelistEntry = { email: string; added_by: string | null; added_at: string }

const root = document.getElementById('app') as HTMLElement

function fmt(ts: string | null): string {
  return ts ? ts.replace('T', ' ').slice(0, 16) : '—'
}

async function boot(): Promise<void> {
  try {
    const me = await api<Me>('/api/v1/me')
    await renderSettings(me)
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) renderLogin()
    else renderError(e)
  }
}

function renderLogin(): void {
  root.innerHTML = `
    <main class="card">
      <h1>Editing site</h1>
      <p>Вход только для адресов из списка.</p>
      <a class="button" href="/api/v1/auth/login">Войти через Яндекс</a>
    </main>`
}

function renderError(e: unknown): void {
  const msg = e instanceof Error ? e.message : String(e)
  root.innerHTML = `<main class="card"><h1>Ошибка</h1><p>${escapeHtml(msg)}</p></main>`
}

async function renderSettings(me: Me): Promise<void> {
  const { tokens } = await api<{ tokens: Token[] }>('/api/v1/tokens')
  const rows = tokens
    .map(
      t => `<tr><td>${escapeHtml(t.name)}</td><td>${fmt(t.created_at)}</td><td>${fmt(t.last_used_at)}</td>
        <td>${fmt(t.expires_at)}</td><td><button data-revoke="${t.id}">Отозвать</button></td></tr>`,
    )
    .join('')
  root.innerHTML = `
    <header class="bar"><strong>Editing site</strong><span>${escapeHtml(me.email)}</span><button id="logout">Выйти</button></header>
    <main class="card">
      <h2>Токены для агента</h2>
      <table>
        <thead><tr><th>Имя</th><th>Создан</th><th>Использован</th><th>Истекает</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5">Пока нет</td></tr>'}</tbody>
      </table>
      <form id="token-form"><input name="name" placeholder="Имя токена" required maxlength="100" /><button>Выпустить</button></form>
      <pre id="secret" hidden></pre>
    </main>
    <section id="admin"></section>`

  document.getElementById('logout')!.addEventListener('click', async () => {
    await api('/api/v1/auth/logout', { method: 'POST' })
    await boot()
  })

  const form = document.getElementById('token-form') as HTMLFormElement
  form.addEventListener('submit', async ev => {
    ev.preventDefault()
    const name = String(new FormData(form).get('name') ?? '').trim()
    const created = await api<Token & { secret: string }>('/api/v1/tokens', {
      method: 'POST',
      body: JSON.stringify({ name }),
    })
    await renderSettings(me)
    const box = document.getElementById('secret') as HTMLPreElement
    box.hidden = false
    box.textContent = `Токен «${created.name}» показывается один раз:\n${created.secret}`
  })

  root.querySelectorAll<HTMLButtonElement>('button[data-revoke]').forEach(b =>
    b.addEventListener('click', async () => {
      await api(`/api/v1/tokens/${b.dataset.revoke}`, { method: 'DELETE' })
      await renderSettings(me)
    }),
  )

  if (me.role === 'admin') await renderAdmin()
}

async function renderAdmin(): Promise<void> {
  const { emails } = await api<{ emails: WhitelistEntry[] }>('/api/v1/admin/whitelist')
  const el = document.getElementById('admin') as HTMLElement
  const items = emails
    .map(e => `<li>${escapeHtml(e.email)} <button data-remove="${escapeHtml(e.email)}">Убрать</button></li>`)
    .join('')
  el.innerHTML = `
    <main class="card">
      <h2>Разрешённые адреса</h2>
      <ul>${items || '<li>Пока никого</li>'}</ul>
      <form id="wl-form"><input name="email" type="email" placeholder="user@yandex.ru" required /><button>Добавить</button></form>
    </main>`

  const form = document.getElementById('wl-form') as HTMLFormElement
  form.addEventListener('submit', async ev => {
    ev.preventDefault()
    const email = String(new FormData(form).get('email') ?? '').trim()
    await api('/api/v1/admin/whitelist', { method: 'POST', body: JSON.stringify({ email }) })
    await renderAdmin()
  })

  el.querySelectorAll<HTMLButtonElement>('button[data-remove]').forEach(b =>
    b.addEventListener('click', async () => {
      await api(`/api/v1/admin/whitelist/${encodeURIComponent(b.dataset.remove ?? '')}`, { method: 'DELETE' })
      await renderAdmin()
    }),
  )
}

void boot()
```

- [ ] **Step 7: Собрать и проверить типы**

Run: `npm run build` (в `web/`)
Expected: `tsc` без ошибок, `vite build` создаёт `web/dist/index.html` и `web/dist/assets/*`.

- [ ] **Step 8: Проверить страницу вручную**

Run: `uv run uvicorn server.app.main:app --port 8010` из корня репо, открыть `http://127.0.0.1:8010/`.
Expected: страница «Editing site» с кнопкой «Войти через Яндекс». Клик ведёт на `/api/v1/auth/login`; без ключей Yandex в `.env` ответ 503 в JSON — это ожидаемо до Task 13. Остановить сервер.

- [ ] **Step 9: Commit**

```bash
git add web/package.json web/package-lock.json web/tsconfig.json web/vite.config.ts web/index.html web/src
git commit -m "feat(web): login and settings page with agent tokens and admin whitelist

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Деплой: Caddyfile, systemd, bootstrap, deploy

**Files:**
- Create: `deploy/Caddyfile`, `deploy/video-api.service`, `deploy/bootstrap.sh`, `deploy/deploy.sh`

- [ ] **Step 1: Создать `deploy/Caddyfile`** (лимиты тела для загрузок и блок `/files/*` появятся в M1)

```
VIDEO_DOMAIN_PLACEHOLDER {
	encode zstd gzip
	request_body {
		max_size 1MB
	}
	reverse_proxy 127.0.0.1:8010
}
```

- [ ] **Step 2: Создать `deploy/video-api.service`**

```ini
[Unit]
Description=Editing site API
After=network-online.target
Wants=network-online.target

[Service]
User=video
Group=video
WorkingDirectory=/opt/editing-site
EnvironmentFile=/opt/editing-site/.env
ExecStart=/opt/editing-site/.venv/bin/uvicorn server.app.main:app --host 127.0.0.1 --port 8010 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Создать `deploy/bootstrap.sh`**

```bash
#!/usr/bin/env bash
# Первичная настройка чистой Ubuntu 24.04 под Editing site.
# Запуск от root: sudo bash bootstrap.sh <domain> <git-url>
# До запуска: A-запись домена указывает на эту VM (Caddy получит сертификат).
set -euo pipefail

DOMAIN="${1:?usage: bootstrap.sh <domain> <git-url>}"
REPO="${2:?usage: bootstrap.sh <domain> <git-url>}"
APP_DIR=/opt/editing-site
DATA_DIR=/srv/video

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  git ffmpeg sqlite3 ufw pipx nodejs npm fonts-dejavu-core fonts-noto-core \
  debian-keyring debian-archive-keyring apt-transport-https curl gnupg ca-certificates

# Caddy из официального репозитория (инструкция caddyserver.com/docs/install)
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi

# uv через pipx в /usr/local/bin, без curl | sh
if ! command -v uv >/dev/null 2>&1; then
  PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install uv
fi

# Сервисный пользователь без домашних файлов: git clone требует пустой каталог
if ! id -u video >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin video
fi
mkdir -p "$APP_DIR" "$DATA_DIR/data" "$DATA_DIR/tmp/uploads"
chown video:video "$APP_DIR"
chown -R video:video "$DATA_DIR"
chmod 750 "$DATA_DIR" "$DATA_DIR/data" "$DATA_DIR/tmp" "$DATA_DIR/tmp/uploads"

if [ ! -d "$APP_DIR/.git" ]; then
  sudo -u video git clone "$REPO" "$APP_DIR"
fi

if [ ! -f "$APP_DIR/.env" ]; then
  sudo -u video cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  cat <<EOF
!!! Заполни $APP_DIR/.env:
    VIDEO_DATA_DIR=$DATA_DIR/data
    VIDEO_PUBLIC_BASE_URL=https://$DOMAIN
    VIDEO_COOKIE_SECURE=true
    VIDEO_YANDEX_CLIENT_ID / VIDEO_YANDEX_CLIENT_SECRET / VIDEO_ADMIN_EMAIL
EOF
fi

sed "s/VIDEO_DOMAIN_PLACEHOLDER/$DOMAIN/" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
install -m 644 "$APP_DIR/deploy/video-api.service" /etc/systemd/system/video-api.service
systemctl daemon-reload
systemctl enable caddy video-api
systemctl restart caddy

ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "bootstrap done. Дальше: заполнить $APP_DIR/.env и выполнить: sudo bash $APP_DIR/deploy/deploy.sh"
```

- [ ] **Step 4: Создать `deploy/deploy.sh`**

```bash
#!/usr/bin/env bash
# Деплой текущего main: sudo bash /opt/editing-site/deploy/deploy.sh
set -euo pipefail

APP_DIR=/opt/editing-site
cd "$APP_DIR"

run_as_video() { sudo -u video env HOME="$APP_DIR" UV_PYTHON=/usr/bin/python3 UV_PYTHON_DOWNLOADS=never "$@"; }

run_as_video git fetch origin
run_as_video git merge --ff-only origin/main
run_as_video uv sync --frozen --no-dev
(cd web && run_as_video npm ci --no-audit --no-fund && run_as_video npm run build)
run_as_video .venv/bin/python -m server.db.migrate

systemctl restart video-api
sleep 1
systemctl is-active video-api
curl -fsS http://127.0.0.1:8010/healthz
echo
echo "deploy ok: $(git rev-parse --short HEAD)"
```

- [ ] **Step 5: Проверить синтаксис скриптов**

Run: `bash -n deploy/bootstrap.sh && bash -n deploy/deploy.sh && echo syntax-ok`
Expected: `syntax-ok`

- [ ] **Step 6: Проверить Caddyfile, если Caddy установлен локально** (иначе шаг пропускается, проверка произойдёт в bootstrap)

Run: `sed 's/VIDEO_DOMAIN_PLACEHOLDER/example.com/' deploy/Caddyfile | caddy validate --config - --adapter caddyfile`
Expected: `Valid configuration`

- [ ] **Step 7: Commit**

```bash
git add deploy
git commit -m "chore(deploy): caddyfile, systemd unit, bootstrap and deploy scripts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Скрипт замера ffmpeg

**Files:**
- Create: `tools/bench_ffmpeg.py`, `tools/__init__.py`
- Test: `tests/test_bench.py`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_bench.py`:

```python
from pathlib import Path

import pytest

from tools.bench_ffmpeg import bench_commands, realtime_factor, render_report, sample_command


def test_realtime_factor():
    assert realtime_factor(60, 30) == 2.0
    assert realtime_factor(60, 90) == 0.67
    with pytest.raises(ValueError):
        realtime_factor(60, 0)


def test_bench_commands_match_spec_presets(tmp_path):
    cmds = bench_commands(Path("sample.mp4"), tmp_path)
    assert set(cmds) == {"proxy", "draft", "final"}
    proxy, draft, final = cmds["proxy"], cmds["draft"], cmds["final"]
    assert proxy[0] == "ffmpeg" and "-i" in proxy and "sample.mp4" in proxy
    assert proxy[proxy.index("-preset") + 1] == "veryfast" and proxy[proxy.index("-crf") + 1] == "28"
    assert "-g" in proxy and proxy[proxy.index("-g") + 1] == "30"
    assert draft[draft.index("-preset") + 1] == "ultrafast" and draft[draft.index("-crf") + 1] == "26"
    assert draft[draft.index("-vf") + 1] == "scale=-2:720"
    assert final[final.index("-preset") + 1] == "veryfast" and final[final.index("-crf") + 1] == "20"
    assert final[final.index("-vf") + 1] == "scale=-2:1080"
    assert final[-1] == str(tmp_path / "final.mp4")


def test_sample_command_is_4k_testsrc(tmp_path):
    cmd = sample_command(tmp_path / "s.mp4", seconds=60)
    assert "testsrc2=size=3840x2160:rate=30" in cmd and cmd[cmd.index("-t") + 1] == "60"


def test_render_report_has_table_rows():
    text = render_report("vm-1", 4, 60.0, {"proxy": 20.0, "draft": 10.0, "final": 40.0})
    assert "| proxy | 20.0 | 3.0× |" in text
    assert "| final | 40.0 | 1.5× |" in text
    assert "vm-1" in text and "4 потоков" in text
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_bench.py`
Expected: `ModuleNotFoundError: No module named 'tools'`

- [ ] **Step 3: Написать `tools/__init__.py` (пустой) и `tools/bench_ffmpeg.py`**

```python
"""Замер скорости ffmpeg на этой машине: прокси / draft / final по пресетам спеки.

Запуск на VM из корня репо:  .venv/bin/python tools/bench_ffmpeg.py
Образец 4K генерируется сам (testsrc2 + sine, 60 с), либо передаётся свой файл: --sample path.mp4
Отчёт пишется в docs/benchmarks/<дата>-<хост>.md
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

PROXY_SCALE = "scale=w='if(gte(iw,ih),854,-2)':h='if(gte(iw,ih),-2,854)'"


def sample_command(path: Path, seconds: int) -> list[str]:
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(seconds),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ]


def bench_commands(sample: Path, out_dir: Path) -> dict[str, list[str]]:
    common = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(sample)]
    return {
        "proxy": common + [
            "-vf", PROXY_SCALE, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out_dir / "proxy.mp4"),
        ],
        "draft": common + [
            "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(out_dir / "draft.mp4"),
        ],
        "final": common + [
            "-vf", "scale=-2:1080", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(out_dir / "final.mp4"),
        ],
    }


def realtime_factor(media_seconds: float, elapsed_seconds: float) -> float:
    if elapsed_seconds <= 0:
        raise ValueError("elapsed_seconds must be > 0")
    return round(media_seconds / elapsed_seconds, 2)


def render_report(host: str, cpu_count: int, media_seconds: float, results: dict[str, float]) -> str:
    lines = [
        f"# Замер ffmpeg: {host}",
        "",
        f"- Дата: {date.today().isoformat()}",
        f"- CPU: {cpu_count} потоков",
        f"- Образец: {media_seconds:.0f} с, 3840x2160, 30 fps",
        "",
        "| Задача | Время, с | Быстрее реального времени |",
        "|---|---|---|",
    ]
    for name, elapsed in results.items():
        lines.append(f"| {name} | {elapsed:.1f} | {realtime_factor(media_seconds, elapsed)}× |")
    return "\n".join(lines) + "\n"


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def timed(cmd: list[str]) -> float:
    started = time.perf_counter()
    subprocess.run(cmd, check=True)
    return time.perf_counter() - started


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=Path, help="свой файл вместо сгенерированного образца")
    parser.add_argument("--seconds", type=int, default=60, help="длина генерируемого образца")
    parser.add_argument("--work", type=Path, default=Path("data/bench"), help="рабочий каталог")
    parser.add_argument("--out", type=Path, default=Path("docs/benchmarks"), help="куда писать отчёт")
    args = parser.parse_args(argv)

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg/ffprobe не найдены в PATH", file=sys.stderr)
        return 2
    args.work.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.sample is None:
        sample = args.work / "sample_4k.mp4"
        print(f"генерирую образец {args.seconds} с 4K…", flush=True)
        subprocess.run(sample_command(sample, args.seconds), check=True)
    else:
        sample = args.sample
    media_seconds = probe_duration(sample)

    results: dict[str, float] = {}
    for name, cmd in bench_commands(sample, args.work).items():
        print(f"{name}…", end=" ", flush=True)
        results[name] = timed(cmd)
        print(f"{results[name]:.1f} с", flush=True)

    host = platform.node() or "unknown"
    report = render_report(host, os.cpu_count() or 0, media_seconds, results)
    path = args.out / f"{date.today().isoformat()}-{host}.md"
    path.write_text(report, encoding="utf-8")
    print()
    print(report)
    print("отчёт:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/test_bench.py`
Expected: `4 passed`

- [ ] **Step 5: Прогнать замер локально, если ffmpeg есть на машине разработчика** (иначе только на VM в Task 13)

Run: `uv run python tools/bench_ffmpeg.py --seconds 10 --out data/bench-report`
Expected: три строки с временем и таблица; отчёт в `data/bench-report/` (каталог `data/` в gitignore, локальный отчёт не коммитится).

- [ ] **Step 6: Commit**

```bash
git add tools tests/test_bench.py
git commit -m "feat(tools): ffmpeg benchmark script with markdown report

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: Живая проверка на VM

Ручные шаги; выполняет владелец с доступом к VM. Каждый шаг с ожидаемым результатом.

- [ ] **Step 1: Опубликовать репозиторий**

Репозиторий `glmelnik96/Editing-site` создан 2026-09-04; `main` (fast-forward до M0) и ветка `m0-skeleton-auth` запушены: `git remote add origin git@github.com:glmelnik96/Editing-site.git`, `git push -u origin main m0-skeleton-auth`.

Expected: ветка `main` на GitHub с кодом M0.

- [ ] **Step 2: DNS и Yandex OAuth**

A-запись поддомена (например `video.cloudrudesign.ru`) на IP новой VM. На https://oauth.yandex.ru/client/new создать приложение: платформа «Веб-сервисы», redirect URI `https://<домен>/api/v1/auth/callback`, доступы `login:email`, `login:info`. Сохранить ClientID и Client secret.

- [ ] **Step 3: Bootstrap на VM**

```bash
ssh <user>@<vm-ip>
sudo apt-get install -y git
git clone https://github.com/glmelnik96/Editing-site.git /tmp/editing-site
sudo bash /tmp/editing-site/deploy/bootstrap.sh <домен> https://github.com/glmelnik96/Editing-site.git
```

Expected: последняя строка `bootstrap done…`, `systemctl is-active caddy` печатает `active`, `sudo caddy validate --config /etc/caddy/Caddyfile` печатает `Valid configuration`. Если репозиторий приватный: сгенерировать на VM ключ `ssh-keygen -t ed25519 -N "" -f /etc/editing-site/deploy_key` (каталог создать заранее), публичную часть добавить в GitHub → Settings → Deploy keys, и передавать bootstrap SSH-адрес `git@github.com:glmelnik96/Editing-site.git`.

- [ ] **Step 4: Заполнить `.env` и задеплоить**

```bash
sudo -u video nano /opt/editing-site/.env
sudo bash /opt/editing-site/deploy/deploy.sh
```

Expected: `active`, затем JSON `{"status":"ok","db":true,...}` и `deploy ok: <sha>`.

- [ ] **Step 5: Проверить вход и токен**

Открыть `https://<домен>/` в браузере, нажать «Войти через Яндекс», войти под `VIDEO_ADMIN_EMAIL`.
Expected: страница настроек с почтой в шапке и пустой таблицей токенов, ниже блок «Разрешённые адреса».

Выпустить токен «agent», скопировать секрет, затем с локальной машины:

```bash
curl -s -H "Authorization: Bearer vt_..." https://<домен>/api/v1/me
```

Expected: `{"id":"usr_...","email":"...","name":"...","role":"admin","auth":"token"}`.

Добавить в whitelist почту коллеги, войти под ней в приватном окне.
Expected: вход проходит, роль `user`, блока «Разрешённые адреса» нет.

- [ ] **Step 6: Замер производительности**

```bash
cd /opt/editing-site && sudo -u video .venv/bin/python tools/bench_ffmpeg.py --out /tmp/bench-report --work /srv/video/tmp/bench
cat /tmp/bench-report/*.md
```

Expected: таблица с тремя строками. Скопировать содержимое отчёта в локальный репозиторий в `docs/benchmarks/<дата>-<хост>.md`. Отчёт и образец пишутся вне репозитория на VM: файл внутри `/opt/editing-site/docs` сломал бы следующий `git merge --ff-only` в deploy.sh.

- [ ] **Step 7: Зафиксировать пресеты**

Если `final` медленнее 1× реального времени, в спеке (раздел 9.2) поменять пресет `final` на `superfast` и записать причину в отчёт. Если `proxy` медленнее 2×, в спеке (раздел 7) снизить длинную сторону прокси до 640 px.

- [ ] **Step 8: Commit отчёта**

```bash
git add docs/benchmarks
git commit -m "docs: ffmpeg benchmark report from the VM

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push
```

---

## Самопроверка плана

**Покрытие спеки (M0 по разделу 15):** репозиторий и тулчейн (Task 1), настройки (Task 2), SQLite и миграции (Task 3), формат ошибок и защита (Task 4), `/healthz` и фабрика (Task 5), сессии и токены как модули (Task 6), Yandex OAuth + whitelist + `/me` + лимит входа (Task 7), `/api/v1/tokens` (Task 8), `/api/v1/admin/*` (Task 9), страница настроек (Task 10), Caddy + systemd + bootstrap + deploy (Task 11), замер (Task 12), живой прогон и фиксация пресетов (Task 13). OpenAPI по `/api/v1/openapi.json` задаётся в Task 5.

**Осознанно не в M0:** квота в `/me` (нужны ассеты, M1), `/files/*` и forward_auth (M1), воркер и пульс (M1; `/healthz` уже умеет его читать), egress-allowlist (M4).

**Согласованность имён между задачами:** `SESSION_COOKIE` живёт в `server/app/security.py`, `sessions.py` импортирует его (Task 6); `resolve_session` возвращает строку с `session_id` и полями пользователя, `resolve_token` — с `token_id`; `CurrentUser.auth` принимает `"cookie"` или `"token"`; `disk_free_pct` определён в `health.py` и переиспользуется в `admin/routes.py`; `normalize_email` определён в `users.py` и переиспользуется в `admin/routes.py`; `client_ip` из `security.py` используется в `routes.py`.
