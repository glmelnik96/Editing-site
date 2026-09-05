import sqlite3

from server.app.util import now_iso

VIDEO = "ast_000000000001"
AUDIO = "ast_000000000003"


def seed_assets(client, settings, user_id):
    """Готовые ассеты прямо в базе: путь загрузки и обработки уже проверен другими тестами."""
    conn = sqlite3.connect(str(settings.db_path))
    for asset_id, kind, duration in ((VIDEO, "video", 120.0), (AUDIO, "audio", 200.0)):
        conn.execute(
            "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
            "created_at, last_access_at) VALUES (?, ?, ?, 'a', 'mp4', 1, 'ready', ?, ?, ?)",
            (asset_id, user_id, kind, duration, now_iso(), now_iso()),
        )
    conn.commit()
    conn.close()


def doc(**over):
    return {"clips": [{"asset_id": VIDEO, "in": 1.0, "out": 5.0}], **over}


def test_create_read_list_and_save(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])

    r = client.post("/api/v1/projects", json={"name": "Подкаст", "doc": doc()})
    assert r.status_code == 201, r.text
    project = r.json()
    assert project["version"] == 1 and project["status"] == "draft"
    assert project["doc"]["clips"][0]["id"] == "c1"
    assert project["doc"]["clips"][0]["in_verified"] is False

    listing = client.get("/api/v1/projects").json()["projects"]
    assert len(listing) == 1 and listing[0]["clips_count"] == 1 and "doc" not in listing[0]

    got = client.get(f"/api/v1/projects/{project['id']}").json()
    assert got["doc"] == project["doc"]

    r = client.put(
        f"/api/v1/projects/{project['id']}",
        json={"name": "Подкаст 2", "version": 1, "doc": doc(output={"aspect": "9:16"})},
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and r.json()["doc"]["output"]["aspect"] == "9:16"


def test_create_without_a_document(client, login_as, settings):
    login_as()
    r = client.post("/api/v1/projects", json={"name": "Пустой"})
    assert r.status_code == 201 and r.json()["doc"]["clips"] == []


def test_validation_errors_list_every_bad_field(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    r = client.post("/api/v1/projects", json={"name": "Плохой", "doc": {
        "clips": [{"asset_id": VIDEO, "in": -1, "out": 5}], "output": {"fps": 24},
    }})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "invalid_project"
    fields = {e["field"] for e in body["details"]["errors"]}
    assert fields == {"clips[0].in", "output.fps"}


def test_stale_version_returns_409_with_the_current_project(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    client.put(f"/api/v1/projects/{p['id']}", json={"name": "Мой", "version": 1, "doc": doc()})
    r = client.put(f"/api/v1/projects/{p['id']}", json={"name": "Мой", "version": 1, "doc": doc()})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "version_conflict"
    assert err["details"]["project"]["version"] == 2
    assert err["details"]["project"]["doc"]["clips"]


def test_foreign_project_is_404(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(f"/api/v1/projects/{p['id']}").status_code == 404
    r = client.put(f"/api/v1/projects/{p['id']}", json={"name": "x", "version": 1, "doc": doc()})
    assert r.status_code == 404
    assert client.delete(f"/api/v1/projects/{p['id']}").status_code == 404
    assert client.post(f"/api/v1/projects/{p['id']}/finish").status_code == 404
    assert client.get("/api/v1/projects").json()["projects"] == []


def test_delete_project(client, login_as, settings):
    login_as()
    p = client.post("/api/v1/projects", json={"name": "Мой"}).json()
    assert client.delete(f"/api/v1/projects/{p['id']}").status_code == 204
    assert client.get(f"/api/v1/projects/{p['id']}").status_code == 404
    assert client.delete(f"/api/v1/projects/{p['id']}").status_code == 404


def test_finish_marks_the_project_and_frees_assets(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    r = client.post(f"/api/v1/projects/{p['id']}/finish")
    assert r.status_code == 200 and r.json()["status"] == "finished"
    assert client.get(f"/api/v1/assets/{VIDEO}").status_code == 404
    r = client.put(f"/api/v1/projects/{p['id']}", json={"name": "Мой", "version": 2, "doc": doc()})
    assert r.status_code == 422 and r.json()["error"]["details"]["errors"][0]["field"] == "status"


def test_asset_in_use_cannot_be_deleted(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    r = client.delete(f"/api/v1/assets/{VIDEO}")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "asset_in_use"
    assert err["details"]["projects"] == [{"id": p["id"], "name": "Мой"}]
    assert client.delete(f"/api/v1/assets/{AUDIO}").status_code == 204  # музыка нигде не занята
    assert client.delete(f"/api/v1/projects/{p['id']}").status_code == 204
    assert client.delete(f"/api/v1/assets/{VIDEO}").status_code == 204  # проект удалён — ассет свободен


def test_agent_can_drive_projects_with_a_token(bearer_client, settings):
    me = bearer_client.get("/api/v1/me").json()
    seed_assets(bearer_client, settings, me["id"])
    p = bearer_client.post("/api/v1/projects", json={"name": "Агентский", "doc": doc()}).json()
    saved = bearer_client.put(
        f"/api/v1/projects/{p['id']}",
        json={"name": "Агентский", "version": 1, "doc": doc(music={"asset_id": AUDIO, "volume": 0.2})},
    )
    assert saved.status_code == 200 and saved.json()["doc"]["music"]["volume"] == 0.2


def test_projects_require_auth(client):
    assert client.get("/api/v1/projects").status_code == 401
    assert client.post("/api/v1/projects", json={"name": "x"}).status_code == 401


def test_project_deleted_between_check_and_save_is_404(client, login_as, settings, monkeypatch):
    """Проект исчез в момент сохранения: клиенту «не найден», а не внутренняя ошибка."""
    login_as()
    p = client.post("/api/v1/projects", json={"name": "Мой"}).json()
    from server.app.projects import routes as project_routes

    def vanish(*args, **kwargs):
        raise KeyError(p["id"])

    monkeypatch.setattr(project_routes, "save_project", vanish)
    r = client.put(f"/api/v1/projects/{p['id']}", json={"name": "Мой", "version": 1, "doc": None})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
