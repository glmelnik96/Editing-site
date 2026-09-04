"""Фабрика приложения. uvicorn server.app.main:app
(объект app создаётся при первом обращении, не при импорте).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.app.admin.routes import router as admin_router
from server.app.assets.routes import router as assets_router
from server.app.auth.routes import me_router
from server.app.auth.routes import router as auth_router
from server.app.auth.token_routes import router as tokens_router
from server.app.config import Settings
from server.app.errors import ApiError, install_error_handlers
from server.app.health import router as health_router
from server.app.ratelimit import FixedWindowLimiter
from server.app.security import install_origin_check
from server.app.uploads.routes import router as uploads_router
from server.db.core import connect
from server.db.migrate import migrate

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
log = logging.getLogger("video")


def configure_logging(level: str) -> None:
    """Корень на WARNING, чтобы чужие библиотеки (httpx и прочие) не шумели в journald;
    наш логгер на заданном уровне.
    """
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("video").setLevel(level)


def create_app(settings: Settings | None = None, web_dist: Path | None = None) -> FastAPI:
    settings = settings or Settings()
    web_dist = WEB_DIST if web_dist is None else web_dist

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.uploads_tmp_path.mkdir(parents=True, exist_ok=True)
        if settings.uploads_tmp_path.stat().st_dev != settings.data_dir.stat().st_dev:
            log.warning(
                "tmp_dir %s и data_dir %s на разных разделах: завершение загрузки будет падать",
                settings.tmp_path,
                settings.data_dir,
            )
        conn = connect(settings.db_path)
        try:
            applied = migrate(conn)
        except Exception:
            conn.close()
            raise
        # Долгоживущее соединение: пока оно открыто, файлы WAL не пересоздаются на каждый запрос.
        # Из обработчиков запросов им не пользоваться: у них своё соединение через get_db.
        app.state.db_keeper = conn
        log.info(
            "starting: public_base_url=%s allowed_origin=%s data_dir=%s migrations_applied=%s",
            settings.public_base_url,
            settings.allowed_origin,
            settings.data_dir,
            ", ".join(str(v) for v in applied) or "none",
        )
        try:
            yield
        finally:
            conn.close()

    app = FastAPI(
        title="Editing site",
        version="0.1.0",
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.login_limiter = FixedWindowLimiter(settings.login_rate_max, settings.login_rate_window_sec)
    app.state.upload_limiter = FixedWindowLimiter(settings.uploads_per_hour, 3600)
    install_error_handlers(app)
    install_origin_check(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(tokens_router)
    app.include_router(admin_router)
    app.include_router(uploads_router)
    app.include_router(assets_router)
    # Роутеры API из следующих задач подключаются ВЫШЕ этой строки.
    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def _api_not_found(rest: str) -> None:
        raise ApiError(404, "not_found", "Нет такого маршрута")
    # Все роутеры подключаются выше этой строки: статика на "/" перехватывает всё остальное.
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")
    return app


_apps: dict[str, FastAPI] = {}


def __getattr__(name: str) -> FastAPI:
    """`server.app.main:app` для uvicorn: настройки читаются при первом обращении, а не при импорте."""
    if name == "app":
        if "app" not in _apps:
            _apps["app"] = create_app()
        return _apps["app"]
    raise AttributeError(name)
