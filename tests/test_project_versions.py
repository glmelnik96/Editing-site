import pytest

from server.app.config import Settings
from server.app.projects.doc import ProjectInvalid
from server.app.projects.store import (
    create_checkpoint,
    create_project,
    finish_project,
    get_project,
    list_versions,
    restore_version,
    save_project,
)
from server.app.storage import asset_dir
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate

USER = "usr_00000000000a"
ASSET = "ast_000000000001"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", versions_kept=3)


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = connect(settings.db_path)
    migrate(c)
    c.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)", (USER, now_iso())
    )
    c.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
        "created_at, last_access_at) VALUES (?, ?, 'video', 'a', 'mp4', 1, 'ready', 120, ?, ?)",
        (ASSET, USER, now_iso(), now_iso()),
    )
    folder = asset_dir(settings, USER, ASSET)
    folder.mkdir(parents=True, exist_ok=True)
    yield c
    c.close()


def doc(out=5.0):
    return {"clips": [{"asset_id": ASSET, "in": 1.0, "out": out}]}


def test_checkpoint_snapshots_the_current_state(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    made = create_checkpoint(conn, settings, USER, p["id"], label="до перестановки")
    assert made["version"] == p["version"] and made["label"] == "до перестановки"
    versions = list_versions(conn, USER, p["id"])
    assert len(versions) == 1
    assert versions[0]["clips_count"] == 1 and versions[0]["duration"] == 4.0
    assert versions[0]["name"] == "Мой"


def test_empty_label_is_allowed(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    made = create_checkpoint(conn, settings, USER, p["id"], label="")
    assert made["label"] == ""


def test_too_long_label_is_rejected(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    with pytest.raises(ProjectInvalid) as e:
        create_checkpoint(conn, settings, USER, p["id"], label="я" * 201)
    assert e.value.errors[0]["field"] == "label"


def test_pool_keeps_only_the_newest(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    for step in range(5):
        saved = save_project(
            conn, settings, USER, p["id"], name="Мой", raw_doc=doc(5 + step), version=p["version"] + step
        )
        create_checkpoint(conn, settings, USER, p["id"], label=f"точка {step}")
        assert saved["version"] == p["version"] + step + 1
    versions = list_versions(conn, USER, p["id"])
    assert [v["label"] for v in versions] == ["точка 4", "точка 3", "точка 2"]  # versions_kept = 3


def test_restore_puts_the_snapshot_back_as_a_new_save(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc(5.0))
    point = create_checkpoint(conn, settings, USER, p["id"], label="хорошая")
    save_project(conn, settings, USER, p["id"], name="Испорчено", raw_doc=doc(9.0), version=1)
    restored = restore_version(conn, settings, USER, p["id"], point["id"])
    assert restored["version"] == 3  # 1 создание, 2 порча, 3 возврат
    assert restored["doc"]["clips"][0]["out"] == 5.0
    assert restored["name"] == "Мой"
    assert get_project(conn, USER, p["id"])["doc"]["clips"][0]["out"] == 5.0


def test_restore_keeps_the_pool_intact(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    point = create_checkpoint(conn, settings, USER, p["id"], label="первая")
    restore_version(conn, settings, USER, p["id"], point["id"])
    assert [v["label"] for v in list_versions(conn, USER, p["id"])] == ["первая"]


def test_versions_are_scoped_to_the_owner(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    point = create_checkpoint(conn, settings, USER, p["id"], label="моя")
    assert list_versions(conn, "usr_00000000000b", p["id"]) == []
    with pytest.raises(KeyError):
        restore_version(conn, settings, "usr_00000000000b", p["id"], point["id"])


def test_restore_of_a_missing_point_is_an_error(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    with pytest.raises(KeyError):
        restore_version(conn, settings, USER, p["id"], "pvr_00000000dead")


def test_finished_project_takes_no_checkpoints(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    finish_project(conn, settings, USER, p["id"])
    with pytest.raises(ProjectInvalid) as e:
        create_checkpoint(conn, settings, USER, p["id"], label="поздно")
    assert e.value.errors[0]["field"] == "status"


def test_deleting_a_project_takes_its_versions(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    create_checkpoint(conn, settings, USER, p["id"], label="точка")
    conn.execute("DELETE FROM projects WHERE id = ?", (p["id"],))
    assert conn.execute("SELECT count(*) FROM project_versions").fetchone()[0] == 0
