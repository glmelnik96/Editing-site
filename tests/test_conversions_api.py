import sqlite3

from server.app.util import now_iso

ASSET = "ast_000000000001"


def seed_asset(
    settings, user_id, *, status="proxy_ready", has_audio=1, duration=60.0,
    kind="video", asset_id=ASSET, name="встреча.mp4",
):
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, has_audio, "
        "created_at, last_access_at) VALUES (?, ?, ?, ?, 'mp4', 1, ?, ?, ?, ?, ?)",
        (asset_id, user_id, kind, name, status, duration, has_audio, now_iso(), now_iso()),
    )
    conn.commit()
    conn.close()
    return asset_id


def seed_conversion(settings, user_id, conversion_id, fmt="mp3", created_at=None, asset_id=ASSET):
    created = created_at or now_iso()
    path = str(settings.data_dir / user_id / "assets" / asset_id / "conversions" / f"{conversion_id}.{fmt}")
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO conversions (id, user_id, asset_id, job_id, format, path, size, duration, "
        "created_at, expires_at) VALUES (?, ?, ?, 'job_1', ?, ?, 100, 60, ?, ?)",
        (conversion_id, user_id, asset_id, fmt, path, created, "2099-01-01T00:00:00.000Z"),
    )
    conn.commit()
    conn.close()
    return conversion_id


def test_convert_queues_a_job(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "mp3"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"].startswith("job_") and body["conversion_id"].startswith("cnv_")

    job = client.get(f"/api/v1/jobs/{body['job_id']}").json()
    assert job["type"] == "convert" and job["status"] == "queued" and job["progress"] == 0


def test_unknown_format_is_422(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "gif"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_format"


def test_video_without_audio_cannot_extract_sound(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"], has_audio=0)
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "mp3"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_audio"


def test_audio_cannot_become_mp4(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"], kind="audio", name="a.mp3")
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "mp4"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_video"


def test_asset_not_ready_is_422(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"], status="analyzing")
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "wav"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "asset_not_ready"


def test_too_long_asset_is_422(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"], duration=settings.max_total_duration_sec + 1)
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "m4a"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_long"


def test_second_convert_while_queued_is_409(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    assert client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "mp3"}).status_code == 202
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "wav"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_queued"


def test_convert_and_render_share_the_queue_limit(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    project = client.post(
        "/api/v1/projects",
        json={"name": "Ролик", "doc": {"clips": [{"asset_id": ASSET, "in": 1.0, "out": 6.0}]}},
    ).json()
    slots = settings.max_renders_queued + 1
    for _ in range(slots):
        assert client.post(f"/api/v1/projects/{project['id']}/render", json={}).status_code == 202
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "mp3"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "too_many_renders"


def test_missing_mp3_encoder_is_503(client, login_as, settings, monkeypatch):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    from server.app.conversions import routes as conv_routes

    monkeypatch.setattr(conv_routes, "missing_encoder", lambda _s, fmt: "нет MP3" if fmt == "mp3" else None)
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "mp3"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "encoder_unavailable"


def test_aac_convert_queues(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "aac"})
    assert r.status_code == 202, r.text


def test_webm_convert_queues_for_video(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "webm"})
    assert r.status_code == 202, r.text


def test_audio_cannot_become_webm(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"], kind="audio", name="a.mp3")
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "webm"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_video"


def test_missing_webm_encoder_is_503(client, login_as, settings, monkeypatch):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    from server.app.conversions import routes as conv_routes

    monkeypatch.setattr(conv_routes, "missing_encoder", lambda _s, fmt: "нет VP9" if fmt == "webm" else None)
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "webm"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "encoder_unavailable"


def test_missing_ogg_encoder_is_503(client, login_as, settings, monkeypatch):
    """libvorbis есть не в каждой сборке, а раньше ogg уходил в ffmpeg без всякой проверки:
    задание падало сырым «Unknown encoder 'libvorbis'» вместо внятного отказа."""
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    from server.app.conversions import routes as conv_routes

    monkeypatch.setattr(
        conv_routes, "missing_encoder", lambda _s, fmt: "нет Vorbis" if fmt == "ogg" else None
    )
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "ogg"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "encoder_unavailable"


def test_list_and_card_and_delete(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    cid = seed_conversion(settings, me["id"], "cnv_000000000001")
    folder = settings.data_dir / me["id"] / "assets" / ASSET / "conversions"
    folder.mkdir(parents=True)
    (folder / f"{cid}.mp3").write_bytes(b"x")

    listing = client.get(f"/api/v1/assets/{ASSET}/conversions").json()
    assert [c["id"] for c in listing["conversions"]] == [cid]
    assert listing["conversions"][0]["download"] == (
        f"/files/{me['id']}/assets/{ASSET}/conversions/{cid}.mp3"
    )
    assert listing["conversions"][0]["expires_at"].startswith("2099")
    assert listing["conversions"][0]["original_name"] == "встреча.mp4"
    card = client.get(f"/api/v1/conversions/{cid}").json()
    assert card["format"] == "mp3" and card["size"] == 100

    assert client.delete(f"/api/v1/conversions/{cid}").status_code == 204
    assert not (folder / f"{cid}.mp3").exists()
    assert client.get(f"/api/v1/conversions/{cid}").status_code == 404
    assert client.delete(f"/api/v1/conversions/{cid}").status_code == 404


def test_queueing_the_eleventh_does_not_destroy_the_oldest(client, login_as, settings):
    """Вытеснение переехало на успех задания: отменённая или упавшая конвертация не должна
    стоить человеку готового файла, которого ей нечем заменить."""
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    folder = settings.data_dir / me["id"] / "assets" / ASSET / "conversions"
    folder.mkdir(parents=True)
    for i in range(10):
        cid = f"cnv_0000000000{i:02d}"
        seed_conversion(settings, me["id"], cid, created_at=f"2026-01-{i + 1:02d}T00:00:00.000Z")
        (folder / f"{cid}.mp3").write_bytes(b"x")
    assert client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "wav"}).status_code == 202
    left = client.get(f"/api/v1/assets/{ASSET}/conversions").json()["conversions"]
    assert len(left) == 10
    assert "cnv_000000000000" in {c["id"] for c in left}
    assert (folder / "cnv_000000000000.mp3").exists()


def test_foreign_conversion_is_404(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    cid = seed_conversion(settings, me["id"], "cnv_000000000001")
    job_id = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "mp3"}).json()["job_id"]
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(f"/api/v1/conversions/{cid}").status_code == 404
    assert client.delete(f"/api/v1/conversions/{cid}").status_code == 404
    assert client.get(f"/api/v1/assets/{ASSET}/conversions").status_code == 404
    assert client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "mp3"}).status_code == 404
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404


def test_agent_can_convert_with_a_token(bearer_client, settings):
    me = bearer_client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    r = bearer_client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "m4a"})
    assert r.status_code == 202
    assert bearer_client.get(f"/api/v1/jobs/{r.json()['job_id']}").status_code == 200


def test_conversions_require_auth(client):
    assert client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "mp3"}).status_code == 401
    assert client.get(f"/api/v1/assets/{ASSET}/conversions").status_code == 401
    assert client.get("/api/v1/conversions/cnv_000000000001").status_code == 401
    assert client.get("/api/v1/conversions").status_code == 401


def test_list_all_conversions_is_mine_newest_first(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    a1 = seed_asset(settings, me["id"], asset_id="ast_000000000001", name="встреча.mp4")
    a2 = seed_asset(settings, me["id"], asset_id="ast_000000000002", name="нарезка.mp4")
    seed_conversion(
        settings, me["id"], "cnv_00000000000a",
        created_at="2026-01-01T00:00:00.000Z", asset_id=a1,
    )
    seed_conversion(
        settings, me["id"], "cnv_00000000000b",
        created_at="2026-02-01T00:00:00.000Z", asset_id=a2,
    )
    listing = client.get("/api/v1/conversions").json()["conversions"]
    assert [c["id"] for c in listing] == ["cnv_00000000000b", "cnv_00000000000a"]
    assert listing[0]["original_name"] == "нарезка.mp4"
    assert listing[1]["original_name"] == "встреча.mp4"


def test_list_all_conversions_hides_foreign(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    seed_conversion(settings, me["id"], "cnv_000000000001")
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get("/api/v1/conversions").json()["conversions"] == []


def test_deleting_an_asset_drops_conversion_files(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    cid = seed_conversion(settings, me["id"], "cnv_000000000001")
    folder = settings.data_dir / me["id"] / "assets" / ASSET / "conversions"
    folder.mkdir(parents=True)
    (folder / f"{cid}.mp3").write_bytes(b"x")
    assert client.delete(f"/api/v1/assets/{ASSET}").status_code == 204
    assert not folder.exists()
    assert client.get(f"/api/v1/conversions/{cid}").status_code == 404
