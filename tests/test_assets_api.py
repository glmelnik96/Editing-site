import sqlite3

from server.app.assets.views import asset_view
from server.app.util import now_iso

SRT = b"1\n00:00:00,000 --> 00:00:01,000\nhi\n"


def _upload_small(client, name="s.srt", data=SRT, kind=None):
    files = {"file": (name, data, "application/octet-stream")}
    form = {"kind": kind} if kind else {}
    return client.post("/api/v1/assets/upload", files=files, data=form)


def _row(**over):
    base = {
        "id": "ast_0123456789ab", "user_id": "usr_0123456789ab", "kind": "video", "original_name": "a.mp4",
        "ext": "mp4", "size": 1, "status": "uploaded", "duration": None, "width": None, "height": None,
        "fps": None, "has_audio": None, "video_codec": None, "audio_codec": None, "error": None,
        "created_at": "2026-09-04T00:00:00.000Z", "last_access_at": "2026-09-04T00:00:00.000Z",
    }
    return {**base, **over}


def test_view_links_follow_status():
    v = asset_view(_row())
    assert v.files.model_dump() == {
        "proxy": None, "thumbs": None, "thumbs_meta": None, "peaks": None, "analysis": None, "vtt": None,
    }
    v = asset_view(_row(status="ready", has_audio=1))
    assert v.has_audio is True and v.files.proxy is None
    assert v.files.peaks == "/files/usr_0123456789ab/assets/ast_0123456789ab/peaks.json"
    assert v.files.thumbs == "/files/usr_0123456789ab/assets/ast_0123456789ab/thumbs.jpg"
    assert v.files.vtt is None
    v = asset_view(_row(status="proxy_ready"))
    assert v.files.proxy.endswith("/proxy.mp4")
    v = asset_view(_row(kind="audio", status="proxy_ready"))
    assert v.files.proxy.endswith("/proxy.m4a") and v.files.thumbs is None
    v = asset_view(_row(kind="subtitle", status="ready"))
    assert v.files.peaks is None and v.files.proxy is None
    assert v.files.vtt == "/files/usr_0123456789ab/assets/ast_0123456789ab/subs.vtt"


def test_small_upload_list_get_delete(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    r = _upload_small(client)
    assert r.status_code == 201, r.text
    asset = r.json()
    assert asset["kind"] == "subtitle" and asset["status"] == "ready" and asset["size"] == len(SRT)
    assert asset["original_name"] == "s.srt"
    source = settings.data_dir / me["id"] / "assets" / asset["id"] / "source.srt"
    assert source.read_bytes() == SRT
    listing = client.get("/api/v1/assets").json()["assets"]
    assert [a["id"] for a in listing] == [asset["id"]]
    assert client.get(f"/api/v1/assets/{asset['id']}").json()["id"] == asset["id"]
    quota = {"used_bytes": len(SRT), "limit_bytes": 10 * 1024 * 1024}
    assert client.get("/api/v1/me").json()["quota"] == quota
    assert client.delete(f"/api/v1/assets/{asset['id']}").status_code == 204
    assert not source.parent.exists()
    assert client.get(f"/api/v1/assets/{asset['id']}").status_code == 404
    assert client.get("/api/v1/assets").json()["assets"] == []


def test_small_upload_of_video_queues_analyze(client, login_as, settings):
    login_as()
    r = _upload_small(client, name="c.mp4", data=b"\0" * 100)
    assert r.status_code == 201 and r.json()["status"] == "uploaded"
    conn = sqlite3.connect(str(settings.db_path))
    job = conn.execute("SELECT type, target_id FROM jobs").fetchone()
    conn.close()
    assert job == ("analyze", r.json()["id"])


def test_small_upload_limits(client, login_as):
    login_as()
    r = _upload_small(client, name="big.mp3", data=b"\0" * (1024 * 1024 + 1))
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    r = _upload_small(client, name="e.srt", data=b"")
    assert r.status_code == 422 and r.json()["error"]["code"] == "empty_file"
    r = _upload_small(client, kind="image")
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_kind"
    assert client.get("/api/v1/assets").json()["assets"] == []


def test_delete_cancels_open_jobs(client, login_as, settings):
    login_as()
    asset_id = _upload_small(client, name="c.mp4", data=b"\0" * 10).json()["id"]
    assert client.delete(f"/api/v1/assets/{asset_id}").status_code == 204
    conn = sqlite3.connect(str(settings.db_path))
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "canceled"
    conn.close()


def test_foreign_asset_is_404(client, login_as):
    login_as()
    asset_id = _upload_small(client).json()["id"]
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 404
    assert client.delete(f"/api/v1/assets/{asset_id}").status_code == 404
    assert client.get("/api/v1/assets").json()["assets"] == []


def test_bearer_can_list_and_delete(bearer_client):
    r = _upload_small(bearer_client)
    assert r.status_code == 201, r.text
    assert len(bearer_client.get("/api/v1/assets").json()["assets"]) == 1
    assert bearer_client.delete(f"/api/v1/assets/{r.json()['id']}").status_code == 204


def test_me_has_quota_and_requires_auth(client, login_as):
    assert client.get("/api/v1/me").status_code == 401
    login_as()
    me = client.get("/api/v1/me").json()
    assert me["quota"] == {"used_bytes": 0, "limit_bytes": 10 * 1024 * 1024}
    assert me["role"] == "admin" and me["auth"] == "cookie" and now_iso().endswith("Z")


def test_subtitle_upload_produces_a_vtt_link(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    r = _upload_small(client)
    assert r.status_code == 201
    asset = r.json()
    assert asset["files"]["vtt"] == f"/files/{me['id']}/assets/{asset['id']}/subs.vtt"
    body = client.get(asset["files"]["vtt"])
    assert body.status_code == 200 and body.text.startswith("WEBVTT")


def test_broken_subtitle_file_is_rejected_on_upload(client, login_as):
    login_as()
    bad = b"\xd0\xbd\xd0\xb5 \xd1\x81\xd1\x83\xd0\xb1\xd1\x82\xd1\x8b"
    r = _upload_small(client, name="bad.srt", data=bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_subtitles"
    assert client.get("/api/v1/assets").json()["assets"] == []
