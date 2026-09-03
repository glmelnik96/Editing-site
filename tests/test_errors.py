from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from server.app.errors import ApiError, install_error_handlers


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api")
    def api():
        raise ApiError(418, "teapot", "Чайник", {"k": 1}, headers={"X-Extra": "1"})

    @app.get("/http")
    def http():
        raise HTTPException(status_code=401, detail="nope", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret detail")

    @app.get("/q")
    def q(n: int):
        return {"n": n}

    return app


def test_api_error_envelope_and_headers():
    r = TestClient(_app()).get("/api")
    assert r.status_code == 418
    assert r.json() == {"error": {"code": "teapot", "message": "Чайник", "details": {"k": 1}}}
    assert r.headers["x-extra"] == "1"


def test_http_exception_is_wrapped_with_headers():
    r = TestClient(_app()).get("/http")
    assert r.status_code == 401
    assert r.json()["error"] == {"code": "http_error", "message": "nope", "details": {}}
    assert r.headers["www-authenticate"] == "Bearer"


def test_unknown_route_is_json_404():
    r = TestClient(_app()).get("/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "http_error"


def test_validation_error_hides_input():
    r = TestClient(_app()).get("/q", params={"n": "abc"})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error"
    assert set(body["details"]["errors"][0]) == {"loc", "msg", "type"}
    assert "abc" not in r.text


def test_unhandled_exception_is_json_500_without_details():
    r = TestClient(_app(), raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Внутренняя ошибка", "details": {}}}
    assert "secret detail" not in r.text
