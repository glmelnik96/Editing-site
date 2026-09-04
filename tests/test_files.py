import sqlite3

OLD = "2026-01-01T00:00:00.000Z"


def _ready_video_asset(client, settings, user_id, peaks=b'{"rate": 50, "peaks": []}'):
    """Ассет-видео в статусе ready с peaks.json на диске (анализ в этом плане не запускается)."""
    files = {"file": ("c.mp4", b"\0" * 10, "application/octet-stream")}
    r = client.post("/api/v1/assets/upload", files=files)
    assert r.status_code == 201, r.text
    asset_id = r.json()["id"]
    (settings.data_dir / user_id / "assets" / asset_id / "peaks.json").write_bytes(peaks)
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute("UPDATE assets SET status = 'ready', last_access_at = ? WHERE id = ?", (OLD, asset_id))
    conn.commit()
    conn.close()
    return asset_id


def _url(user_id, asset_id, name):
    return f"/files/{user_id}/assets/{asset_id}/{name}"


def test_serves_public_file_and_touches_last_access(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    asset_id = _ready_video_asset(client, settings, me["id"])
    url = client.get(f"/api/v1/assets/{asset_id}").json()["files"]["peaks"]
    assert url == _url(me["id"], asset_id, "peaks.json")
    r = client.get(url)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, max-age=3600"
    assert r.json() == {"rate": 50, "peaks": []}
    assert client.get(f"/api/v1/assets/{asset_id}").json()["last_access_at"] > OLD


def test_range_requests(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    asset_id = _ready_video_asset(client, settings, me["id"], peaks=b"0123456789")
    r = client.get(_url(me["id"], asset_id, "peaks.json"), headers={"Range": "bytes=2-4"})
    assert r.status_code == 206 and r.content == b"234"


def test_source_and_unknown_names_are_forbidden(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    asset_id = _ready_video_asset(client, settings, me["id"])
    for name in ("source.mp4", "evil.txt", "audio16k.wav"):
        r = client.get(_url(me["id"], asset_id, name))
        assert r.status_code == 403, name
        assert r.json()["error"]["code"] == "forbidden"


def test_missing_foreign_and_unknown_are_404(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    asset_id = _ready_video_asset(client, settings, me["id"])
    assert client.get(_url(me["id"], asset_id, "thumbs.jpg")).status_code == 404
    assert client.get(_url(me["id"], "ast_000000000000", "peaks.json")).status_code == 404
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(_url(me["id"], asset_id, "peaks.json")).status_code == 404


def test_own_prefix_with_foreign_asset_is_404(client, login_as, settings):
    """Главная гарантия: подстановка чужого ассета под своим user_id упирается в фильтр по владельцу."""
    login_as()
    owner = client.get("/api/v1/me").json()
    victim_asset = _ready_video_asset(client, settings, owner["id"])
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    thief = client.get("/api/v1/me").json()
    assert thief["id"] != owner["id"]
    url = _url(thief["id"], victim_asset, "peaks.json")
    assert client.get(url).status_code == 404
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": url}).status_code == 404


def test_files_require_auth(client):
    r = client.get(_url("usr_000000000000", "ast_000000000000", "peaks.json"))
    assert r.status_code == 401


def test_authz_for_caddy(client, login_as, settings, bearer_client):
    me = client.get("/api/v1/me").json()  # bearer_client уже выполнил login_as() для client
    asset_id = _ready_video_asset(client, settings, me["id"])
    ok = _url(me["id"], asset_id, "peaks.json")
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": ok}).status_code == 204
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": ok + "?t=1"}).status_code == 204
    assert bearer_client.get("/internal/authz", headers={"X-Forwarded-Uri": ok}).status_code == 204
    r = client.get("/internal/authz", headers={"X-Forwarded-Uri": _url(me["id"], asset_id, "source.mp4")})
    assert r.status_code == 403
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": "/files/x/y"}).status_code == 404
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": "/api/v1/me"}).status_code == 404
    assert client.get("/internal/authz").status_code == 404
    other = _url("usr_000000000000", asset_id, "peaks.json")
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": other}).status_code == 404
    assert client.get(f"/api/v1/assets/{asset_id}").json()["last_access_at"] > OLD


def test_authz_requires_auth(app):
    from starlette.testclient import TestClient

    with TestClient(app) as anon:
        r = anon.get("/internal/authz", headers={"X-Forwarded-Uri": "/files/a/assets/b/peaks.json"})
        assert r.status_code == 401
