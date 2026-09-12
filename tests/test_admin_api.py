from server.db.core import connect


def test_admin_manages_whitelist_and_user_can_login(login_as):
    admin = login_as("admin@ya.ru")
    assert admin.get("/api/v1/admin/whitelist").json() == {"emails": []}
    r = admin.post("/api/v1/admin/whitelist", json={"email": " User@YA.ru "})
    assert r.status_code == 201
    assert r.json()["email"] == "user@ya.ru"
    assert admin.post("/api/v1/admin/whitelist", json={"email": "user@ya.ru"}).status_code == 201
    entries = admin.get("/api/v1/admin/whitelist").json()["emails"]
    assert [e["email"] for e in entries] == ["user@ya.ru"]
    assert entries[0]["added_by"] == "admin@ya.ru"

    user = login_as("user@ya.ru", "User")
    assert user.get("/api/v1/me").json()["role"] == "user"
    assert user.get("/api/v1/admin/stats").status_code == 403
    assert user.get("/api/v1/admin/stats").json()["error"]["code"] == "admin_only"

    admin = login_as("admin@ya.ru")
    assert admin.delete("/api/v1/admin/whitelist/user@ya.ru").status_code == 204
    assert admin.delete("/api/v1/admin/whitelist/user@ya.ru").status_code == 404
    assert admin.get("/api/v1/admin/whitelist").json() == {"emails": []}


def test_removing_from_whitelist_disables_account_and_readding_enables(login_as, settings):
    admin = login_as("admin@ya.ru")
    admin.post("/api/v1/admin/whitelist", json={"email": "user@ya.ru"})
    user = login_as("user@ya.ru", "User")
    secret = user.post("/api/v1/tokens", json={"name": "agent"}).json()["secret"]
    user_cookie = user.cookies.get("vsid")

    admin = login_as("admin@ya.ru")
    assert admin.delete("/api/v1/admin/whitelist/user@ya.ru").status_code == 204
    conn = connect(settings.db_path)
    assert conn.execute("SELECT disabled FROM users WHERE email = 'user@ya.ru'").fetchone()[0] == 1
    conn.close()
    assert admin.get("/api/v1/me", headers={"Authorization": f"Bearer {secret}"}).status_code == 401
    admin.cookies.set("vsid", user_cookie)
    assert admin.get("/api/v1/me").status_code == 401
    # httpx хранит эту cookie отдельно от той, что выставил Set-Cookie (разные домены в jar:
    # "" у вручную выставленной против "testserver.local" у серверной) — без очистки обе уйдут
    # с следующим запросом и переживут ближайший login_as, ломая его сессию.
    admin.cookies.delete("vsid")

    admin = login_as("admin@ya.ru")
    assert admin.post("/api/v1/admin/whitelist", json={"email": "user@ya.ru"}).status_code == 201
    conn = connect(settings.db_path)
    assert conn.execute("SELECT disabled FROM users WHERE email = 'user@ya.ru'").fetchone()[0] == 0
    conn.close()
    assert login_as("user@ya.ru", "User").get("/api/v1/me").status_code == 200


def test_admin_rejects_invalid_email(login_as):
    admin = login_as("admin@ya.ru")
    r = admin.post("/api/v1/admin/whitelist", json={"email": "not-an-email"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_email"


def test_admin_stats(login_as):
    admin = login_as("admin@ya.ru")
    admin.post("/api/v1/tokens", json={"name": "t"})
    body = admin.get("/api/v1/admin/stats").json()
    assert body["users"] == 1
    assert body["sessions"] == 1
    assert body["tokens"] == 1
    assert 0 <= body["disk_free_pct"] <= 100


def test_admin_routes_need_login_and_admin_role(client):
    assert client.get("/api/v1/admin/stats").status_code == 401
    assert client.get("/api/v1/admin/whitelist").status_code == 401


def test_config_admin_cannot_be_removed_or_locked_out(login_as, settings):
    admin = login_as("admin@ya.ru")
    assert admin.post("/api/v1/admin/whitelist", json={"email": "admin@ya.ru"}).status_code == 201
    r = admin.delete("/api/v1/admin/whitelist/admin@ya.ru")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "cannot_remove_admin"
    assert admin.get("/api/v1/me").status_code == 200
    conn = connect(settings.db_path)
    conn.execute("UPDATE users SET disabled = 1 WHERE email = 'admin@ya.ru'")
    conn.close()
    assert login_as("admin@ya.ru").get("/api/v1/me").status_code == 200
    conn = connect(settings.db_path)
    assert conn.execute("SELECT disabled FROM users WHERE email = 'admin@ya.ru'").fetchone()[0] == 0
    conn.close()


def test_agent_token_cannot_touch_whitelist_but_may_read_stats(login_as):
    admin = login_as("admin@ya.ru")
    secret = admin.post("/api/v1/tokens", json={"name": "agent"}).json()["secret"]
    headers = {"Authorization": f"Bearer {secret}"}
    r = admin.post("/api/v1/admin/whitelist", json={"email": "x@ya.ru"}, headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "cookie_required"
    assert admin.get("/api/v1/admin/whitelist", headers=headers).status_code == 403
    assert admin.delete("/api/v1/admin/whitelist/x@ya.ru", headers=headers).status_code == 403
    assert admin.get("/api/v1/admin/stats", headers=headers).status_code == 200


def _seed_asset(settings, user_id, asset_id="ast_000000000001", size=1234):
    """Готовая запись на диске: проекту нужна ссылка на существующий и обработанный файл."""
    import sqlite3

    from server.app.storage import asset_dir

    folder = asset_dir(settings, user_id, asset_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "source.mp4").write_bytes(b"x")
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
        "created_at, last_access_at) VALUES (?, ?, 'video', 'встреча.mp4', 'mp4', ?, 'ready', 60, "
        "'2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')",
        (asset_id, user_id, size),
    )
    conn.commit()
    conn.close()
    return asset_id


def test_admin_sees_everyones_projects_and_assets(login_as, settings):
    """Диск общий, и следить за тем, чем он занят, кроме админа некому."""
    admin = login_as("admin@ya.ru")
    admin.post("/api/v1/admin/whitelist", json={"email": "user@ya.ru"})

    user = login_as("user@ya.ru", "Пользователь")
    me = user.get("/api/v1/me").json()
    asset = _seed_asset(settings, me["id"])
    project = user.post(
        "/api/v1/projects",
        json={"name": "Планёрка", "doc": {"clips": [{"asset_id": asset, "in": 0, "out": 5}]}},
    ).json()

    admin = login_as("admin@ya.ru")
    projects = admin.get("/api/v1/admin/projects").json()["projects"]
    assert [p["id"] for p in projects] == [project["id"]]
    assert projects[0]["owner_email"] == "user@ya.ru" and projects[0]["owner_name"] == "Пользователь"
    assert projects[0]["clips_count"] == 1 and projects[0]["duration"] == 5.0

    assets = admin.get("/api/v1/admin/assets").json()["assets"]
    assert [a["id"] for a in assets] == [asset]
    assert assets[0]["owner_email"] == "user@ya.ru" and assets[0]["size"] == 1234


def test_these_lists_are_for_the_admin_only(login_as):
    admin = login_as("admin@ya.ru")
    admin.post("/api/v1/admin/whitelist", json={"email": "user@ya.ru"})
    user = login_as("user@ya.ru", "Пользователь")
    assert user.get("/api/v1/admin/projects").status_code == 403
    assert user.get("/api/v1/admin/assets").status_code == 403


def test_admin_sees_who_takes_how_much_disk(login_as, settings):
    """Место по людям — по файлам на диске: исходник, прокси, ролики. Строки базы тут ни при чём:
    у записи в базе 1234 байта, а на диске её исходник занимает один."""
    admin = login_as("admin@ya.ru")
    admin.post("/api/v1/admin/whitelist", json={"email": "user@ya.ru"})
    user = login_as("user@ya.ru", "Пользователь")
    me = user.get("/api/v1/me").json()
    asset = _seed_asset(settings, me["id"])
    (settings.data_dir / me["id"] / "assets" / asset / "proxy.mp4").write_bytes(b"x" * 99)
    renders = settings.data_dir / me["id"] / "projects" / "prj_000000000001" / "renders"
    renders.mkdir(parents=True)
    (renders / "r.mp4").write_bytes(b"x" * 900)

    admin = login_as("admin@ya.ru")
    people = admin.get("/api/v1/admin/usage").json()["people"]
    assert people == [{"email": "user@ya.ru", "name": "Пользователь", "bytes": 1000, "records": 1}]
    assert login_as("user@ya.ru", "Пользователь").get("/api/v1/admin/usage").status_code == 403


def test_admin_opens_and_edits_a_foreign_project(login_as, settings):
    """Открыть чужой проект и починить его — то, ради чего админ вообще видит чужое.

    Работает он при этом от имени владельца: файлы лежат в каталоге владельца, и сохранение
    ищет строку по его id, а не по id вошедшего.
    """
    admin = login_as("admin@ya.ru")
    admin.post("/api/v1/admin/whitelist", json={"email": "user@ya.ru"})
    user = login_as("user@ya.ru", "Пользователь")
    me = user.get("/api/v1/me").json()
    asset = _seed_asset(settings, me["id"])
    project = user.post(
        "/api/v1/projects",
        json={"name": "Планёрка", "doc": {"clips": [{"asset_id": asset, "in": 0, "out": 5}]}},
    ).json()

    admin = login_as("admin@ya.ru")
    assert admin.get(f"/api/v1/projects/{project['id']}").status_code == 200
    # Записи проекта видны админу через сам проект: своих у него нет, а без них редактор
    # показал бы каждый клип необработанным.
    seen = admin.get(f"/api/v1/projects/{project['id']}/assets").json()["assets"]
    assert [a["id"] for a in seen] == [asset]

    saved = admin.put(
        f"/api/v1/projects/{project['id']}",
        json={
            "name": "Планёрка (поправил админ)",
            "version": project["version"],
            "doc": {"clips": [{"asset_id": asset, "in": 1, "out": 4}]},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == project["version"] + 1

    # Правка ушла владельцу, а не завелась вторым проектом у админа.
    user = login_as("user@ya.ru", "Пользователь")
    mine = user.get("/api/v1/projects").json()["projects"]
    assert [p["name"] for p in mine] == ["Планёрка (поправил админ)"]
    assert admin.get("/api/v1/projects").status_code == 200


def test_a_foreign_project_is_still_invisible_to_everyone_else(login_as, settings):
    admin = login_as("admin@ya.ru")
    for email in ("one@ya.ru", "two@ya.ru"):
        admin.post("/api/v1/admin/whitelist", json={"email": email})
    one = login_as("one@ya.ru", "Первый")
    project = one.post("/api/v1/projects", json={"name": "Своё"}).json()

    two = login_as("two@ya.ru", "Второй")
    assert two.get(f"/api/v1/projects/{project['id']}").status_code == 404
    assert two.delete(f"/api/v1/projects/{project['id']}").status_code == 404
    assert two.get(f"/api/v1/projects/{project['id']}/assets").status_code == 404


def test_admin_deletes_a_foreign_project_and_asset(login_as, settings):
    admin = login_as("admin@ya.ru")
    admin.post("/api/v1/admin/whitelist", json={"email": "user@ya.ru"})
    user = login_as("user@ya.ru", "Пользователь")
    me = user.get("/api/v1/me").json()
    asset = _seed_asset(settings, me["id"])
    project = user.post("/api/v1/projects", json={"name": "Планёрка"}).json()

    admin = login_as("admin@ya.ru")
    assert admin.delete(f"/api/v1/projects/{project['id']}").status_code == 204
    assert admin.delete(f"/api/v1/assets/{asset}").status_code == 204
    assert admin.get("/api/v1/admin/projects").json()["projects"] == []
    assert admin.get("/api/v1/admin/assets").json()["assets"] == []

