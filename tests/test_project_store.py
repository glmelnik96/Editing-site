import json

import pytest

from server.app.config import Settings
from server.app.projects.doc import ProjectInvalid
from server.app.projects.store import (
    ProjectConflict,
    ProjectLimit,
    assets_of,
    create_project,
    delete_project,
    finish_project,
    get_project,
    list_projects,
    projects_using_asset,
    save_project,
)
from server.app.storage import asset_dir
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate

USER = "usr_00000000000a"
OTHER = "usr_00000000000b"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data")


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = connect(settings.db_path)
    migrate(c)
    for uid in (USER, OTHER):
        c.execute(
            "INSERT INTO users (id, email, name, created_at) VALUES (?, ?, 'U', ?)",
            (uid, f"{uid}@ya.ru", now_iso()),
        )
    for asset_id, kind, duration in (
        ("ast_000000000001", "video", 120.0),
        ("ast_000000000002", "video", 60.0),
        ("ast_000000000003", "audio", 200.0),
    ):
        c.execute(
            "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
            "created_at, last_access_at) VALUES (?, ?, ?, 'a', 'mp4', 1, 'ready', ?, ?, ?)",
            (asset_id, USER, kind, duration, now_iso(), now_iso()),
        )
        folder = asset_dir(settings, USER, asset_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "source.mp4").write_bytes(b"x")
    yield c
    c.close()


def doc(asset="ast_000000000001", **over) -> dict:
    return {"clips": [{"asset_id": asset, "in": 1.0, "out": 5.0}], **over}


def test_create_returns_a_normalized_project(conn, settings):
    p = create_project(conn, settings, USER, name="Подкаст", raw_doc=doc())
    assert p["id"].startswith("prj_") and p["version"] == 1 and p["status"] == "draft"
    assert p["name"] == "Подкаст"
    assert p["doc"]["clips"][0]["id"] == "c1"
    assert p["doc"]["output"]["aspect"] == "16:9"
    assert p["created_at"] == p["updated_at"] and p["finished_at"] is None


def test_create_without_a_document_starts_empty(conn, settings):
    p = create_project(conn, settings, USER, name="Пустой", raw_doc=None)
    assert p["doc"]["clips"] == [] and p["version"] == 1


def test_name_is_trimmed_and_required(conn, settings):
    p = create_project(conn, settings, USER, name="  Ролик  ", raw_doc=None)
    assert p["name"] == "Ролик"
    with pytest.raises(ProjectInvalid) as e:
        create_project(conn, settings, USER, name="   ", raw_doc=None)
    assert e.value.errors[0]["field"] == "name"


def test_project_count_limit(conn, settings):
    small = Settings(_env_file=None, data_dir=settings.data_dir, max_projects_per_user=2)
    create_project(conn, small, USER, name="1", raw_doc=None)
    create_project(conn, small, USER, name="2", raw_doc=None)
    with pytest.raises(ProjectLimit):
        create_project(conn, small, USER, name="3", raw_doc=None)


def test_get_and_list_are_scoped_to_the_owner(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    assert get_project(conn, USER, p["id"])["name"] == "Мой"
    assert get_project(conn, OTHER, p["id"]) is None
    assert [x["id"] for x in list_projects(conn, USER)] == [p["id"]]
    assert list_projects(conn, OTHER) == []


def test_list_does_not_carry_the_whole_document(conn, settings):
    create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    row = list_projects(conn, USER)[0]
    assert "doc" not in row and row["clips_count"] == 1
    assert row["duration"] == 4.0


def test_save_bumps_the_version_and_normalizes(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    saved = save_project(
        conn, settings, USER, p["id"], name="Другое", raw_doc=doc(output={"fps": 50}), version=1,
    )
    assert saved["version"] == 2 and saved["name"] == "Другое"
    assert saved["doc"]["output"]["fps"] == 50
    assert saved["updated_at"] >= p["updated_at"]


def test_stale_version_conflicts_and_returns_the_current_project(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=doc(), version=1)
    with pytest.raises(ProjectConflict) as e:
        save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=doc(), version=1)
    assert e.value.project["version"] == 2


def test_save_applies_snapping(conn, settings):
    folder = asset_dir(settings, USER, "ast_000000000001")
    (folder / "analysis.json").write_text(
        json.dumps({"silences_dense": [{"start": 4.0, "end": 5.0}]}), encoding="utf-8"
    )
    p = create_project(conn, settings, USER, name="Мой", raw_doc=None)
    raw = {"clips": [{"asset_id": "ast_000000000001", "in": 1.0, "out": 4.1, "snap_to_pauses": True}]}
    saved = save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=raw, version=1)
    clip = saved["doc"]["clips"][0]
    assert clip["out"] == 4.3 and clip["out_verified"] is True
    assert clip["in"] == 1.0 and clip["in_verified"] is False


def test_save_rejects_a_foreign_asset(conn, settings):
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, created_at, "
        "last_access_at) VALUES ('ast_00000000000f', ?, 'video', 'a', 'mp4', 1, 'ready', 9, ?, ?)",
        (OTHER, now_iso(), now_iso()),
    )
    p = create_project(conn, settings, USER, name="Мой", raw_doc=None)
    with pytest.raises(ProjectInvalid) as e:
        save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=doc("ast_00000000000f"), version=1)
    assert e.value.errors[0]["field"] == "clips[0].asset_id"


def test_save_touches_the_assets_it_uses(conn, settings):
    old = "2020-01-01T00:00:00.000Z"
    conn.execute("UPDATE assets SET last_access_at = ? WHERE id = 'ast_000000000001'", (old,))
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    assert conn.execute(
        "SELECT last_access_at FROM assets WHERE id = 'ast_000000000001'"
    ).fetchone()[0] > old
    assert p["id"]


def test_delete_is_scoped_and_returns_whether_it_deleted(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    assert delete_project(conn, OTHER, p["id"]) is False
    assert delete_project(conn, USER, p["id"]) is True
    assert get_project(conn, USER, p["id"]) is None


def test_assets_of_lists_every_referenced_asset(conn, settings):
    raw = {
        "clips": [{"asset_id": "ast_000000000001", "in": 0, "out": 2},
                  {"asset_id": "ast_000000000002", "in": 0, "out": 2}],
        "music": {"asset_id": "ast_000000000003"},
    }
    p = create_project(conn, settings, USER, name="Мой", raw_doc=raw)
    assert assets_of(p["doc"]) == {"ast_000000000001", "ast_000000000002", "ast_000000000003"}


def test_projects_using_asset_ignores_finished_ones(conn, settings):
    a = create_project(conn, settings, USER, name="Живой", raw_doc=doc())
    b = create_project(conn, settings, USER, name="Готовый", raw_doc=doc())
    finish_project(conn, settings, USER, b["id"])
    using = projects_using_asset(conn, USER, "ast_000000000001")
    assert [x["id"] for x in using] == [a["id"]]


def test_finish_deletes_assets_that_nobody_else_needs(conn, settings):
    shared = create_project(conn, settings, USER, name="Общий", raw_doc=doc("ast_000000000001"))
    done = create_project(conn, settings, USER, name="Готовый", raw_doc={
        "clips": [{"asset_id": "ast_000000000001", "in": 0, "out": 2},
                  {"asset_id": "ast_000000000002", "in": 0, "out": 2}],
    })
    result = finish_project(conn, settings, USER, done["id"])
    assert result["status"] == "finished" and result["finished_at"]
    # ast_000000000001 остаётся: он нужен другому незавершённому проекту
    assert conn.execute("SELECT count(*) FROM assets WHERE id = 'ast_000000000001'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM assets WHERE id = 'ast_000000000002'").fetchone()[0] == 0
    assert not asset_dir(settings, USER, "ast_000000000002").exists()
    assert get_project(conn, USER, shared["id"])["doc"]["clips"]  # чужой проект цел


def test_finishing_twice_is_harmless(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    finish_project(conn, settings, USER, p["id"])
    again = finish_project(conn, settings, USER, p["id"])
    assert again["status"] == "finished"


def test_finished_project_cannot_be_saved(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    finish_project(conn, settings, USER, p["id"])
    with pytest.raises(ProjectInvalid) as e:
        save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=doc(), version=p["version"])
    assert e.value.errors[0]["field"] == "status"
