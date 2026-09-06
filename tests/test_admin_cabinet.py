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
    """Отдельное имя, не settings: так называется фикстура приложения из conftest.
    Умолчания сведены в словарь, иначе вызов с тем же ключом даст «multiple values»."""
    return Settings(_env_file=None, **{
        "admin_email": ADMIN, "board_service_token": "b", "stream_service_token": "s", **kw
    })


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
    """У board белый список пуст, а доступ у администратора есть — он из конфигурации сервиса.
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
    def handler(request: httpx.Request) -> httpx.Response:
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

    monkeypatch.setattr(
        routes, "build_client", lambda s: httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_cabinet_page(client, login_as, monkeypatch):
    patch_neighbours(monkeypatch, both_ok)
    login_as()
    body = client.get("/api/v1/admin/cabinet").json()
    assert [s["key"] for s in body["services"]] == ["video", "board", "stream"]
    assert all(isinstance(p["access"], dict) for p in body["people"])


def test_cabinet_change_reports_each_service(client, login_as, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/v1/admin" in request.url.path:
            return httpx.Response(403, json={})
        return httpx.Response(201, json={})

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
