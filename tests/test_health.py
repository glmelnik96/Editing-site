from datetime import timedelta

from server.app.util import iso, utcnow
from server.db.core import connect


def test_healthz_ok_without_worker(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert 0 <= body["disk_free_pct"] <= 100
    assert body["worker_seen_sec_ago"] is None


def test_healthz_degraded_when_worker_stale(client, settings):
    conn = connect(settings.db_path)
    conn.execute(
        "INSERT INTO heartbeats (name, at) VALUES ('worker', ?)", (iso(utcnow() - timedelta(seconds=600)),)
    )
    conn.close()
    body = client.get("/healthz").json()
    assert body["status"] == "degraded"
    assert body["worker_seen_sec_ago"] >= 600


def test_unknown_route_returns_json_error(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "http_error"


def test_app_keeps_a_long_lived_db_connection(client, app):
    keeper = app.state.db_keeper
    assert keeper.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert keeper.in_transaction is False
