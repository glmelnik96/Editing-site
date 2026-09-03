from datetime import timedelta

import pytest

from server.app.auth.sessions import create_session, delete_session, resolve_session
from server.app.auth.tokens import TOKEN_PREFIX, create_token, list_tokens, resolve_token, revoke_token
from server.app.auth.users import is_whitelisted, upsert_user
from server.app.util import iso, sha256_hex, utcnow
from server.db.core import connect
from server.db.migrate import migrate


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    yield conn
    conn.close()


def test_whitelist_admin_email_always_allowed_and_table_lookup(db):
    assert is_whitelisted(db, "Admin@YA.ru", "admin@ya.ru") is True
    assert is_whitelisted(db, "user@ya.ru", "admin@ya.ru") is False
    db.execute(
        "INSERT INTO whitelist (email, added_by, added_at) "
        "VALUES ('user@ya.ru', NULL, '2026-01-01T00:00:00.000Z')"
    )
    assert is_whitelisted(db, " user@ya.ru ", "admin@ya.ru") is True
    assert is_whitelisted(db, "", "admin@ya.ru") is False


def test_upsert_user_sets_role_and_updates_name(db):
    a = upsert_user(db, email="Admin@ya.ru", name="A", admin_email="admin@ya.ru")
    assert a["email"] == "admin@ya.ru" and a["role"] == "admin"
    u = upsert_user(db, email="user@ya.ru", name="U1", admin_email="admin@ya.ru")
    assert u["role"] == "user"
    u2 = upsert_user(db, email="user@ya.ru", name="U2", admin_email="admin@ya.ru")
    assert u2["id"] == u["id"] and u2["name"] == "U2"


def test_session_limit_evicts_oldest_and_stores_only_hashes(db, settings):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    sids = [create_session(db, user_id=uid, user_agent="ua", settings=settings) for _ in range(6)]
    alive = {r[0] for r in db.execute("SELECT id FROM sessions WHERE user_id = ?", (uid,))}
    assert len(alive) == 5
    assert sha256_hex(sids[0]) not in alive
    assert sha256_hex(sids[-1]) in alive
    assert sids[-1] not in alive
    row = resolve_session(db, sids[-1], settings)
    assert row["email"] == "u@ya.ru" and row["session_id"] == sha256_hex(sids[-1])


def test_session_expires_by_idle_and_absolute_ttl(db, settings):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    idle = create_session(db, user_id=uid, user_agent="", settings=settings)
    db.execute(
        "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
        (iso(utcnow() - timedelta(days=8)), sha256_hex(idle)),
    )
    assert resolve_session(db, idle, settings) is None
    old = create_session(db, user_id=uid, user_agent="", settings=settings)
    db.execute(
        "UPDATE sessions SET absolute_expires_at = ? WHERE id = ?",
        (iso(utcnow() - timedelta(seconds=1)), sha256_hex(old)),
    )
    assert resolve_session(db, old, settings) is None
    assert resolve_session(db, None, settings) is None
    assert resolve_session(db, "no-such", settings) is None


def test_delete_session(db, settings):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    sid = create_session(db, user_id=uid, user_agent="", settings=settings)
    delete_session(db, sid)
    assert resolve_session(db, sid, settings) is None


def test_disabled_user_cannot_use_session_or_token(db, settings):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    sid = create_session(db, user_id=uid, user_agent="", settings=settings)
    _, secret = create_token(db, user_id=uid, name="agent", expires_in_days=None)
    db.execute("UPDATE users SET disabled = 1 WHERE id = ?", (uid,))
    assert resolve_session(db, sid, settings) is None
    assert resolve_token(db, secret) is None


def test_token_lifecycle(db):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    view, secret = create_token(db, user_id=uid, name="agent", expires_in_days=None)
    assert secret.startswith(TOKEN_PREFIX) and "secret" not in view
    assert [t["id"] for t in list_tokens(db, uid)] == [view["id"]]
    row = resolve_token(db, secret)
    assert row["email"] == "u@ya.ru" and row["token_id"] == view["id"]
    assert resolve_token(db, "vt_wrong") is None
    assert resolve_token(db, "not-a-token") is None
    assert revoke_token(db, user_id=uid, token_id=view["id"]) is True
    assert revoke_token(db, user_id=uid, token_id=view["id"]) is False
    assert resolve_token(db, secret) is None
    assert list_tokens(db, uid) == []


def test_token_expiry(db):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    view, secret = create_token(db, user_id=uid, name="short", expires_in_days=1)
    assert view["expires_at"] > iso(utcnow())
    db.execute(
        "UPDATE api_tokens SET expires_at = ? WHERE id = ?",
        (iso(utcnow() - timedelta(seconds=1)), view["id"]),
    )
    assert resolve_token(db, secret) is None


def test_session_touch_updates_last_seen_only_after_interval(db, settings):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    sid = create_session(db, user_id=uid, user_agent="", settings=settings)
    key = sha256_hex(sid)
    before = db.execute("SELECT last_seen_at FROM sessions WHERE id = ?", (key,)).fetchone()[0]
    assert resolve_session(db, sid, settings) is not None
    assert db.execute("SELECT last_seen_at FROM sessions WHERE id = ?", (key,)).fetchone()[0] == before
    stale = iso(utcnow() - timedelta(minutes=5))
    db.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (stale, key))
    assert resolve_session(db, sid, settings) is not None
    assert db.execute("SELECT last_seen_at FROM sessions WHERE id = ?", (key,)).fetchone()[0] > stale


def test_corrupt_last_seen_invalidates_session(db, settings):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    sid = create_session(db, user_id=uid, user_agent="", settings=settings)
    db.execute("UPDATE sessions SET last_seen_at = 'garbage' WHERE id = ?", (sha256_hex(sid),))
    assert resolve_session(db, sid, settings) is None
    assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_sessions_and_tokens_are_isolated_per_user(db, settings):
    a = upsert_user(db, email="a@ya.ru", name="A", admin_email="")["id"]
    b = upsert_user(db, email="b@ya.ru", name="B", admin_email="")["id"]
    b_sid = create_session(db, user_id=b, user_agent="", settings=settings)
    for _ in range(settings.max_sessions_per_user + 3):
        create_session(db, user_id=a, user_agent="", settings=settings)
    assert resolve_session(db, b_sid, settings)["email"] == "b@ya.ru"
    b_view, b_secret = create_token(db, user_id=b, name="b-agent", expires_in_days=None)
    assert revoke_token(db, user_id=a, token_id=b_view["id"]) is False
    assert resolve_token(db, b_secret)["email"] == "b@ya.ru"
    assert list_tokens(db, a) == []


def test_token_last_used_is_recorded(db):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    view, secret = create_token(db, user_id=uid, name="agent", expires_in_days=None)
    assert view["last_used_at"] is None
    resolve_token(db, secret)
    assert list_tokens(db, uid)[0]["last_used_at"] is not None


def test_token_expiry_bounds_and_empty_email_rejected(db):
    uid = upsert_user(db, email="u@ya.ru", name="U", admin_email="")["id"]
    with pytest.raises(ValueError):
        create_token(db, user_id=uid, name="zero", expires_in_days=0)
    with pytest.raises(ValueError):
        create_token(db, user_id=uid, name="long", expires_in_days=100_000)
    with pytest.raises(ValueError):
        upsert_user(db, email="   ", name="X", admin_email=" ")
