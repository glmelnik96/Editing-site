"""Журнал необратимых действий: кто что удалил или отменил и чьё это было.

Проекты, записи, ролики и задания общие: удалить и отменить может любой вошедший, а в базе после
удаления не остаётся ничего. Строка журнала сервера отвечает на вопрос «куда делся проект» —
раньше его приходилось восстанавливать по адресам в журнале uvicorn. Почта внутри команды не
секрет: ею же подписаны задания и карточки.
"""
from __future__ import annotations

import logging
import sqlite3

from server.app.auth.deps import CurrentUser

log = logging.getLogger("video.audit")


def audit(
    conn: sqlite3.Connection, user: CurrentUser, action: str, target: str, author_id: str | None
) -> None:
    """action — «удалил проект»; target — что именно, с id; author_id — чьё это было."""
    row = None
    if author_id:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (author_id,)).fetchone()
    author = row["email"] if row else "неизвестен"
    log.info("%s (%s) %s %s, автор %s", user.email, user.auth, action, target, author)
