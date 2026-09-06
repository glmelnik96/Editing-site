import os

import pytest
from starlette.testclient import TestClient

from server.app.config import Settings
from server.app.main import create_app

# Служебные секреты соседей названы без префикса (так они лежат на ВМ), поэтому одной проверки
# по VIDEO_ мало: настоящий токен из окружения разработчика не должен просачиваться в тесты.
UNPREFIXED = ("STREAM_SERVICE_TOKEN", "BOARD_SERVICE_TOKEN")


@pytest.fixture(autouse=True)
def _clean_video_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("VIDEO_") or key in UNPREFIXED:
            monkeypatch.delenv(key)


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
        max_sessions_per_user=5,
        session_absolute_days=30,
        session_idle_days=7,
        tmp_dir=tmp_path / "tmp",
        chunk_size=1024,
        user_quota_bytes=10 * 1024 * 1024,
        max_upload_bytes=8 * 1024 * 1024,
        small_upload_max_bytes=1024 * 1024,
        uploads_per_hour=1000,
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
        assert r.status_code == 302 and r.headers["location"] == "/", r.text
        return client

    return _login


@pytest.fixture
def bearer_client(app, client, login_as):
    """Второй клиент без cookie и без Origin: агент с Bearer-токеном того же пользователя."""
    login_as()
    r = client.post("/api/v1/tokens", json={"name": "agent"})
    assert r.status_code == 201, r.text
    secret = r.json()["secret"]
    with TestClient(app, headers={"Authorization": f"Bearer {secret}"}) as c:
        yield c
