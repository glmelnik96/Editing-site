"""Настройки сервиса из окружения и .env (префикс VIDEO_)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="VIDEO_", extra="ignore")

    data_dir: Path = Path("./data")
    public_base_url: str = "http://localhost:8010"
    yandex_client_id: str = ""
    yandex_client_secret: str = ""
    admin_email: str = ""
    cookie_secure: bool = False
    session_absolute_days: int = Field(default=30, ge=1, le=3650)
    session_idle_days: int = Field(default=7, ge=1, le=3650)
    max_sessions_per_user: int = Field(default=5, ge=1, le=100)
    login_rate_max: int = Field(default=10, ge=1)
    login_rate_window_sec: int = Field(default=60, ge=1)
    log_level: str = "INFO"

    @field_validator("public_base_url")
    @classmethod
    def _check_public_base_url(cls, value: str) -> str:
        u = urlsplit(value)
        try:
            port = u.port
        except ValueError as exc:
            raise ValueError("VIDEO_PUBLIC_BASE_URL: порт должен быть числом от 1 до 65535") from exc
        if u.scheme not in ("http", "https") or not u.hostname or port == 0:
            raise ValueError("VIDEO_PUBLIC_BASE_URL должен быть вида https://host[:port]")
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError("VIDEO_LOG_LEVEL: DEBUG, INFO, WARNING, ERROR или CRITICAL")
        return level

    @property
    def db_path(self) -> Path:
        return self.data_dir / "video.db"

    @property
    def yandex_redirect_uri(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/api/v1/auth/callback"

    @property
    def allowed_origin(self) -> str:
        u = urlsplit(self.public_base_url)
        host = u.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        default_port = 443 if u.scheme == "https" else 80
        netloc = host if u.port in (None, default_port) else f"{host}:{u.port}"
        return f"{u.scheme}://{netloc}"
