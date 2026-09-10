import sqlite3

from server.app.jobs import enqueue_job
from server.app.util import now_iso

VIDEO = "ast_000000000001"


def seed_asset(settings, user_id):
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, has_audio, "
        "created_at, last_access_at) VALUES (?, ?, 'video', 'a.mp4', 'mp4', 1, 'proxy_ready', 60, 1, ?, ?)",
        (VIDEO, user_id, now_iso(), now_iso()),
    )
    conn.commit()
    conn.close()


def make_project(client, settings, user_id):
    seed_asset(settings, user_id)
    doc = {"clips": [{"asset_id": VIDEO, "in": 1.0, "out": 6.0}]}
    return client.post("/api/v1/projects", json={"name": "Мой", "doc": doc}).json()


def seed_render(settings, project_id, user_id, render_id="rnd_000000000001"):
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO renders (id, project_id, user_id, job_id, quality, path, size, duration, "
        "created_at, expires_at) VALUES (?, ?, ?, 'job_1', 'draft', '/x.mp4', 100, 5, ?, ?)",
        (render_id, project_id, user_id, now_iso(), "2099-01-01T00:00:00.000Z"),
    )
    conn.commit()
    conn.close()
    return render_id


def test_render_queues_a_job(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    r = client.post(f"/api/v1/projects/{project['id']}/render", json={"quality": "final"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"].startswith("job_") and body["quality"] == "final"

    job = client.get(f"/api/v1/jobs/{body['job_id']}").json()
    assert job["type"] == "render" and job["status"] == "queued" and job["progress"] == 0


def test_default_quality_is_draft(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    r = client.post(f"/api/v1/projects/{project['id']}/render", json={})
    assert r.status_code == 202 and r.json()["quality"] == "draft"


def test_bad_quality_is_rejected(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    r = client.post(f"/api/v1/projects/{project['id']}/render", json={"quality": "ultra"})
    assert r.status_code == 422


def test_empty_project_cannot_be_rendered(client, login_as, settings):
    login_as()
    project = client.post("/api/v1/projects", json={"name": "Пустой"}).json()
    r = client.post(f"/api/v1/projects/{project['id']}/render", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "empty_project"


def test_queue_limit_per_user(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    for _ in range(settings.max_renders_queued + 1):
        assert client.post(f"/api/v1/projects/{project['id']}/render", json={}).status_code == 202
    r = client.post(f"/api/v1/projects/{project['id']}/render", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "too_many_renders"


def test_renders_list_and_card(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    render_id = seed_render(settings, project["id"], me["id"])

    listing = client.get(f"/api/v1/projects/{project['id']}/renders").json()["renders"]
    assert [r["id"] for r in listing] == [render_id]
    assert listing[0]["download"] == (
        f"/files/{me['id']}/projects/{project['id']}/renders/{render_id}.mp4"
    )
    assert client.get(f"/api/v1/renders/{render_id}").json()["quality"] == "draft"


def test_render_delete(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    render_id = seed_render(settings, project["id"], me["id"])
    assert client.delete(f"/api/v1/renders/{render_id}").status_code == 204
    assert client.get(f"/api/v1/renders/{render_id}").status_code == 404
    assert client.delete(f"/api/v1/renders/{render_id}").status_code == 404


def test_deleting_a_project_drops_its_renders(client, login_as, settings):
    """Готовый ролик удалённого проекта не нужен никому: пересобрать его больше не из чего."""
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    render_id = seed_render(settings, project["id"], me["id"])
    assert client.delete(f"/api/v1/projects/{project['id']}").status_code == 204
    assert client.get(f"/api/v1/renders/{render_id}").status_code == 404


def test_deleting_a_project_cancels_its_render(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    job_id = client.post(f"/api/v1/projects/{project['id']}/render", json={}).json()["job_id"]
    assert client.delete(f"/api/v1/projects/{project['id']}").status_code == 204
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "canceled"


def test_analysis_cannot_be_canceled(client, login_as, settings):
    """Отменить анализ значило бы оставить запись навсегда в analyzing: в проект её не поставить,
    перезапустить нечем. Кому нужно прервать — удаляет запись, и это отменяет задания правильно."""
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    conn = sqlite3.connect(str(settings.db_path), isolation_level=None)
    try:
        job_id = enqueue_job(conn, user_id=me["id"], type_="analyze", target_id=VIDEO)
    finally:
        conn.close()

    r = client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert r.status_code == 422 and r.json()["error"]["code"] == "cannot_cancel"
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "queued"


def test_job_can_be_canceled(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    job_id = client.post(f"/api/v1/projects/{project['id']}/render", json={}).json()["job_id"]
    assert client.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 204
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "canceled"


def test_foreign_things_are_404(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    render_id = seed_render(settings, project["id"], me["id"])
    job_id = client.post(f"/api/v1/projects/{project['id']}/render", json={}).json()["job_id"]
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(f"/api/v1/renders/{render_id}").status_code == 404
    assert client.delete(f"/api/v1/renders/{render_id}").status_code == 404
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert client.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 404
    assert client.post(f"/api/v1/projects/{project['id']}/render", json={}).status_code == 404
    assert client.get(f"/api/v1/projects/{project['id']}/renders").status_code == 404


def test_agent_can_render_with_a_token(bearer_client, settings):
    me = bearer_client.get("/api/v1/me").json()
    project = make_project(bearer_client, settings, me["id"])
    r = bearer_client.post(f"/api/v1/projects/{project['id']}/render", json={"quality": "draft"})
    assert r.status_code == 202
    assert bearer_client.get(f"/api/v1/jobs/{r.json()['job_id']}").status_code == 200


def test_renders_require_auth(client):
    assert client.get("/api/v1/renders/rnd_000000000001").status_code == 401
    assert client.get("/api/v1/jobs/job_1").status_code == 401


def test_render_options_travel_to_the_job(client, login_as, settings):
    """Формат, разрешение и битрейт уходят в задание: собирает по ним воркер, а не маршрут."""
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    body = {"quality": "target", "format": "mp4", "short_side": 480, "bitrate_kbps": 1500}
    r = client.post(f"/api/v1/projects/{project['id']}/render", json=body)
    assert r.status_code == 202, r.text
    assert r.json()["format"] == "mp4" and r.json()["quality"] == "target"
    conn = sqlite3.connect(str(settings.db_path))
    params = conn.execute("SELECT params FROM jobs WHERE id = ?", (r.json()["job_id"],)).fetchone()[0]
    conn.close()
    import json

    assert json.loads(params) == body


def test_target_quality_needs_a_bitrate(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    r = client.post(f"/api/v1/projects/{project['id']}/render", json={"quality": "target"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bitrate_required"


def test_render_rejects_unknown_format_and_size(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    url = f"/api/v1/projects/{project['id']}/render"
    assert client.post(url, json={"format": "avi"}).status_code == 422
    assert client.post(url, json={"short_side": 999}).status_code == 422
    assert client.post(url, json={"quality": "target", "bitrate_kbps": 10}).status_code == 422


def test_webm_without_vp9_is_refused_before_queueing(client, login_as, settings, monkeypatch):
    """Без VP9 сборка упала бы через минуты кодирования сырым stderr — отказ нужен сразу."""
    from server.app.projects import routes

    monkeypatch.setattr(routes, "missing_encoder", lambda _settings, _fmt: "нет VP9")
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    r = client.post(f"/api/v1/projects/{project['id']}/render", json={"format": "webm"})
    assert r.status_code == 503 and r.json()["error"]["message"] == "нет VP9"
    assert client.get("/api/v1/jobs").json()["jobs"] == []


def test_render_card_tells_format_and_size(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO renders (id, project_id, user_id, job_id, quality, format, width, height, path, "
        "size, duration, created_at, expires_at) VALUES ('rnd_00000000000a', ?, ?, 'job_1', 'high', "
        "'webm', 1280, 720, '/x.webm', 100, 5, ?, '2099-01-01T00:00:00.000Z')",
        (project["id"], me["id"], now_iso()),
    )
    conn.commit()
    conn.close()
    seed_render(settings, project["id"], me["id"])  # старая строка: формат по умолчанию mp4
    cards = {c["id"]: c for c in client.get(f"/api/v1/projects/{project['id']}/renders").json()["renders"]}
    webm = cards["rnd_00000000000a"]
    assert (webm["format"], webm["width"], webm["height"]) == ("webm", 1280, 720)
    assert webm["download"].endswith("/renders/rnd_00000000000a.webm")
    old = cards["rnd_000000000001"]
    assert old["format"] == "mp4" and old["width"] is None and old["download"].endswith(".mp4")
