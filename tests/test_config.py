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
