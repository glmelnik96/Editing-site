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
    now = now or utcnow()
    conn = connect(settings.db_path)
    try:
        stats = {
            "uploads_expired": rules.delete_expired_uploads(conn, now),
            "assets_expired": rules.delete_expired_assets(conn, settings, now),
            "orphans": rules.delete_orphans(conn, settings, now),
            "sessions_expired": rules.delete_expired_sessions(conn, settings, now),
        }
        stats["jobs_requeued"], stats["jobs_failed"] = rules.requeue_stale_jobs(conn, now)
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
