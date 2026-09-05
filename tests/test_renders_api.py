import sqlite3

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


def test_finishing_a_project_drops_its_renders(client, login_as, settings):
    """Готовый ролик завершённого проекта не нужен: документ остаётся, ролик пересобирается."""
    login_as()
    me = client.get("/api/v1/me").json()
    project = make_project(client, settings, me["id"])
    render_id = seed_render(settings, project["id"], me["id"])
    assert client.post(f"/api/v1/projects/{project['id']}/finish").status_code == 200
    assert client.get(f"/api/v1/renders/{render_id}").status_code == 404


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
