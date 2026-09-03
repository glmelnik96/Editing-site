import sqlite3
from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from server.app import health
from server.app import main as main_mod
from server.app.main import create_app
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
    assert r.json()["error"]["code"] == "not_found"


def test_app_keeps_a_long_lived_db_connection(client, app):
    keeper = app.state.db_keeper
    assert keeper.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert keeper.in_transaction is False


def test_healthz_degraded_when_disk_low(client, monkeypatch):
    monkeypatch.setattr(health, "disk_free_pct", lambda path: 5.0)
    body = client.get("/healthz").json()
    assert body["status"] == "degraded"
    assert body["disk_free_pct"] == 5.0


def test_healthz_reports_db_false_when_tables_missing(client, settings):
    conn = connect(settings.db_path)
    conn.execute("DROP TABLE schema_migrations")
    conn.close()
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["db"] is False
    assert r.json()["status"] == "degraded"


def test_healthz_degraded_when_heartbeat_unreadable(client, settings):
    conn = connect(settings.db_path)
    conn.execute("INSERT INTO heartbeats (name, at) VALUES ('worker', 'garbage')")
    conn.close()
    body = client.get("/healthz").json()
    assert body["status"] == "degraded"
    assert body["worker_seen_sec_ago"] is None


def test_keeper_connection_closed_on_shutdown(app):
    with TestClient(app):
        keeper = app.state.db_keeper
    with pytest.raises(sqlite3.ProgrammingError):
        keeper.execute("SELECT 1")


def test_migration_failure_surfaces_and_closes_connection(settings, monkeypatch, tmp_path):
    opened = []
    real_connect = main_mod.connect

    def tracking_connect(path):
        conn = real_connect(path)
        opened.append(conn)
        return conn

    def failing_migrate(conn):
        raise RuntimeError("boom")

    monkeypatch.setattr(main_mod, "connect", tracking_connect)
    monkeypatch.setattr(main_mod, "migrate", failing_migrate)
    app = create_app(settings, web_dist=tmp_path / "no-dist")
    with pytest.raises(RuntimeError, match="boom"), TestClient(app):
        pass
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_static_mount_keeps_api_routes_first(settings, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>spa</h1>", encoding="utf-8")
    (dist / "404.html").write_text("<h1>nf</h1>", encoding="utf-8")
    app = create_app(settings, web_dist=dist)
    with TestClient(app, headers={"Origin": "http://testserver"}) as c:
        assert c.get("/").text == "<h1>spa</h1>"
        assert c.get("/healthz").status_code == 200
        assert c.get("/api/v1/openapi.json").status_code == 200
        r = c.get("/api/v1/nope")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"
        assert c.post("/api/v1/nope").status_code == 404


def test_lazy_app_attribute_builds_once():
    first = main_mod.app
    assert first is main_mod.app
    with pytest.raises(AttributeError):
        main_mod.nope  # noqa: B018


def test_healthz_degraded_when_database_cannot_open(client, monkeypatch):
    def failing_connect(path):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(health, "connect", failing_connect)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["db"] is False
    assert r.json()["status"] == "degraded"
