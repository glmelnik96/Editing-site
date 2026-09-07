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
    assert row["cancelable"] is True
    assert "progress" in row


def test_list_hides_other_users_jobs(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES ('usr_stranger01', 'x@y.z', 'X', ?)",
        (now_iso(),),
    )
    enqueue_job(conn, user_id="usr_stranger01", type_="analyze", target_id="ast_nope")
    conn.commit()
    conn.close()
    ids = [j["id"] for j in client.get("/api/v1/jobs").json()["jobs"]]
    assert all(j.startswith("job_") for j in ids)
    conn = sqlite3.connect(str(settings.db_path))
    stranger = conn.execute("SELECT id FROM jobs WHERE user_id = 'usr_stranger01'").fetchone()[0]
    conn.close()
    assert stranger not in ids
    assert me["id"]


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
