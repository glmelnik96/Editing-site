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
