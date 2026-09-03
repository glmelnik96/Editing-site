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
def app(settings, tmp_path):
    return create_app(settings, web_dist=tmp_path / "no-dist")


@pytest.fixture
def client(app):
    # Origin по умолчанию: проверка cross-site считает запрос без Origin чужим (Task 4),
    # а браузер шлёт его всегда.
    with TestClient(app, headers={"Origin": "http://testserver"}) as c:
        yield c


@pytest.fixture
def login_as(client, monkeypatch):
    """Логин через подменённые OAuth-функции. Возвращает функцию login(email, name)."""

    def _login(email: str = "admin@ya.ru", name: str = "Admin"):
        from server.app.auth import routes

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
