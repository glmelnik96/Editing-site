import httpx
from starlette.testclient import TestClient

from server.app.config import Settings
from server.app.main import create_app
from server.db.core import connect


def _bare_app(tmp_path, **overrides):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", **overrides)
    return create_app(settings, web_dist=tmp_path / "no-dist")


def test_login_redirects_to_yandex_with_state_cookie(client):
    r = client.get("/api/v1/auth/login", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("https://oauth.yandex.ru/authorize?")
    assert "client_id=cid" in loc
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Fapi%2Fv1%2Fauth%2Fcallback" in loc
    assert client.cookies.get("oauth_state")


def test_callback_sets_session_and_me_works(login_as):
    c = login_as("admin@ya.ru", "Admin")
    me = c.get("/api/v1/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "admin@ya.ru"
    assert body["role"] == "admin"
    assert body["auth"] == "cookie"


def test_callback_rejects_bad_state(client):
    client.get("/api/v1/auth/login", follow_redirects=False)
    r = client.get("/api/v1/auth/callback", params={"code": "x", "state": "wrong"}, follow_redirects=False)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_state"


def test_callback_reports_provider_error(client):
    r = client.get("/api/v1/auth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "oauth_error"


def test_callback_rejects_non_whitelisted(client, monkeypatch):
    from server.app.auth import routes

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


def test_callback_rejects_disabled_user(login_as, settings):
    from server.db.core import connect

    c = login_as("admin@ya.ru")
    conn = connect(settings.db_path)
    conn.execute("UPDATE users SET disabled = 1 WHERE email = 'admin@ya.ru'")
    conn.close()
    assert c.get("/api/v1/me").status_code == 401
    c.get("/api/v1/auth/login", follow_redirects=False)
    state = c.cookies.get("oauth_state")
    r = c.get("/api/v1/auth/callback", params={"code": "x", "state": state}, follow_redirects=False)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "account_disabled"


def test_me_without_session_is_401_with_challenge(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert r.headers["www-authenticate"] == "Bearer"


def test_logout_clears_session(login_as):
    c = login_as()
    assert c.post("/api/v1/auth/logout").status_code == 200
    assert c.get("/api/v1/me").status_code == 401


def test_login_rate_limited_per_ip(tmp_path):
    app = _bare_app(tmp_path, yandex_client_id="cid", yandex_client_secret="sec", login_rate_max=2)
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/login", follow_redirects=False).status_code == 302
        assert c.get("/api/v1/auth/login", follow_redirects=False).status_code == 302
        r = c.get("/api/v1/auth/login", follow_redirects=False)
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limited"


def test_login_without_oauth_config_is_503(tmp_path):
    with TestClient(_bare_app(tmp_path, yandex_client_id="")) as c:
        r = c.get("/api/v1/auth/login", follow_redirects=False)
        assert r.status_code == 503


def test_head_on_unknown_api_path_is_json_404(client):
    r = client.head("/api/v1/nope")
    assert r.status_code == 404


def _fake_yandex(monkeypatch, email="admin@ya.ru", exchange=None):
    from server.app.auth import routes

    async def fake_exchange(client_, **kwargs):
        if exchange is not None:
            raise exchange
        return "ACCESS"

    async def fake_userinfo(client_, token):
        return {"id": "42", "default_email": email, "real_name": "A"}

    monkeypatch.setattr(routes, "exchange_code", fake_exchange)
    monkeypatch.setattr(routes, "fetch_userinfo", fake_userinfo)


def test_relogin_rotates_session_and_keeps_one_row(login_as, settings):
    c = login_as()
    first = c.cookies.get("vsid")
    login_as()
    second = c.cookies.get("vsid")
    assert first != second
    conn = connect(settings.db_path)
    assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    assert conn.execute("SELECT yandex_id FROM users").fetchone()[0] == "1"
    conn.close()


def test_cookie_attributes_and_state_cleared(client, monkeypatch):
    _fake_yandex(monkeypatch)
    r = client.get("/api/v1/auth/login", follow_redirects=False)
    state_cookie = r.headers["set-cookie"]
    assert "HttpOnly" in state_cookie
    assert "Path=/api/v1/auth" in state_cookie
    assert "Max-Age=600" in state_cookie
    state = client.cookies.get("oauth_state")
    r = client.get("/api/v1/auth/callback", params={"code": "x", "state": state}, follow_redirects=False)
    cookies = r.headers.get_list("set-cookie")
    vsid = next(c for c in cookies if c.startswith("vsid="))
    assert "HttpOnly" in vsid and "Path=/" in vsid and "Max-Age=2592000" in vsid and "SameSite=lax" in vsid
    cleared = next(c for c in cookies if c.startswith("oauth_state="))
    assert "Max-Age=0" in cleared and "HttpOnly" in cleared
    assert client.cookies.get("oauth_state") is None


def test_callback_without_state_cookie_is_bad_state(client):
    r = client.get("/api/v1/auth/callback", params={"code": "x", "state": "abc"}, follow_redirects=False)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_state"


def test_callback_upstream_failures_are_502(client, monkeypatch):
    for exc in (httpx.ConnectError("boom"), KeyError("access_token"), ValueError("not json")):
        _fake_yandex(monkeypatch, exchange=exc)
        client.get("/api/v1/auth/login", follow_redirects=False)
        state = client.cookies.get("oauth_state")
        r = client.get("/api/v1/auth/callback", params={"code": "x", "state": state}, follow_redirects=False)
        assert r.status_code == 502
        assert r.json()["error"]["code"] == "oauth_upstream"


def test_failed_callback_spends_state_and_callback_is_rate_limited(tmp_path, monkeypatch):
    app = _bare_app(tmp_path, yandex_client_id="cid", yandex_client_secret="sec", login_rate_max=3)
    with TestClient(app, headers={"Origin": "http://testserver"}) as c:
        _fake_yandex(monkeypatch, email="stranger@ya.ru")
        c.get("/api/v1/auth/login", follow_redirects=False)
        state = c.cookies.get("oauth_state")
        r = c.get("/api/v1/auth/callback", params={"code": "x", "state": state}, follow_redirects=False)
        assert r.status_code == 403
        assert c.cookies.get("oauth_state") is None
        r = c.get("/api/v1/auth/callback", params={"code": "x", "state": state}, follow_redirects=False)
        assert r.json()["error"]["code"] == "bad_state"
        r = c.get("/api/v1/auth/callback", params={"code": "x", "state": state}, follow_redirects=False)
        assert r.status_code == 429


def test_bearer_takes_precedence_over_cookie(login_as):
    c = login_as()
    r = c.get("/api/v1/me", headers={"Authorization": "Bearer vt_bogus"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_token"


def test_me_is_not_cacheable(login_as):
    assert login_as().get("/api/v1/me").headers["cache-control"] == "no-store"


def test_login_requires_client_secret_too(tmp_path):
    with TestClient(_bare_app(tmp_path, yandex_client_id="cid", yandex_client_secret="")) as c:
        assert c.get("/api/v1/auth/login", follow_redirects=False).status_code == 503


def test_head_healthz_and_options_on_unknown_api_path(client):
    assert client.head("/healthz").status_code == 200
    assert client.options("/api/v1/nope").status_code == 404


def test_rate_limited_callback_keeps_state_cookie(tmp_path, monkeypatch):
    app = _bare_app(tmp_path, yandex_client_id="cid", yandex_client_secret="sec", login_rate_max=1)
    with TestClient(app, headers={"Origin": "http://testserver"}) as c:
        _fake_yandex(monkeypatch)
        c.get("/api/v1/auth/login", follow_redirects=False)
        state = c.cookies.get("oauth_state")
        r = c.get("/api/v1/auth/callback", params={"code": "x", "state": state}, follow_redirects=False)
        assert r.status_code == 429
        assert c.cookies.get("oauth_state") == state
