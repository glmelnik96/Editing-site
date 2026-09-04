import os

from server.app.ratelimit import FixedWindowLimiter
from server.app.uploads import store

OCTET = {"Content-Type": "application/octet-stream"}


def _create(client, size, filename="clip.mp4", kind=None):
    body = {"filename": filename, "size": size}
    if kind:
        body["kind"] = kind
    r = client.post("/api/v1/uploads", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _put(client, upload_id, idx, data):
    return client.put(f"/api/v1/uploads/{upload_id}/chunks/{idx}", content=data, headers=OCTET)


def _whitelist_and_login(client, login_as, email):
    login_as()  # админ
    assert client.post("/api/v1/admin/whitelist", json={"email": email}).status_code == 201
    return login_as(email, "Other")


def test_roundtrip_out_of_order_repeat_and_complete(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    data = os.urandom(2 * 1024 + 300)
    up = _create(client, len(data))
    assert up["chunk_size"] == 1024 and up["total_chunks"] == 3 and up["expires_at"].endswith("Z")
    uid = up["upload_id"]
    assert _put(client, uid, 2, data[2048:]).status_code == 204
    assert _put(client, uid, 0, data[:1024]).status_code == 204
    assert _put(client, uid, 0, data[:1024]).status_code == 204  # повтор части
    st = client.get(f"/api/v1/uploads/{uid}").json()
    assert st == {"upload_id": uid, "received": [0, 2], "total": 3, "size": len(data), "chunk_size": 1024}
    r = client.post(f"/api/v1/uploads/{uid}/complete")
    assert r.status_code == 409 and r.json()["error"]["code"] == "incomplete"
    assert r.json()["error"]["details"]["missing"] == [1]
    assert _put(client, uid, 1, data[1024:2048]).status_code == 204
    r = client.post(f"/api/v1/uploads/{uid}/complete")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "uploaded"
    asset_id = r.json()["asset_id"]
    source = settings.data_dir / me["id"] / "assets" / asset_id / "source.mp4"
    assert source.read_bytes() == data
    assert client.get(f"/api/v1/uploads/{uid}").status_code == 404
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 200


def test_chunk_length_is_checked(client, login_as):
    login_as()
    uid = _create(client, 2048)["upload_id"]
    r = _put(client, uid, 0, b"x" * 1000)
    assert r.status_code == 422 and r.json()["error"]["code"] == "chunk_size_mismatch"
    assert r.json()["error"]["details"] == {"expected": 1024, "received": 1000}
    r = _put(client, uid, 1, b"x" * 1025)
    assert r.status_code == 422
    r = _put(client, uid, 2, b"x")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_such_chunk"
    assert client.get(f"/api/v1/uploads/{uid}").json()["received"] == []


def test_create_validation_and_limits(client, login_as, monkeypatch):
    login_as()
    r = client.post("/api/v1/uploads", json={"filename": "a.mp4", "size": 0})
    assert r.status_code == 422
    r = client.post("/api/v1/uploads", json={"filename": "a.mp4", "size": 9 * 1024 * 1024})
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    r = client.post("/api/v1/uploads", json={"filename": "a.mp4", "size": 10, "kind": "image"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_kind"
    _create(client, 6 * 1024 * 1024)
    r = client.post("/api/v1/uploads", json={"filename": "b.mp4", "size": 5 * 1024 * 1024})
    assert r.status_code == 413 and r.json()["error"]["code"] == "quota_exceeded"
    monkeypatch.setattr(store, "disk_free_pct_safe", lambda _p: 3.0)
    r = client.post("/api/v1/uploads", json={"filename": "c.mp4", "size": 10})
    assert r.status_code == 507 and r.json()["error"]["code"] == "disk_low"


def test_upload_rate_limit_per_user(app, client, login_as):
    login_as()
    app.state.upload_limiter = FixedWindowLimiter(2, 3600)
    _create(client, 10)
    _create(client, 10)
    r = client.post("/api/v1/uploads", json={"filename": "a.mp4", "size": 10})
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


def test_foreign_upload_is_404(client, login_as):
    login_as()
    uid = _create(client, 10)["upload_id"]
    _whitelist_and_login(client, login_as, "other@ya.ru")
    assert client.get(f"/api/v1/uploads/{uid}").status_code == 404
    assert _put(client, uid, 0, b"x" * 10).status_code == 404
    assert client.post(f"/api/v1/uploads/{uid}/complete").status_code == 404
    assert client.delete(f"/api/v1/uploads/{uid}").status_code == 404


def test_delete_upload_frees_quota(client, login_as, settings):
    login_as()
    up = _create(client, 500)
    path = settings.uploads_tmp_path / up["upload_id"]
    assert path.stat().st_size == 500
    assert client.get("/api/v1/me").json()["quota"]["used_bytes"] == 500
    assert client.delete(f"/api/v1/uploads/{up['upload_id']}").status_code == 204
    assert not path.exists()
    assert client.get("/api/v1/me").json()["quota"]["used_bytes"] == 0


def test_agent_uploads_with_bearer_token(bearer_client):
    data = b"a" * 1024 + b"b" * 10
    up = _create(bearer_client, len(data), filename="talk.wav")
    assert _put(bearer_client, up["upload_id"], 0, data[:1024]).status_code == 204
    assert _put(bearer_client, up["upload_id"], 1, data[1024:]).status_code == 204
    r = bearer_client.post(f"/api/v1/uploads/{up['upload_id']}/complete")
    assert r.status_code == 200, r.text
    asset = bearer_client.get(f"/api/v1/assets/{r.json()['asset_id']}").json()
    assert asset["kind"] == "audio" and asset["original_name"] == "talk.wav"


def test_requires_auth(client):
    assert client.post("/api/v1/uploads", json={"filename": "a.mp4", "size": 10}).status_code == 401
    assert client.get("/api/v1/uploads/upl_000000000000").status_code == 401
