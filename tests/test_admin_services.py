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
STREAM_BODY = [
    {"email": "a@x.ru", "note": "коллега", "added_by": "unified-admin", "created_at": "2026-09-06"}
]


def make_settings(**kw) -> Settings:
    # Значения по умолчанию идут одним словарём: иначе тест, задающий board_service_token явно,
    # столкнётся с одноимённым аргументом и получит TypeError вместо настроек.
    return Settings(_env_file=None, **{"board_service_token": "b", "stream_service_token": "s", **kw})


def service(key: str, **kw):
    return next(s for s in remote_services(make_settings(**kw)) if s.key == key)


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
        # raw_path, а не path: path httpx отдаёт уже раскодированным, и на нём экранирование не увидеть.
        seen.append((request.method, request.url.raw_path.decode()))
        return httpx.Response(201 if request.method == "POST" else 204)

    client = RemoteClient(service("stream"), client_for(handler))
    client.add("Some.One@X.ru")
    client.remove("Some.One@X.ru")
    assert seen == [("POST", "/api/admin/allowed"), ("DELETE", "/api/admin/allowed/Some.One%40X.ru")]


def test_missing_person_on_remove_is_success():
    """Снять доступ у того, кого в списке нет, — цель достигнута, ругаться не на что."""
    stub = client_for(lambda r: httpx.Response(404, json={"detail": "нет"}))
    RemoteClient(service("stream"), stub).remove("ghost@x.ru")


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
