"""Настройки сервиса из окружения и .env (префикс VIDEO_)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Пустая переменная окружения для пути к бинарю значит «искать в PATH», а не запускать пустую строку.
_TOOL_PATH_DEFAULTS = {"ffmpeg_path": "ffmpeg", "ffprobe_path": "ffprobe"}


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

    # Хранение. tmp_dir — тот же раздел, что data_dir: завершение загрузки делает os.replace.
    tmp_dir: Path | None = None
    chunk_size: int = Field(default=32 * 1024 * 1024, ge=1024, le=256 * 1024 * 1024)
    max_upload_bytes: int = Field(default=5 * 1024**3, ge=1)
    small_upload_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    user_quota_bytes: int = Field(default=20 * 1024**3, ge=1)
    disk_low_pct: float = Field(default=10.0, ge=0.0, le=90.0)
    uploads_per_hour: int = Field(default=20, ge=1)
    upload_ttl_hours: int = Field(default=24, ge=1)
    asset_ttl_hours: int = Field(default=24, ge=1)

    # Обработка медиа. Пути к бинарям берутся из PATH, если не заданы явно.
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    worker_poll_sec: float = Field(default=2.0, ge=0.1, le=60.0)
    analyze_timeout_sec: int = Field(default=1800, ge=10)
    proxy_timeout_sec: int = Field(default=14400, ge=10)
    peaks_per_sec: int = Field(default=50, ge=1, le=200)
    thumb_width: int = Field(default=160, ge=32, le=640)
    thumb_interval_sec: float = Field(default=2.0, gt=0)
    thumb_max_frames: int = Field(default=600, ge=1, le=5000)
    thumb_cols: int = Field(default=10, ge=1, le=50)
    proxy_long_side: int = Field(default=640, ge=160, le=1920)
    silence_min_sec: float = Field(default=0.5, gt=0)
    silence_dense_min_sec: float = Field(default=0.15, gt=0)
    speech_offset_db: float = Field(default=16.0, ge=1.0, le=60.0)

    # Пределы проекта (раздел 4 спеки) и подтяжка резов к паузам (раздел 10.6).
    max_clips: int = Field(default=100, ge=1, le=1000)
    max_total_duration_sec: int = Field(default=3 * 3600, ge=1)
    min_clip_sec: float = Field(default=0.1, gt=0)
    snap_window_sec: float = Field(default=0.35, ge=0.0, le=5.0)
    snap_buffer_sec: float = Field(default=0.3, ge=0.0, le=5.0)
    max_projects_per_user: int = Field(default=200, ge=1)
    versions_kept: int = Field(default=5, ge=1, le=50)
    # Реплики субтитров лежат в документе проекта (спека §5.2): часовой ролик — это около тысячи
    # реплик, и предел стоит там, где документ ещё читается целиком каждым сохранением.
    max_cues: int = Field(default=2000, ge=1, le=20000)

    # Рендер (раздел 9 спеки). Короткая сторона кадра задаёт разрешение вместе с пропорцией.
    render_timeout_sec: int = Field(default=4 * 3600, ge=60)
    render_ttl_hours: int = Field(default=24, ge=1)
    max_renders_queued: int = Field(default=2, ge=1, le=20)
    draft_short_side: int = Field(default=720, ge=240, le=2160)
    final_short_side: int = Field(default=1080, ge=240, le=2160)

    # Транскрипция (раздел 10 спеки). Пустой ключ запрещает запуск, а не пытается ходить в сеть.
    transcribe_base_url: str = "https://foundation-models.api.cloud.ru/v1"
    transcribe_api_key: str = ""
    transcribe_model: str = "openai/whisper-large-v3"
    transcribe_language: str = "ru"
    transcribe_chunk_sec: int = Field(default=600, ge=30, le=1800)
    transcribe_chunk_window_sec: int = Field(default=60, ge=0, le=300)
    transcribe_concurrency: int = Field(default=4, ge=1, le=20)
    transcribe_timeout_sec: int = Field(default=600, ge=30)
    transcribe_max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    transcribe_retries: int = Field(default=3, ge=1, le=10)
    # Полосы, которые разбирает воркер. Транскрипция ждёт сеть десятками минут, и держать за ней
    # очередь рендеров нельзя: полосы работают параллельно, каждая своим потоком.
    worker_lanes: str = "cpu,net"

    # Единый кабинет: соседи по ВМ. Служебные секреты названы БЕЗ префикса VIDEO_ — так они
    # разложены в /opt/editing-site/.env (спека §4). validation_alias отменяет env_prefix
    # только для этих двух полей, остальные читаются как раньше. Ровно на этом споткнулся сосед:
    # переменную положили как SERVICE_TOKEN, а настройки искали BOARD_SERVICE_TOKEN.
    stream_service_token: str = Field(default="", validation_alias=AliasChoices("STREAM_SERVICE_TOKEN"))
    board_service_token: str = Field(default="", validation_alias=AliasChoices("BOARD_SERVICE_TOKEN"))
    # Ходим на loopback: три сервиса на одной машине, Caddy и интернет тут не при чём.
    board_base_url: str = "http://127.0.0.1:8020"
    stream_base_url: str = "http://127.0.0.1:8014"
    service_timeout_sec: float = Field(default=3.0, gt=0, le=30)

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

    @field_validator("tmp_dir", mode="before")
    @classmethod
    def _normalize_tmp_dir(cls, value: object) -> object:
        # Пустой VIDEO_TMP_DIR из окружения — как будто не задан, используем data_dir/tmp.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("ffmpeg_path", "ffprobe_path", mode="before")
    @classmethod
    def _tool_path(cls, value: object, info: ValidationInfo) -> str:
        # Пустой VIDEO_FFMPEG_PATH/VIDEO_FFPROBE_PATH — как будто не задан, ищем в PATH под своим именем.
        if value is None or not str(value).strip():
            return _TOOL_PATH_DEFAULTS[info.field_name]
        return str(value).strip()

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

    @property
    def tmp_path(self) -> Path:
        return self.tmp_dir if self.tmp_dir is not None else self.data_dir / "tmp"

    @property
    def uploads_tmp_path(self) -> Path:
        return self.tmp_path / "uploads"
