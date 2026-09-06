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


def test_settings_reject_bad_port_and_bad_log_level():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, public_base_url="https://host:abc")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_level="loud")
    assert Settings(_env_file=None, log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, public_base_url="http://:8010")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, public_base_url="https://host:70000")


def test_settings_reject_zero_session_limit():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_sessions_per_user=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, login_rate_max=0)


def test_storage_defaults_and_tmp_path(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "d")
    assert s.chunk_size == 32 * 1024 * 1024
    assert s.user_quota_bytes == 20 * 1024**3
    assert s.max_upload_bytes == 5 * 1024**3
    assert s.small_upload_max_bytes == 64 * 1024 * 1024
    assert s.disk_low_pct == 10.0
    assert s.uploads_per_hour == 20
    assert s.upload_ttl_hours == 24 and s.asset_ttl_hours == 24
    assert s.tmp_path == tmp_path / "d" / "tmp"
    assert s.uploads_tmp_path == tmp_path / "d" / "tmp" / "uploads"


def test_tmp_dir_override(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "d", tmp_dir=tmp_path / "t")
    assert s.uploads_tmp_path == tmp_path / "t" / "uploads"


def test_chunk_size_bounds():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, chunk_size=512)


def test_empty_tmp_dir_env_means_default(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_TMP_DIR", "")
    s = Settings(_env_file=None, data_dir=tmp_path / "d")
    assert s.tmp_dir is None and s.tmp_path == tmp_path / "d" / "tmp"


def test_project_limits_have_sane_defaults():
    s = Settings(_env_file=None)
    assert s.max_clips == 100
    assert s.max_total_duration_sec == 3 * 3600
    assert s.min_clip_sec == 0.1
    assert s.snap_window_sec == 0.35
    assert s.snap_buffer_sec == 0.3
    assert s.max_projects_per_user == 200


def test_snap_window_is_bounded():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, snap_window_sec=-1)


def test_versions_kept_default():
    s = Settings(_env_file=None)
    assert s.versions_kept == 5


def test_versions_kept_is_bounded():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, versions_kept=0)


def test_render_settings_have_sane_defaults():
    s = Settings(_env_file=None)
    assert s.render_timeout_sec == 4 * 3600
    assert s.render_ttl_hours == 24
    assert s.max_renders_queued == 2
    assert s.draft_short_side == 720
    assert s.final_short_side == 1080


def test_render_short_side_is_bounded():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, final_short_side=99)


def test_service_tokens_are_read_without_prefix(monkeypatch):
    """Секреты соседей разложены на ВМ без префикса VIDEO_. Ровно на этом сосед и споткнулся:
    переменную назвали SERVICE_TOKEN, а настройки искали BOARD_SERVICE_TOKEN, и токен молча не работал."""
    monkeypatch.setenv("STREAM_SERVICE_TOKEN", "s-secret")
    monkeypatch.setenv("BOARD_SERVICE_TOKEN", "b-secret")
    monkeypatch.setenv("VIDEO_STREAM_SERVICE_TOKEN", "мимо")
    s = Settings(_env_file=None)
    assert s.stream_service_token == "s-secret"
    assert s.board_service_token == "b-secret"


def test_service_tokens_default_to_empty(monkeypatch):
    for name in ("STREAM_SERVICE_TOKEN", "BOARD_SERVICE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None)
    assert s.stream_service_token == "" and s.board_service_token == ""
    assert s.board_base_url == "http://127.0.0.1:8020"
    assert s.stream_base_url == "http://127.0.0.1:8014"
