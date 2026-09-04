import os
import sqlite3
from pathlib import Path

import pytest

from server.app.config import Settings
from server.app.storage import asset_dir
from server.app.uploads import store
from server.app.uploads.store import (
    ChunkWriter,
    UploadError,
    chunk_length,
    complete_upload,
    create_upload,
    delete_upload,
    finalize_file,
    get_upload,
    mark_chunk,
    received_chunks,
    total_chunks,
    used_bytes,
)
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate

USER = "usr_000000000001"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        chunk_size=1024,
        user_quota_bytes=10_000,
        max_upload_bytes=8_000,
    )


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = connect(settings.db_path)
    migrate(c)
    c.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)",
        (USER, now_iso()),
    )
    yield c
    c.close()


def test_chunk_arithmetic():
    up = {"size": 2500, "chunk_size": 1024}
    assert total_chunks(up) == 3
    assert chunk_length(up, 0) == 1024 and chunk_length(up, 2) == 452
    assert total_chunks({"size": 1024, "chunk_size": 1024}) == 1


def test_chunk_length_last_chunk_when_size_is_multiple():
    up = {"size": 2048, "chunk_size": 1024}
    assert total_chunks(up) == 2
    assert chunk_length(up, 1) == 1024
    with pytest.raises(UploadError) as e:
        chunk_length(up, 2)
    assert e.value.code == "no_such_chunk" and e.value.details == {"total": 2}
    with pytest.raises(UploadError):
        chunk_length(up, -1)


def test_create_reserves_file_and_counts_quota(conn, settings):
    up = create_upload(conn, settings, USER, filename="Clip.MOV", size=2500, kind=None)
    assert up["id"].startswith("upl_") and up["kind"] == "video" and up["chunk_size"] == 1024
    assert Path(up["path"]).stat().st_size == 2500
    assert up["expires_at"] > up["created_at"]
    assert used_bytes(conn, USER) == 2500
    assert get_upload(conn, USER, up["id"])["filename"] == "Clip.MOV"
    assert get_upload(conn, "usr_000000000002", up["id"]) is None


def test_create_rejects_bad_input(conn, settings):
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="a.mp4", size=0, kind=None)
    assert e.value.code == "empty_file"
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="a.mp4", size=9_000, kind=None)
    assert e.value.code == "too_large"
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="a.mp4", size=100, kind="image")
    assert e.value.code == "bad_kind"
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="   ", size=100, kind=None)
    assert e.value.code == "bad_filename"


def test_quota_counts_assets_and_pending_uploads(conn, settings):
    create_upload(conn, settings, USER, filename="a.mp4", size=6_000, kind=None)
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="b.mp4", size=5_000, kind=None)
    assert e.value.code == "quota_exceeded"
    assert e.value.details == {"used_bytes": 6_000, "limit_bytes": 10_000}


def test_disk_low_blocks_new_uploads(conn, settings, monkeypatch):
    monkeypatch.setattr(store, "disk_free_pct_safe", lambda _path: 5.0)
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="a.mp4", size=100, kind=None)
    assert e.value.code == "disk_low"


def test_reserve_failure_leaves_no_file_and_no_row(conn, settings, monkeypatch):
    def boom(path, size):
        path.write_bytes(b"")  # файл уже создан, как при настоящем ENOSPC после O_CREAT
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "reserve_file", boom)
    with pytest.raises(UploadError) as e:
        create_upload(conn, settings, USER, filename="a.mp4", size=100, kind=None)
    assert e.value.code == "disk_low"
    assert list(settings.uploads_tmp_path.glob("upl_*")) == []
    assert conn.execute("SELECT count(*) FROM uploads").fetchone()[0] == 0
    assert not conn.in_transaction


def test_chunk_writer_writes_at_offset_and_guards_length(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"\0" * 10)
    w = ChunkWriter(path, offset=4, expected=3)
    w.write(b"ab")
    assert not w.done()
    w.write(b"c")
    assert w.done()
    w.close()
    assert path.read_bytes() == b"\0\0\0\0abc\0\0\0"
    w = ChunkWriter(path, offset=0, expected=2)
    with pytest.raises(UploadError) as e:
        w.write(b"xyz")
    w.close()
    assert e.value.code == "chunk_size_mismatch"


def test_complete_requires_all_chunks_and_exact_size(conn, settings):
    up = create_upload(conn, settings, USER, filename="a.mp4", size=2500, kind=None)
    mark_chunk(conn, up["id"], 0)
    mark_chunk(conn, up["id"], 0)  # повтор части допустим
    mark_chunk(conn, up["id"], 2)
    assert received_chunks(conn, up["id"]) == [0, 2]
    with pytest.raises(UploadError) as e:
        complete_upload(conn, settings, up)
    assert e.value.code == "incomplete" and e.value.details == {"missing": [1], "total": 3}
    mark_chunk(conn, up["id"], 1)
    os.truncate(up["path"], 2400)
    with pytest.raises(UploadError) as e:
        complete_upload(conn, settings, up)
    assert e.value.code == "size_mismatch"


def test_complete_moves_file_creates_asset_and_job(conn, settings):
    up = create_upload(conn, settings, USER, filename="a.mp4", size=2048, kind=None)
    Path(up["path"]).write_bytes(b"x" * 2048)
    for i in range(2):
        mark_chunk(conn, up["id"], i)
    asset = complete_upload(conn, settings, up)
    assert asset["id"].startswith("ast_") and asset["status"] == "uploaded" and asset["ext"] == "mp4"
    src = asset_dir(settings, USER, asset["id"]) / "source.mp4"
    assert src.read_bytes() == b"x" * 2048
    assert not Path(up["path"]).exists()
    assert conn.execute("SELECT count(*) FROM uploads").fetchone()[0] == 0
    job = conn.execute("SELECT type, status, priority, target_id FROM jobs").fetchone()
    assert tuple(job) == ("analyze", "queued", 10, asset["id"])
    assert used_bytes(conn, USER) == 2048


def test_subtitle_is_ready_without_job(conn, settings, tmp_path):
    src = tmp_path / "s.srt"
    src.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    asset = finalize_file(
        conn, settings, user_id=USER, src=src, filename="s.srt", size=src.stat().st_size, kind="subtitle"
    )
    assert asset["status"] == "ready"
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_finalize_restores_file_when_db_insert_fails(conn, settings, tmp_path):
    src = tmp_path / "f.mp4"
    src.write_bytes(b"abc")
    with pytest.raises(sqlite3.IntegrityError):
        finalize_file(
            conn, settings, user_id="usr_0000000000ff", src=src, filename="f.mp4", size=3, kind="video"
        )
    assert src.read_bytes() == b"abc"
    assert list((settings.data_dir / "usr_0000000000ff" / "assets").glob("ast_*")) == []


def test_delete_upload_removes_record_and_file(conn, settings):
    up = create_upload(conn, settings, USER, filename="a.mp4", size=100, kind=None)
    delete_upload(conn, up)
    assert not Path(up["path"]).exists()
    assert get_upload(conn, USER, up["id"]) is None
