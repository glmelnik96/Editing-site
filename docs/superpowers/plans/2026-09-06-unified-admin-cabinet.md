# Единый кабинет администрирования — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ ПОД-НАВЫК: выполнять этот план задача за задачей через
> superpowers:subagent-driven-development. Шаги помечены чекбоксами (`- [ ]`).

**Цель:** страница в админке Editing site, где один человек заводится и снимается сразу в трёх сервисах ВМ, а отказ любого из соседей виден честно и не ломает остальные.

**Архитектура:** агрегатор. Данные остаются у каждого сервиса; наш сервер ходит к соседям по loopback со служебным токеном в заголовке `X-Service-Token`, свой список правит прямым вызовом функций. Спека — `docs/superpowers/specs/2026-09-05-unified-admin-design.md`.

**Технологии:** FastAPI, httpx (уже в зависимостях), pydantic-settings, TypeScript без фреймворков, pytest + vitest.

---

## Проверено на живой ВМ перед написанием плана (2026-09-06)

| Что | Как оказалось |
|---|---|
| Presentation Remote (`127.0.0.1:8014`) | Служебный токен принят: `GET /api/admin/allowed` → `200`. Без заголовка → `401`, с чужим токеном → `401` |
| Форма ответа stream | Голый массив: `[{"email", "note", "added_by", "created_at"}]` |
| VideoBoard (`127.0.0.1:8020`) | `403 service_forbidden`. Причина найдена: в `/opt/videoboard/.env` переменная названа `SERVICE_TOKEN`, а настройки сервиса читают её с префиксом — `BOARD_SERVICE_TOKEN`. Значение то же, что у нас (отпечатки sha256 совпали) |
| Форма ответа board | По их ответному письму `VideoBoard/docs/REPLY-to-unified-admin.md`: `{"emails": [{"email", "added_by", "added_at"}]}`, ошибки `{"error": {"code", "message", "details"}}` |
| Наши секреты | `STREAM_SERVICE_TOKEN` и `BOARD_SERVICE_TOKEN` лежат в `/opt/editing-site/.env` **без префикса `VIDEO_`** |
| Список board после починки | `200 {"emails": []}` — пустой. Администратор в нём **не числится**, доступ у него из конфигурации сервиса |
| `validation_alias` в pydantic-settings | Отменяет `env_prefix` для конкретного поля: проверено на этой версии — `STREAM_SERVICE_TOKEN` читается, остальные поля продолжают читаться с `VIDEO_` |

Из последних двух строк следует главное требование к задаче 1: имена служебных секретов в настройках должны быть **без префикса**, иначе мы наступим ровно на те же грабли, что уложили соседа.

---

## Решения по ходу планирования

**Одна ручка на изменение, а не три.** `POST /api/v1/admin/cabinet/access` принимает адрес и списки «дать» и «снять» и отвечает результатом по каждому сервису. Отдельной ручки «убрать отовсюду» нет: это тот же запрос со снятием во всех сервисах, где человек есть. Второй способ делать то же самое — второе место, где можно ошибиться.

**Частичный успех отвечает `200`.** Три сервиса — три независимые операции, а не транзакция (спека §7). Код ответа один, правду несёт тело: по строке на сервис. Откатывать удавшееся мы не будем — это означало бы врать про атомарность.

**Синхронный httpx, соседи опрашиваются по очереди.** Оба на loopback, ответ занимает миллисекунды; худший случай при зависшем соседе — два таймаута по 3 с на странице администратора. Асинхронность здесь купила бы секунды раз в день ценой второго стиля в кодовой базе.

**404 при снятии — это успех.** «Снять доступ у того, кого в списке нет» — цель достигнута. Иначе кабинет ругался бы на нормальный исход.

**Своего служебного токена мы не заводим.** Кабинет живёт внутри Editing site и правит наш список прямым вызовом функций, а не HTTP-запросом к самому себе. Соседям это уже написано в `Prezentation stream/docs/NEIGHBOR-REPLY-from-editing-site.md`; принимать `X-Service-Token` на своих маршрутах не нужно.

**Строка администратора не показывает галочки вовсе.** Живая проверка: у board белый список пуст, а администратор доступ имеет — он задан конфигурацией сервиса. Галочка «нет доступа» в его строке была бы прямой неправдой. Поэтому в строке администратора во всех столбцах стоит подпись «из конфигурации», а не переключатель. Членство в списках при этом собирается как есть — врёт не факт, а его подача.

**Проверка администратора на сервере, а не только в интерфейсе.** Заблокированные галочки — удобство; отказ `422 cannot_change_admin` — гарантия.

---

## Структура файлов

| Файл | За что отвечает |
|---|---|
| `server/app/admin/services.py` (создать) | Описание соседа (`RemoteService`), клиент к нему, нормализация ответов, виды отказов |
| `server/app/admin/store.py` (создать) | Наш белый список функциями: список, добавить, убрать. Общий для обычной админки и кабинета |
| `server/app/admin/cabinet.py` (создать) | Сборка трёх списков в одну таблицу и применение правок с результатом по каждому сервису |
| `server/app/admin/routes.py` (изменить) | Ручки кабинета; существующие ручки whitelist переводятся на `store.py` |
| `server/app/config.py` (изменить) | Секреты соседей без префикса, адреса и таймаут |
| `.env.example` (изменить) | Две переменные пустыми с объяснением |
| `web/src/cabinet.ts` (создать) | Таблица кабинета: галочки, добавление, снятие отовсюду, показ отказов |
| `web/src/main.ts` (изменить) | Монтирование кабинета в разделе администратора |
| `tests/test_admin_services.py` (создать) | Адаптер на подменённом транспорте |
| `tests/test_admin_cabinet.py` (создать) | Сборка таблицы и ручки кабинета |
| `tests/test_config.py` (изменить) | Имена служебных переменных без префикса |

---

### Task 1: Настройки и описание соседей

**Files:**
- Modify: `server/app/config.py`, `.env.example`
- Test: `tests/test_config.py`

- [ ] **Step 1: Тест на имена переменных**

Добавить в `tests/test_config.py`:

```python
def test_service_tokens_are_read_without_prefix(monkeypatch):
    """Секреты соседей разложены на ВМ без префикса VIDEO_. Ровно на этом сосед и споткнулся:
    переменную назвали SERVICE_TOKEN, а настройки искали BOARD_SERVICE_TOKEN, и токен молча не работал."""
    monkeypatch.setenv("STREAM_SERVICE_TOKEN", "s-secret")
    monkeypatch.setenv("BOARD_SERVICE_TOKEN", "b-secret")
    monkeypatch.setenv("VIDEO_STREAM_SERVICE_TOKEN", "мимо")
    s = Settings(_env_file=None)
    assert s.stream_service_token == "s-secret"
    assert s.board_service_token == "b-secret"


def test_service_tokens_default_to_empty():
    s = Settings(_env_file=None)
    assert s.stream_service_token == "" and s.board_service_token == ""
    assert s.board_base_url == "http://127.0.0.1:8020"
    assert s.stream_base_url == "http://127.0.0.1:8014"
```

- [ ] **Step 2: Прогон — тест падает**

Run: `uv run python -m pytest tests/test_config.py -q`
Expected: FAIL, `AttributeError: 'Settings' object has no attribute 'stream_service_token'`

- [ ] **Step 3: Поля настроек**

В `server/app/config.py` заменить строку импорта pydantic на:

```python
from pydantic import AliasChoices, Field, ValidationInfo, field_validator
```

и добавить в класс `Settings` после блока рендера:

```python
    # Единый кабинет: соседи по ВМ. Служебные секреты названы БЕЗ префикса VIDEO_ — так они
    # разложены в /opt/editing-site/.env (спека §4). validation_alias отменяет env_prefix
    # только для этих двух полей, остальные читаются как раньше.
    stream_service_token: str = Field(default="", validation_alias=AliasChoices("STREAM_SERVICE_TOKEN"))
    board_service_token: str = Field(default="", validation_alias=AliasChoices("BOARD_SERVICE_TOKEN"))
    # Ходим на loopback: три сервиса на одной машине, Caddy и интернет тут не при чём.
    board_base_url: str = "http://127.0.0.1:8020"
    stream_base_url: str = "http://127.0.0.1:8014"
    service_timeout_sec: float = Field(default=3.0, gt=0, le=30)
```

- [ ] **Step 4: `.env.example`**

Добавить в конец, перед `VIDEO_LOG_LEVEL`:

```
# Единый кабинет администрирования: служебные секреты соседей по ВМ.
# Имена БЕЗ префикса VIDEO_ — именно так они лежат в /opt/editing-site/.env.
# Пустое значение запрещает служебный доступ к соседу, а не разрешает: сосед будет помечен
# «не настроен», кнопки к нему выключены.
STREAM_SERVICE_TOKEN=
BOARD_SERVICE_TOKEN=
# Адреса соседей на loopback (менять незачем, пока они на этой же машине)
VIDEO_BOARD_BASE_URL=http://127.0.0.1:8020
VIDEO_STREAM_BASE_URL=http://127.0.0.1:8014
VIDEO_SERVICE_TIMEOUT_SEC=3
```

- [ ] **Step 5: Прогон и коммит**

Run: `uv run python -m pytest tests/test_config.py -q && uv run ruff check .`
Expected: PASS

```bash
git add server/app/config.py .env.example tests/test_config.py
git commit -m "feat(admin): service token settings for neighbour services"
```

---

### Task 2: Адаптер соседних сервисов

**Files:**
- Create: `server/app/admin/services.py`
- Test: `tests/test_admin_services.py`

- [ ] **Step 1: Тесты адаптера**

Создать `tests/test_admin_services.py`:

```python
import httpx
import pytest

from server.app.admin.services import (
    Person,
    RemoteClient,
    ServiceError,
    remote_services,
)
from server.app.config import Settings

BOARD_BODY = {"emails": [{"email": "a@x.ru", "added_by": "admin@x.ru", "added_at": "2026-09-06T00:00:00Z"}]}
STREAM_BODY = [{"email": "a@x.ru", "note": "коллега", "added_by": "unified-admin", "created_at": "2026-09-06"}]


def settings(**kw) -> Settings:
    return Settings(_env_file=None, board_service_token="b", stream_service_token="s", **kw)


def service(key: str, **kw):
    return next(s for s in remote_services(settings(**kw)) if s.key == key)


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_board_list_is_normalized():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/admin/whitelist"
        assert request.headers["X-Service-Token"] == "b"
        return httpx.Response(200, json=BOARD_BODY)

    people = RemoteClient(service("board"), client_for(handler)).list()
    assert people == [Person(email="a@x.ru", note="", added_by="admin@x.ru")]


def test_stream_list_is_normalized():
    """У соседа список приходит голым массивом и с заметкой — форма другая, запись одна и та же."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/admin/allowed"
        return httpx.Response(200, json=STREAM_BODY)

    people = RemoteClient(service("stream"), client_for(handler)).list()
    assert people == [Person(email="a@x.ru", note="коллега", added_by="unified-admin")]


def test_add_and_remove_hit_the_right_paths():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(201 if request.method == "POST" else 204)

    client = RemoteClient(service("stream"), client_for(handler))
    client.add("Some.One@X.ru")
    client.remove("Some.One@X.ru")
    assert seen == [("POST", "/api/admin/allowed"), ("DELETE", "/api/admin/allowed/Some.One%40X.ru")]


def test_missing_person_on_remove_is_success():
    """Снять доступ у того, кого в списке нет, — цель достигнута, ругаться не на что."""
    client = RemoteClient(service("stream"), client_for(lambda r: httpx.Response(404, json={"detail": "нет"})))
    client.remove("ghost@x.ru")


@pytest.mark.parametrize("status,kind", [(401, "forbidden"), (403, "forbidden"), (500, "bad_response")])
def test_refusals_are_told_apart(status, kind):
    client = RemoteClient(service("board"), client_for(lambda r: httpx.Response(status, json={})))
    with pytest.raises(ServiceError) as exc:
        client.list()
    assert exc.value.kind == kind


def test_timeout_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("не дождались", request=request)

    client = RemoteClient(service("board"), client_for(handler))
    with pytest.raises(ServiceError) as exc:
        client.list()
    assert exc.value.kind == "unavailable"


def test_empty_token_forbids_instead_of_asking():
    """Незаданный секрет запрещает, а не разрешает: забытая переменная не должна открывать список."""
    called = []
    client = RemoteClient(service("board", board_service_token=""), client_for(lambda r: called.append(1)))
    with pytest.raises(ServiceError) as exc:
        client.list()
    assert exc.value.kind == "unconfigured" and not called


def test_garbage_body_is_bad_response():
    client = RemoteClient(service("board"), client_for(lambda r: httpx.Response(200, text="не json")))
    with pytest.raises(ServiceError) as exc:
        client.list()
    assert exc.value.kind == "bad_response"
```

- [ ] **Step 2: Прогон — тесты падают**

Run: `uv run python -m pytest tests/test_admin_services.py -q`
Expected: FAIL, `ModuleNotFoundError: server.app.admin.services`

- [ ] **Step 3: Адаптер**

Создать `server/app/admin/services.py`:

```python
"""Соседние сервисы на этой же ВМ: чтение и правка их белых списков.

Кабинет ходит к ним по loopback со служебным токеном в заголовке X-Service-Token (спека §4).
Токен не даёт ни сессии, ни роли — это право выполнить операцию со списком, и больше ничего.

Формы ответов у соседей разные, поэтому у каждого своё чтение списка; наружу адаптер отдаёт
одинаковые записи, и кабинет про различия не знает.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from server.app.config import Settings


@dataclass(frozen=True)
class Person:
    email: str
    note: str = ""
    added_by: str = ""


class ServiceError(Exception):
    """Отказ соседа. kind различает случаи, которые кабинет показывает по-разному (спека §7):
    unconfigured — секрет не задан, unavailable — не ответил, forbidden — не принял токен,
    bad_response — ответил не тем."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class RemoteService:
    key: str
    title: str
    base_url: str
    list_path: str
    item_path: str
    token: str
    read_list: Callable[[object], list[Person]]


def _read_board(body: object) -> list[Person]:
    """У VideoBoard список лежит в поле emails, заметок у него нет."""
    rows = body.get("emails", []) if isinstance(body, dict) else []
    return [
        Person(email=r["email"], added_by=r.get("added_by") or "")
        for r in rows
        if isinstance(r, dict) and r.get("email")
    ]


def _read_stream(body: object) -> list[Person]:
    """У Presentation Remote список приходит голым массивом и с заметкой."""
    rows = body if isinstance(body, list) else []
    return [
        Person(email=r["email"], note=r.get("note") or "", added_by=r.get("added_by") or "")
        for r in rows
        if isinstance(r, dict) and r.get("email")
    ]


def remote_services(settings: Settings) -> list[RemoteService]:
    """Порядок здесь задаёт порядок столбцов в кабинете."""
    return [
        RemoteService(
            key="board",
            title="Доска",
            base_url=settings.board_base_url,
            list_path="/api/v1/admin/whitelist",
            item_path="/api/v1/admin/whitelist/{email}",
            token=settings.board_service_token,
            read_list=_read_board,
        ),
        RemoteService(
            key="stream",
            title="Трансляции",
            base_url=settings.stream_base_url,
            list_path="/api/admin/allowed",
            item_path="/api/admin/allowed/{email}",
            token=settings.stream_service_token,
            read_list=_read_stream,
        ),
    ]


def build_client(settings: Settings) -> httpx.Client:
    """Отдельной функцией, чтобы тесты подменяли её транспортом-заглушкой."""
    return httpx.Client(timeout=settings.service_timeout_sec)


class RemoteClient:
    """Один сосед. Клиент передаётся снаружи: в тестах это httpx.MockTransport."""

    def __init__(self, service: RemoteService, client: httpx.Client) -> None:
        self.service = service
        self._client = client

    def _request(self, method: str, path: str, json: dict | None = None) -> httpx.Response:
        if not self.service.token:
            raise ServiceError("unconfigured", f"{self.service.title}: служебный токен не задан")
        try:
            resp = self._client.request(
                method,
                self.service.base_url + path,
                headers={"X-Service-Token": self.service.token},
                json=json,
            )
        except httpx.HTTPError as exc:
            raise ServiceError("unavailable", f"{self.service.title}: не отвечает") from exc
        # 401 и 403 у соседей значат одно и то же — «токен не принят»; различие в кодах у них
        # историческое, и тащить его в интерфейс незачем.
        if resp.status_code in (401, 403):
            raise ServiceError("forbidden", f"{self.service.title}: служебный токен не принят")
        if resp.status_code >= 400:
            raise ServiceError("bad_response", f"{self.service.title}: ответил кодом {resp.status_code}")
        return resp

    def list(self) -> list[Person]:
        resp = self._request("GET", self.service.list_path)
        try:
            body = resp.json()
        except ValueError as exc:
            raise ServiceError("bad_response", f"{self.service.title}: ответ не разобрать") from exc
        return self.service.read_list(body)

    def add(self, email: str) -> None:
        self._request("POST", self.service.list_path, json={"email": email})

    def remove(self, email: str) -> None:
        path = self.service.item_path.format(email=quote(email, safe=""))
        try:
            self._request("DELETE", path)
        except ServiceError as exc:
            # Человека и так нет в списке — снятие достигло цели.
            if exc.kind != "bad_response" or "404" not in str(exc):
                raise
```

- [ ] **Step 4: Прогон и коммит**

Run: `uv run python -m pytest tests/test_admin_services.py -q && uv run ruff check .`
Expected: PASS, 9 тестов

```bash
git add server/app/admin/services.py tests/test_admin_services.py
git commit -m "feat(admin): adapter for neighbour whitelist APIs"
```

---

### Task 3: Свой список функциями

**Files:**
- Create: `server/app/admin/store.py`
- Modify: `server/app/admin/routes.py`
- Test: `tests/test_admin_api.py` (существующие тесты должны остаться зелёными)

Наш сервис кабинет правит прямым вызовом (спека §3), поэтому логика уезжает из ручек в модуль, а ручки становятся тонкими. Поведение не меняется — существующие тесты это и проверяют.

- [ ] **Step 1: Модуль**

Создать `server/app/admin/store.py`:

```python
"""Наш белый список: одни и те же функции для браузерной админки и для кабинета.

HTTP-запрос к самому себе означал бы второй способ делать то же самое и второе место,
где можно ошибиться (спека §3).
"""
from __future__ import annotations

import re
import sqlite3

from server.app.auth.users import normalize_email
from server.app.config import Settings
from server.app.errors import ApiError
from server.app.util import now_iso
from server.db.core import transaction

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(email: str) -> str:
    """Нормализованный адрес или ApiError 422."""
    normalized = normalize_email(email)
    if not EMAIL_RE.match(normalized):
        raise ApiError(422, "invalid_email", "Это не похоже на адрес почты")
    return normalized


def listing(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT email, added_by, added_at FROM whitelist ORDER BY added_at, email")
    return [dict(r) for r in rows]


def add(conn: sqlite3.Connection, email: str, *, added_by: str) -> dict:
    """Добавляет адрес и включает отключённую учётную запись обратно."""
    normalized = valid_email(email)
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO whitelist (email, added_by, added_at) VALUES (?, ?, ?)",
            (normalized, added_by, now_iso()),
        )
        conn.execute("UPDATE users SET disabled = 0 WHERE email = ?", (normalized,))
    row = conn.execute(
        "SELECT email, added_by, added_at FROM whitelist WHERE email = ?", (normalized,)
    ).fetchone()
    return dict(row)


def remove(conn: sqlite3.Connection, settings: Settings, email: str) -> None:
    """Убирает адрес, отключает учётную запись и гасит её сессии: человека выкидывает сразу."""
    normalized = normalize_email(email)
    if normalized == normalize_email(settings.admin_email):
        raise ApiError(409, "cannot_remove_admin", "Администратор из конфигурации всегда в списке")
    with transaction(conn):
        cur = conn.execute("DELETE FROM whitelist WHERE email = ?", (normalized,))
        if cur.rowcount == 0:
            raise ApiError(404, "not_found", "Адреса нет в списке")
        conn.execute("UPDATE users SET disabled = 1 WHERE email = ?", (normalized,))
        conn.execute(
            "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email = ?)", (normalized,)
        )
```

- [ ] **Step 2: Ручки через модуль**

В `server/app/admin/routes.py` убрать `EMAIL_RE`, импорты `re`, `normalize_email`, `now_iso`, `transaction` и переписать три ручки:

```python
@router.get("/whitelist", response_model=WhitelistList)
def whitelist_list(
    _: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> WhitelistList:
    return WhitelistList(emails=[WhitelistEntry(**row) for row in store.listing(conn)])


@router.post("/whitelist", status_code=201, response_model=WhitelistEntry)
def whitelist_add(
    body: WhitelistAdd,
    admin: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> WhitelistEntry:
    return WhitelistEntry(**store.add(conn, body.email, added_by=admin.email))


@router.delete("/whitelist/{email}", status_code=204)
def whitelist_remove(
    request: Request,
    email: str,
    _: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    store.remove(conn, request.app.state.settings, email)
    return Response(status_code=204)
```

и добавить импорт `from server.app.admin import store`.

- [ ] **Step 3: Прогон и коммит**

Run: `uv run python -m pytest tests/test_admin_api.py -q && uv run ruff check .`
Expected: PASS, все прежние тесты зелёные без правок

```bash
git add server/app/admin/store.py server/app/admin/routes.py
git commit -m "refactor(admin): whitelist logic in a module shared with the cabinet"
```

---

### Task 4: Сборка таблицы и применение правок

**Files:**
- Create: `server/app/admin/cabinet.py`
- Test: `tests/test_admin_cabinet.py` (часть 1)

- [ ] **Step 1: Тесты сборки**

Создать `tests/test_admin_cabinet.py`:

```python
import sqlite3

import httpx
import pytest

from server.app.admin import cabinet
from server.app.admin.services import RemoteClient, remote_services
from server.app.config import Settings
from server.app.util import now_iso
from server.db.migrate import migrate

ADMIN = "admin@x.ru"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    migrate(c)
    for email in (ADMIN, "own@x.ru"):
        c.execute("INSERT INTO whitelist (email, added_by, added_at) VALUES (?, 'setup', ?)",
                  (email, now_iso()))
    yield c
    c.close()


def make_settings(**kw) -> Settings:
    """Отдельное имя, не settings: так называется фикстура приложения из conftest."""
    return Settings(_env_file=None, admin_email=ADMIN, board_service_token="b",
                    stream_service_token="s", **kw)


def clients(handler, s: Settings) -> list[RemoteClient]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return [RemoteClient(svc, client) for svc in remote_services(s)]


def both_ok(request: httpx.Request) -> httpx.Response:
    if request.url.path.startswith("/api/v1/admin"):
        return httpx.Response(200, json={"emails": [{"email": "own@x.ru", "added_by": "admin@x.ru"}]})
    return httpx.Response(200, json=[{"email": "stream-only@x.ru", "note": "", "added_by": ""}])


def test_three_lists_become_one_table(conn):
    s = make_settings()
    view = cabinet.collect(conn, s, clients(both_ok, s))
    assert [svc.key for svc in view.services] == ["video", "board", "stream"]
    assert all(svc.state == "ok" for svc in view.services)
    rows = {p.email: p.access for p in view.people}
    assert rows["own@x.ru"] == {"video": True, "board": True, "stream": False}
    assert rows["stream-only@x.ru"] == {"video": False, "board": False, "stream": True}


def test_admin_row_is_marked(conn):
    s = make_settings()
    view = cabinet.collect(conn, s, clients(both_ok, s))
    admin_row = next(p for p in view.people if p.email == ADMIN)
    assert admin_row.admin is True


def test_admin_missing_from_a_neighbour_list_is_not_a_denial(conn):
    """У board список пуст, а доступ у администратора есть — он из конфигурации сервиса.
    Строка администратора помечена, и интерфейс рисует в ней подпись, а не пустую галочку."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/admin"):
            return httpx.Response(200, json={"emails": []})
        return httpx.Response(200, json=[{"email": ADMIN}])

    s = make_settings()
    view = cabinet.collect(conn, s, clients(handler, s))
    admin_row = next(p for p in view.people if p.email == ADMIN)
    assert admin_row.admin is True


def test_dead_neighbour_does_not_break_the_others(conn):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/admin"):
            raise httpx.ConnectError("сосед лёг", request=request)
        return httpx.Response(200, json=[{"email": "s@x.ru"}])

    s = make_settings()
    view = cabinet.collect(conn, s, clients(handler, s))
    states = {svc.key: svc.state for svc in view.services}
    assert states == {"video": "ok", "board": "unavailable", "stream": "ok"}
    # У недоступного сервиса доступ неизвестен, а не «нет»: врать про чужое состояние нельзя.
    assert next(p for p in view.people if p.email == "s@x.ru").access["board"] is None


def test_unconfigured_neighbour_is_told_apart(conn):
    s = make_settings(board_service_token="")
    view = cabinet.collect(conn, s, clients(both_ok, s))
    assert next(svc for svc in view.services if svc.key == "board").state == "unconfigured"


def test_apply_reports_each_service(conn):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.startswith("/api/v1/admin"):
            return httpx.Response(403, json={})
        return httpx.Response(201, json={"ok": True})

    s = make_settings()
    results = cabinet.apply(conn, s, clients(handler, s), email="new@x.ru",
                            grant=["video", "board", "stream"], revoke=[], added_by=ADMIN)
    assert {r.service: r.ok for r in results} == {"video": True, "board": False, "stream": True}
    assert "токен не принят" in next(r for r in results if r.service == "board").error
    assert conn.execute("SELECT count(*) FROM whitelist WHERE email = 'new@x.ru'").fetchone()[0] == 1


def test_apply_refuses_to_touch_the_admin(conn):
    s = make_settings()
    with pytest.raises(cabinet.CabinetError):
        cabinet.apply(conn, s, clients(both_ok, s), email=ADMIN, grant=[], revoke=["video"],
                      added_by=ADMIN)


def test_apply_rejects_unknown_service(conn):
    s = make_settings()
    with pytest.raises(cabinet.CabinetError):
        cabinet.apply(conn, s, clients(both_ok, s), email="new@x.ru", grant=["мимо"], revoke=[],
                      added_by=ADMIN)
```

- [ ] **Step 2: Прогон — падает**

Run: `uv run python -m pytest tests/test_admin_cabinet.py -q`
Expected: FAIL, `ModuleNotFoundError: server.app.admin.cabinet`

- [ ] **Step 3: Сборка**

Создать `server/app/admin/cabinet.py`:

```python
"""Кабинет: три белых списка в одной таблице и правки с результатом по каждому сервису.

Свой список правится прямым вызовом, соседские — через адаптер. Частичный успех не откатываем:
три сервиса — это три независимые операции, а не транзакция, и притворяться иначе значит врать
(спека §7).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from server.app.admin import store
from server.app.admin.services import Person, RemoteClient, ServiceError
from server.app.auth.users import normalize_email
from server.app.config import Settings
from server.app.errors import ApiError

OWN_KEY = "video"
OWN_TITLE = "Видео"


class CabinetError(Exception):
    """Отказ до того, как что-либо изменено: неизвестный сервис, чужой адрес, администратор."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ServiceState:
    key: str
    title: str
    state: str  # ok | unconfigured | unavailable | forbidden | bad_response
    message: str = ""


@dataclass
class PersonRow:
    email: str
    admin: bool
    access: dict[str, bool | None]


@dataclass
class CabinetView:
    services: list[ServiceState]
    people: list[PersonRow] = field(default_factory=list)


@dataclass
class ChangeResult:
    service: str
    action: str  # grant | revoke
    ok: bool
    error: str | None = None


def collect(conn: sqlite3.Connection, settings: Settings, clients: list[RemoteClient]) -> CabinetView:
    """Читает три списка и сводит их в таблицу. Упавший сосед забирает с собой только свой столбец."""
    admin_email = normalize_email(settings.admin_email)
    services = [ServiceState(key=OWN_KEY, title=OWN_TITLE, state="ok")]
    lists: dict[str, list[Person] | None] = {
        OWN_KEY: [Person(email=row["email"], added_by=row["added_by"] or "") for row in store.listing(conn)]
    }
    for client in clients:
        try:
            lists[client.service.key] = client.list()
            services.append(ServiceState(key=client.service.key, title=client.service.title, state="ok"))
        except ServiceError as exc:
            lists[client.service.key] = None
            services.append(
                ServiceState(client.service.key, client.service.title, exc.kind, str(exc))
            )

    emails: set[str] = set()
    for people in lists.values():
        if people is not None:
            emails.update(normalize_email(p.email) for p in people)

    rows = []
    for email in sorted(emails):
        access: dict[str, bool | None] = {}
        for key, people in lists.items():
            # None значит «не знаем»: у недоступного соседа отсутствие адреса ничего не доказывает.
            access[key] = None if people is None else any(normalize_email(p.email) == email for p in people)
        rows.append(PersonRow(email=email, admin=bool(admin_email) and email == admin_email, access=access))
    return CabinetView(services=services, people=rows)


def apply(
    conn: sqlite3.Connection,
    settings: Settings,
    clients: list[RemoteClient],
    *,
    email: str,
    grant: list[str],
    revoke: list[str],
    added_by: str,
) -> list[ChangeResult]:
    """Применяет правки по сервисам и возвращает результат по каждому."""
    normalized = store.valid_email(email)
    known = {OWN_KEY} | {c.service.key for c in clients}
    unknown = (set(grant) | set(revoke)) - known
    if unknown:
        raise CabinetError("unknown_service", f"Неизвестный сервис: {', '.join(sorted(unknown))}")
    both = set(grant) & set(revoke)
    if both:
        raise CabinetError("contradictory_change", f"И дать, и снять сразу: {', '.join(sorted(both))}")
    if normalized == normalize_email(settings.admin_email):
        raise CabinetError("cannot_change_admin", "Доступ администратора задан конфигурацией сервисов")

    by_key = {c.service.key: c for c in clients}
    results: list[ChangeResult] = []
    for action, keys in (("grant", grant), ("revoke", revoke)):
        for key in keys:
            results.append(_one(conn, settings, by_key.get(key), key, action, normalized, added_by))
    return results


def _one(
    conn: sqlite3.Connection,
    settings: Settings,
    client: RemoteClient | None,
    key: str,
    action: str,
    email: str,
    added_by: str,
) -> ChangeResult:
    try:
        if key == OWN_KEY:
            if action == "grant":
                store.add(conn, email, added_by=added_by)
            else:
                _remove_own(conn, settings, email)
        elif client is not None:
            if action == "grant":
                client.add(email)
            else:
                client.remove(email)
    except (ServiceError, ApiError) as exc:
        message = exc.message if isinstance(exc, ApiError) else str(exc)
        return ChangeResult(service=key, action=action, ok=False, error=message)
    return ChangeResult(service=key, action=action, ok=True)


def _remove_own(conn: sqlite3.Connection, settings: Settings, email: str) -> None:
    """Отсутствие адреса — не ошибка: снятие достигло цели, как и у соседей."""
    try:
        store.remove(conn, settings, email)
    except ApiError as exc:
        if exc.code != "not_found":
            raise
```

- [ ] **Step 4: Прогон и коммит**

Run: `uv run python -m pytest tests/test_admin_cabinet.py -q && uv run ruff check .`
Expected: PASS, 7 тестов

```bash
git add server/app/admin/cabinet.py tests/test_admin_cabinet.py
git commit -m "feat(admin): merge three whitelists into one table"
```

---

### Task 5: Ручки кабинета

**Files:**
- Modify: `server/app/admin/routes.py`
- Test: `tests/test_admin_cabinet.py` (часть 2)

- [ ] **Step 1: Тесты ручек**

Дописать в `tests/test_admin_cabinet.py`:

```python
@pytest.fixture
def settings(settings):
    """Перекрываем фикстуру приложения, добавляя служебные секреты: без них соседи
    в кабинете помечены «не настроен» и до HTTP дело не доходит. Одноимённый параметр —
    та же фикстура из conftest (штатный приём pytest)."""
    settings.board_service_token = "b"
    settings.stream_service_token = "s"
    return settings


def patch_neighbours(monkeypatch, handler):
    """Подменяем построение клиента: в тестах наружу ходить нельзя."""
    from server.app.admin import routes

    monkeypatch.setattr(routes, "build_client", lambda s: httpx.Client(transport=httpx.MockTransport(handler)))


def test_cabinet_page(client, login_as, monkeypatch):
    patch_neighbours(monkeypatch, both_ok)
    login_as()
    body = client.get("/api/v1/admin/cabinet").json()
    assert [s["key"] for s in body["services"]] == ["video", "board", "stream"]
    assert all(isinstance(p["access"], dict) for p in body["people"])


def test_cabinet_change_reports_each_service(client, login_as, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={}) if "/api/v1/admin" in request.url.path else httpx.Response(201, json={})

    patch_neighbours(monkeypatch, handler)
    login_as()
    r = client.post("/api/v1/admin/cabinet/access",
                    json={"email": "new@x.ru", "grant": ["video", "board", "stream"]})
    assert r.status_code == 200
    results = {row["service"]: row["ok"] for row in r.json()["results"]}
    assert results == {"video": True, "board": False, "stream": True}


def test_cabinet_refuses_admin_and_unknown_service(client, login_as, monkeypatch):
    patch_neighbours(monkeypatch, both_ok)
    login_as()
    me = client.get("/api/v1/me").json()
    r = client.post("/api/v1/admin/cabinet/access", json={"email": me["email"], "revoke": ["video"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "cannot_change_admin"
    r = client.post("/api/v1/admin/cabinet/access", json={"email": "new@x.ru", "grant": ["мимо"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_service"


def test_cabinet_is_admin_and_browser_only(client, login_as, bearer_client, monkeypatch):
    """Кабинет управляет допуском, значит токену агента он закрыт — как и обычный whitelist."""
    patch_neighbours(monkeypatch, both_ok)
    assert bearer_client.get("/api/v1/admin/cabinet").status_code == 403
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@x.ru"}).status_code == 201
    login_as("other@x.ru", "Другой")
    assert client.get("/api/v1/admin/cabinet").status_code == 403
```

Посмотреть в `tests/conftest.py`, как `login_as` заводит администратора (первый вход по `VIDEO_ADMIN_EMAIL`), и при необходимости поправить вызовы.

- [ ] **Step 2: Прогон — падает**

Run: `uv run python -m pytest tests/test_admin_cabinet.py -q`
Expected: FAIL, 404 на `/api/v1/admin/cabinet`

- [ ] **Step 3: Ручки**

Добавить в `server/app/admin/routes.py`:

```python
from server.app.admin import cabinet as cabinet_mod
from server.app.admin.services import RemoteClient, build_client, remote_services


class ServiceItem(BaseModel):
    key: str
    title: str
    state: str
    message: str


class PersonItem(BaseModel):
    email: str
    admin: bool
    access: dict[str, bool | None]


class CabinetView(BaseModel):
    services: list[ServiceItem]
    people: list[PersonItem]


class AccessChange(BaseModel):
    email: str
    grant: list[str] = []
    revoke: list[str] = []


class ChangeItem(BaseModel):
    service: str
    action: str
    ok: bool
    error: str | None


class ChangeList(BaseModel):
    results: list[ChangeItem]


@router.get("/cabinet", response_model=CabinetView)
def cabinet_view(
    request: Request,
    _: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CabinetView:
    settings = request.app.state.settings
    with build_client(settings) as http:
        clients = [RemoteClient(svc, http) for svc in remote_services(settings)]
        view = cabinet_mod.collect(conn, settings, clients)
    return CabinetView(
        services=[ServiceItem(key=s.key, title=s.title, state=s.state, message=s.message) for s in view.services],
        people=[PersonItem(email=p.email, admin=p.admin, access=p.access) for p in view.people],
    )


@router.post("/cabinet/access", response_model=ChangeList)
def cabinet_access(
    request: Request,
    body: AccessChange,
    admin: CurrentUser = Depends(require_admin_cookie),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ChangeList:
    """Ответ всегда 200: частичный успех — обычный исход, и правда лежит в теле (спека §7)."""
    settings = request.app.state.settings
    with build_client(settings) as http:
        clients = [RemoteClient(svc, http) for svc in remote_services(settings)]
        try:
            results = cabinet_mod.apply(
                conn, settings, clients,
                email=body.email, grant=body.grant, revoke=body.revoke, added_by=admin.email,
            )
        except cabinet_mod.CabinetError as exc:
            raise ApiError(422, exc.code, str(exc)) from exc
    return ChangeList(results=[ChangeItem(**vars(r)) for r in results])
```

- [ ] **Step 4: Прогон и коммит**

Run: `uv run python -m pytest -q && uv run ruff check .`
Expected: PASS, вся база тестов зелёная

```bash
git add server/app/admin/routes.py tests/test_admin_cabinet.py
git commit -m "feat(admin): cabinet endpoints for the three services"
```

---

### Task 6: Страница кабинета

**Files:**
- Create: `web/src/cabinet.ts`
- Modify: `web/src/main.ts`, `web/src/style.css`
- Test: живая проверка

- [ ] **Step 1: Модуль**

Создать `web/src/cabinet.ts` — `mountCabinet(el)`:

```ts
import { api, ApiError } from './api'
import { escapeHtml } from './html'

export type ServiceItem = { key: string; title: string; state: string; message: string }
export type PersonItem = { email: string; admin: boolean; access: Record<string, boolean | null> }
export type CabinetView = { services: ServiceItem[]; people: PersonItem[] }
export type ChangeItem = { service: string; action: string; ok: boolean; error: string | null }
```

Поведение:

- таблица: строка — человек, столбец — сервис; в клетке галочка (`<input type="checkbox">`);
- клетка недоступного сервиса (`state !== 'ok'`) выключена, значение неизвестно — показывать `·`, а не пустую галочку;
- строка администратора: вместо галочек во всех столбцах подпись «из конфигурации» — у соседей его может не быть в списке, а доступ у него есть, и пустая галочка соврала бы;
- форма добавления: поле адреса и три галочки «куда пускаем», кнопка «Добавить»;
- кнопка «Убрать отовсюду» в строке — с подтверждением, в тексте которого сказано: **человека выкинет из сервиса сразу, а не когда-нибудь** (спека §6);
- после ответа показывать результат по каждому сервису: удалось или нет и почему; таблицу перечитывать;
- заголовок столбца недоступного сервиса подписан состоянием: «не настроен», «недоступен», «отказал в доступе».

Запросы: `GET /api/v1/admin/cabinet`, `POST /api/v1/admin/cabinet/access` с `{email, grant, revoke}`.

Образец стиля — `web/src/versions.ts` (разметка через `innerHTML`, `escapeHtml` на любых данных, `ApiError` для текста ошибки).

- [ ] **Step 2: Подключение**

В `web/src/main.ts`: в разделе администратора под существующим списком адресов смонтировать кабинет — `<section id="cabinet"></section>` рядом с `<section id="admin"></section>`, вызвать `mountCabinet` там же, где вызывается `renderAdmin()`.

- [ ] **Step 3: Стили**

В `web/src/style.css` — минимум: выравнивание галочек по центру клетки и приглушённый цвет заголовка недоступного столбца. Существующие правила таблиц переиспользовать.

- [ ] **Step 4: Прогон**

Run: `cd web && npm test && npm run build`
Expected: PASS

```bash
git add web/src/cabinet.ts web/src/main.ts web/src/style.css
git commit -m "feat(web): unified admin cabinet page"
```

---

### Task 7: Документация, выкатка, живая проверка

**Files:**
- Modify: `README.md`
- Живая проверка на ВМ (координатор)

- [ ] **Step 1: README**

Добавить раздел:

```markdown
## Единый кабинет администрирования

- Страница в админке: одна таблица, строка — человек, столбцы — три сервиса ВМ (Видео, Доска, Трансляции).
- `GET /api/v1/admin/cabinet` — состояние сервисов и таблица допуска; `POST /api/v1/admin/cabinet/access` `{email, grant[], revoke[]}` — правки, ответ содержит результат по каждому сервису.
- К соседям ходит сервер по loopback со служебным токеном `X-Service-Token`. Секреты — `STREAM_SERVICE_TOKEN` и `BOARD_SERVICE_TOKEN` в `.env`, **без префикса** `VIDEO_`. Пустой секрет запрещает доступ: сервис помечается «не настроен».
- Частичный успех не откатывается: три сервиса — три независимые операции. Что удалось, а что нет, показано поимённо.
- Доступ администратора кабинетом не меняется: он задан конфигурацией каждого сервиса.
- Свои админки у сервисов остаются: если Editing site лежит, доступ к остальным двум должен оставаться управляемым.
```

- [ ] **Step 2: Прогон и коммит**

Run: `uv run python -m pytest && uv run ruff check . && cd web && npm test && npm run build`

```bash
git add README.md
git commit -m "docs: unified admin cabinet"
```

- [ ] **Step 3: Слияние и выкатка** (координатор)

- [ ] **Step 4: Починка секрета у соседа** (координатор, с отмашки владельца)

В `/opt/videoboard/.env` переменная названа `SERVICE_TOKEN`, а сервис читает `BOARD_SERVICE_TOKEN`; значение совпадает с нашим. Переименовать ключ и перезапустить `board-api`. Это чужой сервис — делать только по отмашке.

- [ ] **Step 5: Живая проверка браузером** (координатор)

1. Кабинет открывается, все три столбца в состоянии «ok».
2. Завести новый адрес во все три — человек входит во все три.
3. Снять в одном — там выкидывает сразу, в остальных доступ остался.
4. Строка администратора: галочки выключены, попытка снять через API даёт `422 cannot_change_admin`.
5. Остановить соседа (`systemctl stop board-api`) — его столбец «недоступен», остальные работают; вернуть обратно.
6. Испортить секрет соседа — столбец «отказал в доступе», это видно иначе, чем «недоступен».
7. Убедиться, что собственные админки соседей продолжают работать.

---

## Поправки по ходу выполнения

(заполняется по ходу)

## Вне рамок

Общее хранилище личностей и единый вход, роли внутри сервисов, статистика соседей, отдельный журнал аудита — спека §10.
