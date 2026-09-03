"""Настройки сервиса из окружения и .env (префикс VIDEO_)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="VIDEO_", extra="ignore")

    data_dir: Path = Path("./data")
    public_base_url: str = "http://localhost:8010"
    yandex_client_id: str = ""
    yandex_client_secret: str = ""
    admin_email: str = ""
    cookie_secure: bool = False
    trust_proxy: bool = False
    session_absolute_days: int = 30
    session_idle_days: int = 7
    max_sessions_per_user: int = 5
    login_rate_max: int = 10
    login_rate_window_sec: int = 60
    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "video.db"

    @property
    def yandex_redirect_uri(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/api/v1/auth/callback"

    @property
    def allowed_origin(self) -> str:
        u = urlsplit(self.public_base_url)
        return f"{u.scheme}://{u.netloc}"
