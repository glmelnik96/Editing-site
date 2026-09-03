from types import SimpleNamespace

from fastapi import FastAPI
from starlette.requests import Request
from starlette.testclient import TestClient

from server.app.errors import install_error_handlers
from server.app.security import client_ip, install_origin_check, is_cross_site

ORIGIN = "https://video.example.ru"


def test_origin_header_decides_when_present():
    assert is_cross_site({"origin": "https://evil.example"}, ORIGIN) is True
    assert is_cross_site({"origin": ORIGIN}, ORIGIN) is False
    assert is_cross_site({"origin": "https://VIDEO.example.ru/"}, ORIGIN) is False
    assert is_cross_site({"origin": "null"}, ORIGIN) is True
    assert is_cross_site({"origin": "http://video.example.ru"}, ORIGIN) is True
    assert is_cross_site({"origin": "https://video.example.ru:8443"}, ORIGIN) is True


def test_sec_fetch_site_used_without_origin():
    assert is_cross_site({"sec-fetch-site": "cross-site"}, ORIGIN) is True
    assert is_cross_site({"sec-fetch-site": "same-site"}, ORIGIN) is True
    assert is_cross_site({"sec-fetch-site": "same-origin"}, ORIGIN) is False
    assert is_cross_site({"sec-fetch-site": "none"}, ORIGIN) is False


def test_no_headers_is_treated_as_cross_site():
    assert is_cross_site({}, ORIGIN) is True


def _request(client: tuple[str, int] | None) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "client": client})


def test_client_ip_uses_peer_address_only():
    assert client_ip(_request(("203.0.113.9", 1234))) == "203.0.113.9"
    assert client_ip(_request(None)) == "unknown"


def _app() -> FastAPI:
    app = FastAPI()
    app.state.settings = SimpleNamespace(allowed_origin="http://testserver")
    install_error_handlers(app)
    install_origin_check(app)

    @app.post("/x")
    def post_x():
        return {"ok": True}

    @app.get("/x")
    def get_x():
        return {"ok": True}

    return app


def test_origin_check_middleware_matrix():
    app = _app()
    anon = TestClient(app)
    c = TestClient(app)
    c.cookies.set("vsid", "s")
    evil = {"Origin": "https://evil.example"}
    r = c.post("/x", headers=evil)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "cross_site"
    assert r.json()["error"]["details"]["allowed_origin"] == "http://testserver"
    assert c.post("/x", headers={"Origin": "http://testserver"}).status_code == 200
    assert c.post("/x", headers={"Origin": "null"}).status_code == 403
    assert c.post("/x").status_code == 403
    assert c.post("/x", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert c.post("/x", headers={**evil, "Authorization": "Bearer vt_x"}).status_code == 200
    assert c.post("/x", headers={**evil, "Authorization": "Basic zzz"}).status_code == 403
    assert c.get("/x", headers=evil).status_code == 200
    assert anon.post("/x", headers=evil).status_code == 200
