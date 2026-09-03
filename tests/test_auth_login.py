from starlette.testclient import TestClient

from server.app.config import Settings
from server.app.main import create_app


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
    with TestClient(_bare_app(tmp_path, yandex_client_id="cid", login_rate_max=2)) as c:
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
