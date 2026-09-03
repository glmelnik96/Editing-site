def test_create_list_use_and_revoke_token(login_as):
    c = login_as()
    r = c.post("/api/v1/tokens", json={"name": "agent"})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["secret"].startswith("vt_")
    assert created["name"] == "agent"
    assert created["expires_at"] is None

    listed = c.get("/api/v1/tokens").json()["tokens"]
    assert [t["id"] for t in listed] == [created["id"]]
    assert "secret" not in listed[0]

    headers = {"Authorization": f"Bearer {created['secret']}"}
    me = c.get("/api/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["auth"] == "token"

    assert c.delete(f"/api/v1/tokens/{created['id']}").status_code == 204
    assert c.get("/api/v1/me", headers=headers).status_code == 401
    assert c.delete(f"/api/v1/tokens/{created['id']}").status_code == 404
    assert c.get("/api/v1/tokens").json()["tokens"] == []


def test_token_cannot_manage_tokens(login_as):
    c = login_as()
    secret = c.post("/api/v1/tokens", json={"name": "agent"}).json()["secret"]
    headers = {"Authorization": f"Bearer {secret}"}
    r = c.post("/api/v1/tokens", json={"name": "child"}, headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "cookie_required"
    assert c.get("/api/v1/tokens", headers=headers).status_code == 403


def test_invalid_bearer_is_401(client):
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer vt_nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_token"


def test_token_validation_errors(login_as):
    c = login_as()
    assert c.post("/api/v1/tokens", json={"name": ""}).status_code == 422
    assert c.post("/api/v1/tokens", json={"name": "x", "expires_in_days": 0}).status_code == 422
    assert c.post("/api/v1/tokens", json={"name": "x", "expires_in_days": 5000}).status_code == 422
    r = c.post("/api/v1/tokens", json={"name": "x", "expires_in_days": 30})
    assert r.status_code == 201
    assert r.json()["expires_at"] is not None


def test_cross_site_post_with_cookie_is_rejected(login_as):
    c = login_as()
    r = c.post("/api/v1/tokens", json={"name": "t"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "cross_site"
    r = c.post("/api/v1/tokens", json={"name": "t"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 201


def test_tokens_require_login(client):
    assert client.get("/api/v1/tokens").status_code == 401
    assert client.post("/api/v1/tokens", json={"name": "t"}).status_code == 401


def test_cannot_revoke_another_users_token(login_as, settings):
    from server.db.core import connect

    admin = login_as("admin@ya.ru")
    token_id = admin.post("/api/v1/tokens", json={"name": "a"}).json()["id"]
    conn = connect(settings.db_path)
    conn.execute("INSERT INTO whitelist (email, added_by, added_at) VALUES ('user@ya.ru', NULL, 'x')")
    conn.close()
    user = login_as("user@ya.ru", "User")
    assert user.delete(f"/api/v1/tokens/{token_id}").status_code == 404
    assert user.get("/api/v1/tokens").json()["tokens"] == []


def test_token_name_is_trimmed_and_blank_rejected(login_as):
    c = login_as()
    assert c.post("/api/v1/tokens", json={"name": "   "}).status_code == 422
    r = c.post("/api/v1/tokens", json={"name": "  padded  "})
    assert r.status_code == 201
    assert r.json()["name"] == "padded"


def test_cross_site_delete_with_cookie_is_rejected(login_as):
    c = login_as()
    token_id = c.post("/api/v1/tokens", json={"name": "t"}).json()["id"]
    r = c.delete(f"/api/v1/tokens/{token_id}", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "cross_site"
    assert [t["id"] for t in c.get("/api/v1/tokens").json()["tokens"]] == [token_id]


def test_active_token_cap(login_as):
    from server.app.auth.tokens import MAX_ACTIVE_TOKENS

    c = login_as()
    ids = [c.post("/api/v1/tokens", json={"name": f"t{i}"}).json()["id"] for i in range(MAX_ACTIVE_TOKENS)]
    r = c.post("/api/v1/tokens", json={"name": "one-too-many"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "too_many_tokens"
    assert c.delete(f"/api/v1/tokens/{ids[0]}").status_code == 204
    assert c.post("/api/v1/tokens", json={"name": "fits-again"}).status_code == 201
