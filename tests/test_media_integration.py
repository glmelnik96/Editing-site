"""Полный путь analyze → proxy на настоящем ffmpeg. Ролики генерируются на лету, идут секунды."""
import json

import pytest

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.storage import asset_dir
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate
from server.media.probe import probe_file
from server.media.run import MediaError
from server.worker import __main__ as worker_main
from tests.media_fixtures import HAVE_FFMPEG, make_audio, make_broken, make_silent_video, make_video

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="нужен ffmpeg в PATH")

USER = "usr_00000000000a"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", worker_poll_sec=0.1)


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


def add_asset(conn, settings, source_maker, *, asset_id, kind, ext):
    folder = asset_dir(settings, USER, asset_id)
    folder.mkdir(parents=True, exist_ok=True)
    source_maker(folder / f"source.{ext}")
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, created_at, "
        "last_access_at) "
        "VALUES (?, ?, ?, ?, ?, 1, 'uploaded', ?, ?)",
        (asset_id, USER, kind, f"a.{ext}", ext, now_iso(), now_iso()),
    )
    enqueue_job(conn, user_id=USER, type_="analyze", target_id=asset_id, priority=10)
    return folder


def drain(conn, settings, limit=4):
    """Прокрутить очередь: analyze ставит proxy, поэтому кругов больше одного."""
    for _ in range(limit):
        if not worker_main.run_once(conn, settings, "cpu"):
            return


def test_video_goes_all_the_way_to_proxy(conn, settings):
    folder = add_asset(conn, settings, make_video, asset_id="ast_000000000001", kind="video", ext="mp4")
    drain(conn, settings)

    row = conn.execute("SELECT * FROM assets WHERE id = 'ast_000000000001'").fetchone()
    assert row["status"] == "proxy_ready"
    assert row["duration"] == pytest.approx(6.0, abs=0.3)
    assert (row["width"], row["height"]) == (960, 540)
    assert row["fps"] == pytest.approx(25.0, abs=0.1)
    assert row["has_audio"] == 1 and row["video_codec"] == "h264"

    peaks = json.loads((folder / "peaks.json").read_text(encoding="utf-8"))
    assert peaks["rate"] == 50
    assert len(peaks["peaks"]) == pytest.approx(300, abs=20)
    assert max(peaks["peaks"]) > 10  # звук слышен: тон ffmpeg sine пиковый лишь около -18 дБ
    assert min(peaks["peaks"]) == 0  # тишина в середине

    analysis = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["speech_level_db"] is not None and analysis["threshold_db"] < 0
    pause = next(s for s in analysis["silences"] if s["end"] - s["start"] > 1)
    assert pause["start"] == pytest.approx(2.0, abs=0.3)
    assert pause["end"] == pytest.approx(4.0, abs=0.3)
    assert analysis["silences_dense"]

    meta = json.loads((folder / "thumbs.json").read_text(encoding="utf-8"))
    assert meta["count"] == 3 and meta["cols"] == 10 and meta["rows"] == 1
    sprite = probe_file(settings, str(folder / "thumbs.jpg"))
    assert sprite.width == meta["cols"] * meta["width"]
    assert sprite.height == meta["rows"] * meta["height"]

    proxy = probe_file(settings, str(folder / "proxy.mp4"))
    assert max(proxy.width, proxy.height) == settings.proxy_long_side
    assert proxy.has_audio is True
    assert proxy.duration == pytest.approx(6.0, abs=0.4)
    assert not list(folder.glob("*.part")) and not (folder / "audio16k.wav").exists()

    done = conn.execute("SELECT type, status FROM jobs ORDER BY created_at").fetchall()
    assert [(r["type"], r["status"]) for r in done] == [("analyze", "done"), ("proxy", "done")]


def test_silent_video_still_becomes_ready(conn, settings):
    folder = add_asset(
        conn, settings, make_silent_video, asset_id="ast_000000000002", kind="video", ext="mp4"
    )
    drain(conn, settings)
    row = conn.execute("SELECT status, has_audio FROM assets WHERE id = 'ast_000000000002'").fetchone()
    assert row["status"] == "proxy_ready" and row["has_audio"] == 0
    assert json.loads((folder / "peaks.json").read_text(encoding="utf-8"))["peaks"] == []
    assert (folder / "thumbs.jpg").exists()
    assert (folder / "proxy.mp4").exists()


def test_audio_only_asset(conn, settings):
    folder = add_asset(conn, settings, make_audio, asset_id="ast_000000000003", kind="audio", ext="m4a")
    drain(conn, settings)
    row = conn.execute("SELECT status, kind, width FROM assets WHERE id = 'ast_000000000003'").fetchone()
    assert row["status"] == "proxy_ready" and row["kind"] == "audio" and row["width"] is None
    assert (folder / "proxy.m4a").exists() and not (folder / "thumbs.jpg").exists()
    assert json.loads((folder / "peaks.json").read_text(encoding="utf-8"))["peaks"]


def test_broken_file_fails_with_a_readable_reason(conn, settings):
    add_asset(conn, settings, make_broken, asset_id="ast_000000000004", kind="video", ext="mp4")
    drain(conn, settings)
    row = conn.execute("SELECT status, error FROM assets WHERE id = 'ast_000000000004'").fetchone()
    assert row["status"] == "failed" and row["error"]
    job = conn.execute("SELECT status, error FROM jobs WHERE type = 'analyze'").fetchone()
    assert job["status"] == "failed" and job["error"]
    assert conn.execute("SELECT count(*) FROM jobs WHERE type = 'proxy'").fetchone()[0] == 0


def test_proxy_of_a_deleted_asset_does_nothing(conn, settings):
    add_asset(conn, settings, make_video, asset_id="ast_000000000005", kind="video", ext="mp4")
    worker_main.run_once(conn, settings, "cpu")  # analyze
    conn.execute("DELETE FROM assets WHERE id = 'ast_000000000005'")
    assert worker_main.run_once(conn, settings, "cpu") is True  # proxy взято и мирно завершилось
    assert conn.execute("SELECT status FROM jobs WHERE type = 'proxy'").fetchone()[0] == "done"


def test_probe_reports_a_broken_file(settings, tmp_path):
    with pytest.raises(MediaError):
        probe_file(settings, str(make_broken(tmp_path / "broken.mp4")))
