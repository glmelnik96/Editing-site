"""Фабрика приложения. uvicorn server.app.main:app"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.app.config import Settings
from server.app.errors import install_error_handlers
from server.app.health import router as health_router
from server.app.ratelimit import FixedWindowLimiter
from server.app.security import install_origin_check
from server.db.core import connect
from server.db.migrate import migrate

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
log = logging.getLogger("video")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        conn = connect(settings.db_path)
        try:
            applied = migrate(conn)
        except Exception:
            conn.close()
            raise
        # Долгоживущее соединение: пока оно открыто, файлы WAL не пересоздаются на каждый запрос
        # (соединение на запрос в get_db остаётся; это соединение само ничего не делает).
        app.state.db_keeper = conn
        log.info(
            "starting: public_base_url=%s allowed_origin=%s data_dir=%s migrations_applied=%s",
            settings.public_base_url, settings.allowed_origin, settings.data_dir, applied or "none",
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
    install_error_handlers(app)
    install_origin_check(app)
    app.include_router(health_router)
    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
    return app


app = create_app()
