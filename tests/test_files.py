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


def _render_on_disk(client, settings, user_id, name="Отпуск \"2026\"/май"):
    """Проект с готовым роликом на диске: строка в renders плюс сам файл."""
    project_id = client.post("/api/v1/projects", json={"name": name}).json()["id"]
    render_id = "rnd_000000000001"
    folder = settings.data_dir / user_id / "projects" / project_id / "renders"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{render_id}.mp4"
    path.write_bytes(b"\0" * 32)
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO renders (id, project_id, user_id, job_id, quality, path, size, duration, "
        "created_at, expires_at) VALUES (?, ?, ?, 'job_1', 'draft', ?, 32, 5, ?, ?)",
        (render_id, project_id, user_id, str(path), OLD, "2099-01-01T00:00:00.000Z"),
    )
    conn.commit()
    conn.close()
    return project_id, render_id


def test_render_is_served_as_a_download(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project_id, render_id = _render_on_disk(client, settings, me["id"])
    url = f"/files/{me['id']}/projects/{project_id}/renders/{render_id}.mp4"
    r = client.get(url)
    assert r.status_code == 200 and r.content == b"\0" * 32
    disposition = r.headers["content-disposition"]
    # Кириллица уезжает в filename* (RFC 5987), кавычки и слэш из названия вырезаны.
    assert disposition.startswith(f'attachment; filename="{render_id}.mp4"; filename*=UTF-8\'\'')
    assert "%D0%9E%D1%82%D0%BF%D1%83%D1%81%D0%BA" in disposition
    assert '"' not in disposition.split("filename*=")[1] and "/" not in disposition.split("''")[1]
    assert client.get("/internal/authz", headers={"X-Forwarded-Uri": url}).status_code == 204


def test_render_download_falls_back_to_the_id(client, login_as, settings):
    """Название из одних кавычек и слэшей не должно дать пустое имя файла."""
    login_as()
    me = client.get("/api/v1/me").json()
    project_id, render_id = _render_on_disk(client, settings, me["id"], name='"//"')
    r = client.get(f"/files/{me['id']}/projects/{project_id}/renders/{render_id}.mp4")
    assert r.headers["content-disposition"].endswith(f"filename*=UTF-8''{render_id}.mp4")


def test_render_missing_on_disk_is_404(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    project_id, render_id = _render_on_disk(client, settings, me["id"])
    (settings.data_dir / me["id"] / "projects" / project_id / "renders" / f"{render_id}.mp4").unlink()
    assert client.get(f"/files/{me['id']}/projects/{project_id}/renders/{render_id}.mp4").status_code == 404


def test_foreign_render_is_404(client, login_as, settings):
    login_as()
    owner = client.get("/api/v1/me").json()
    project_id, render_id = _render_on_disk(client, settings, owner["id"])
    good = f"/files/{owner['id']}/projects/{project_id}/renders/{render_id}.mp4"
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    thief = client.get("/api/v1/me").json()
    for url in (good, f"/files/{thief['id']}/projects/{project_id}/renders/{render_id}.mp4"):
        assert client.get(url).status_code == 404, url
        assert client.get("/internal/authz", headers={"X-Forwarded-Uri": url}).status_code == 404, url


def test_render_url_shapes_that_are_not_ours(client, login_as, settings):
    """Всё, что не {id}.mp4 в каталоге рендеров, до проверки прав не доходит."""
    login_as()
    me = client.get("/api/v1/me").json()
    project_id, render_id = _render_on_disk(client, settings, me["id"])
    base = f"/files/{me['id']}/projects/{project_id}/renders"
    for name in (f"{render_id}.mp4.part", "evil.exe", "source.mp4", f"{render_id}.mp4x"):
        url = f"{base}/{name}"
        assert client.get(url).status_code == 404, name
        assert client.get("/internal/authz", headers={"X-Forwarded-Uri": url}).status_code == 404, name
