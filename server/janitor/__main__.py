"""Janitor: python -m server.janitor. Раз в час по systemd-таймеру (deploy/video-janitor.timer):
сроки жизни загрузок и ассетов, сироты на диске, зависшие задания, просроченные сессии, суточный бэкап базы.
Миграции не применяет: это делает deploy.sh до перезапуска сервисов.
"""
from __future__ import annotations

import logging
from datetime import datetime

from server.app.config import Settings
from server.app.main import configure_logging
from server.app.util import utcnow
from server.db.core import connect
from server.janitor import rules

log = logging.getLogger("video.janitor")


def run(settings: Settings, now: datetime | None = None) -> dict[str, int]:
    """Бэкап выполняется всегда, даже если правила очистки упали: ежедневная копия базы не должна
    теряться из-за гонки или временной ошибки одного правила."""
    now = now or utcnow()
    stats = {
        "uploads_expired": 0,
        "assets_expired": 0,
        "renders_expired": 0,
        "orphans": 0,
        "sessions_expired": 0,
        "jobs_requeued": 0,
        "jobs_failed": 0,
        "error": 0,
    }
    conn = connect(settings.db_path)
    try:
        try:
            stats["uploads_expired"] = rules.delete_expired_uploads(conn, now)
            stats["assets_expired"] = rules.delete_expired_assets(conn, settings, now)
            stats["renders_expired"] = rules.delete_expired_renders(conn, now)
            stats["orphans"] = rules.delete_orphans(conn, settings, now)
            stats["sessions_expired"] = rules.delete_expired_sessions(conn, settings, now)
            stats["jobs_requeued"], stats["jobs_failed"] = rules.requeue_stale_jobs(conn, now)
        except Exception:
            log.exception("janitor: правила очистки упали")
            stats["error"] = 1
    finally:
        conn.close()
    stats["backup"] = 1 if rules.backup_if_due(settings, now) else 0
    return stats


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    # На случай первого запуска до deploy.sh (каталог ещё не создан): как в server.db.migrate.main().
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    stats = run(settings)
    log.info("janitor: %s", " ".join(f"{k}={v}" for k, v in stats.items()))


if __name__ == "__main__":
    main()
