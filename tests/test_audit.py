"""Журнал удалений: кто, что и чьё — одной строкой."""
import logging

from server.app.audit import audit
from server.app.auth.deps import CurrentUser
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate


def test_audit_names_who_what_and_whose(tmp_path, caplog):
    conn = connect(tmp_path / "video.db")
    migrate(conn)
    conn.execute(
        "INSERT INTO users (id, email, name, created_at) "
        "VALUES ('usr_00000000000a', 'liza@ya.ru', 'Лиза', ?)",
        (now_iso(),),
    )
    user = CurrentUser(id="usr_00000000000b", email="gleb@ya.ru", name="Глеб", role="user", auth="token")
    with caplog.at_level(logging.INFO, logger="video.audit"):
        audit(conn, user, "удалил проект", "prj_000000000001 «Планёрка»", "usr_00000000000a")
        audit(conn, user, "удалил ролик", "rnd_000000000001", None)
    conn.close()
    assert caplog.messages == [
        "gleb@ya.ru (token) удалил проект prj_000000000001 «Планёрка», автор liza@ya.ru",
        "gleb@ya.ru (token) удалил ролик rnd_000000000001, автор неизвестен",
    ]
