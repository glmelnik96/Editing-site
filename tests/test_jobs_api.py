import sqlite3

from server.app.jobs import enqueue_job
from server.app.util import now_iso


def test_list_requires_auth(client):
    assert client.get("/api/v1/jobs").status_code == 401


def test_list_returns_own_open_jobs(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, "
        "created_at, last_access_at) "
        "VALUES ('ast_jobsapi1', ?, 'video', 'a.mp4', 'mp4', 1, 'proxy_ready', ?, ?)",
        (me["id"], now_iso(), now_iso()),
    )
    job_id = enqueue_job(conn, user_id=me["id"], type_="transcribe", target_id="ast_jobsapi1")
    conn.commit()
    conn.close()
    r = client.get("/api/v1/jobs")
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    row = next(j for j in jobs if j["id"] == job_id)
    assert row["type"] == "transcribe" and row["label"] == "a.mp4"
    assert row["target_id"] == "ast_jobsapi1"
    assert row["cancelable"] is True
    assert "progress" in row


def test_list_hides_other_users_jobs_from_a_plain_user(client, login_as, settings):
    login_as()
    assert client.post("/api/v1/admin/whitelist", json={"email": "u@ya.ru"}).status_code == 201
    login_as("u@ya.ru", "U")
    me = client.get("/api/v1/me").json()
    assert me["role"] == "user"
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES ('usr_stranger01', 'x@y.z', 'X', ?)",
        (now_iso(),),
    )
    stranger = enqueue_job(conn, user_id="usr_stranger01", type_="analyze", target_id="ast_nope")
    mine = enqueue_job(conn, user_id=me["id"], type_="proxy", target_id="ast_mine")
    conn.commit()
    conn.close()
    rows = {j["id"]: j for j in client.get("/api/v1/jobs").json()["jobs"]}
    assert mine in rows and stranger not in rows
    assert rows[mine]["owner_email"] == "u@ya.ru" and rows[mine]["owner_name"] == "U"


def test_admin_sees_the_whole_team_and_who_owns_what(client, login_as, settings):
    """Иначе идущую чужую сборку админ не находил вовсе: шапка спрашивает именно этот список."""
    login_as()
    me = client.get("/api/v1/me").json()
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES ('usr_stranger01', 'x@y.z', 'X', ?)",
        (now_iso(),),
    )
    conn.execute(
        "INSERT INTO projects (id, user_id, name, version, doc, created_at, updated_at) "
        "VALUES ('prj_theirs', 'usr_stranger01', 'Их ролик', 1, '{}', ?, ?)",
        (now_iso(), now_iso()),
    )
    theirs = enqueue_job(conn, user_id="usr_stranger01", type_="render", target_id="prj_theirs")
    mine = enqueue_job(conn, user_id=me["id"], type_="proxy", target_id="ast_mine")
    conn.commit()
    conn.close()
    rows = {j["id"]: j for j in client.get("/api/v1/jobs").json()["jobs"]}
    assert mine in rows and theirs in rows
    assert rows[theirs]["owner_email"] == "x@y.z" and rows[theirs]["owner_name"] == "X"
    assert rows[theirs]["label"] == "Их ролик" and rows[theirs]["cancelable"] is True
    assert rows[mine]["owner_email"] == me["email"]


def test_get_by_id_still_works(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    conn = sqlite3.connect(str(settings.db_path))
    job_id = enqueue_job(conn, user_id=me["id"], type_="proxy", target_id="ast_x")
    conn.commit()
    conn.close()
    r = client.get(f"/api/v1/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == job_id and "label" not in body
