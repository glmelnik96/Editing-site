import pytest
from pydantic import ValidationError

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


def test_settings_reject_base_url_without_scheme():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, public_base_url="video.example.ru")


def test_settings_strip_trailing_slash_from_base_url():
    s = Settings(_env_file=None, public_base_url="https://video.example.ru/")
    assert s.public_base_url == "https://video.example.ru"


def test_allowed_origin_drops_default_port_and_keeps_custom():
    s443 = Settings(_env_file=None, public_base_url="https://video.example.ru:443")
    assert s443.allowed_origin == "https://video.example.ru"
    s8010 = Settings(_env_file=None, public_base_url="http://localhost:8010")
    assert s8010.allowed_origin == "http://localhost:8010"
