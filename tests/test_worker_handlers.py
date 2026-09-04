import json
from pathlib import Path

import pytest

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.storage import asset_dir
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate
from server.media.probe import MediaInfo
from server.media.run import MediaError
from server.worker import handlers
from server.worker.queue import claim_job

USER = "usr_00000000000a"
VIDEO = MediaInfo(
    duration=12.0, width=640, height=360, fps=25.0, has_audio=True,
    video_codec="h264", audio_codec="aac",
)
AUDIO = MediaInfo(
    duration=8.0, width=None, height=None, fps=None, has_audio=True,
    video_codec=None, audio_codec="mp3",
)
SILENT = MediaInfo(
    duration=5.0, width=320, height=240, fps=30.0, has_audio=False,
    video_codec="h264", audio_codec=None,
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data")


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


def make_asset(conn, settings, asset_id="ast_000000000001", kind="video", status="uploaded", ext="mp4"):
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, created_at, "
        "last_access_at) "
        "VALUES (?, ?, ?, 'a.mp4', ?, 10, ?, ?, ?)",
        (asset_id, USER, kind, ext, status, now_iso(), now_iso()),
    )
    d = asset_dir(settings, USER, asset_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"source.{ext}").write_bytes(b"\0" * 10)
    return asset_id


def take(conn, user_id=USER, type_="analyze", target_id="ast_000000000001", priority=10):
    enqueue_job(conn, user_id=user_id, type_=type_, target_id=target_id, priority=priority)
    return claim_job(conn, lane="cpu", pid=1)


def fake_analysis():
    return {
        "peaks": {"rate": 50, "peaks": [1, 2, 3]},
        "analysis": {"duration": 12.0, "speech_level_db": -20.0, "threshold_db": -36.0,
                     "silences": [{"start": 3.0, "end": 6.0}], "silences_dense": []},
    }


def test_analyze_fills_metadata_writes_files_and_queues_proxy(conn, settings, monkeypatch, tmp_path):
    asset_id = make_asset(conn, settings)
    monkeypatch.setattr(handlers, "probe_file", lambda *a, **k: VIDEO)
    monkeypatch.setattr(handlers, "extract_wav", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "analyze_audio", lambda *a, **k: fake_analysis())
    meta = {"count": 6, "cols": 10, "rows": 1, "interval": 2.0, "width": 160, "height": 90}
    monkeypatch.setattr(handlers, "build_thumbs", lambda *a, **k: meta)
    job = take(conn)
    handlers.handle_analyze(conn, settings, job)

    row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    assert row["status"] == "ready" and row["duration"] == 12.0 and row["fps"] == 25.0
    assert row["width"] == 640 and row["height"] == 360 and row["has_audio"] == 1
    assert row["video_codec"] == "h264" and row["audio_codec"] == "aac" and row["error"] is None
    d = asset_dir(settings, USER, asset_id)
    assert json.loads((d / "peaks.json").read_text(encoding="utf-8"))["rate"] == 50
    assert json.loads((d / "analysis.json").read_text(encoding="utf-8"))["threshold_db"] == -36.0
    assert json.loads((d / "thumbs.json").read_text(encoding="utf-8"))["count"] == 6
    assert not (d / "audio16k.wav").exists()  # временный звук убирается за собой
    queued = conn.execute("SELECT type, priority, target_id FROM jobs WHERE status = 'queued'").fetchone()
    assert tuple(queued) == ("proxy", handlers.PROXY_PRIORITY, asset_id)


def test_analyze_of_audio_skips_thumbnails(conn, settings, monkeypatch):
    asset_id = make_asset(conn, settings, kind="audio", ext="mp3")
    monkeypatch.setattr(handlers, "probe_file", lambda *a, **k: AUDIO)
    monkeypatch.setattr(handlers, "extract_wav", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "analyze_audio", lambda *a, **k: fake_analysis())
    monkeypatch.setattr(handlers, "build_thumbs", lambda *a, **k: pytest.fail("для звука полоска не нужна"))
    handlers.handle_analyze(conn, settings, take(conn))
    assert not (asset_dir(settings, USER, asset_id) / "thumbs.jpg").exists()
    assert conn.execute("SELECT status FROM assets WHERE id = ?", (asset_id,)).fetchone()[0] == "ready"


def test_analyze_of_silent_video_writes_empty_maps(conn, settings, monkeypatch):
    asset_id = make_asset(conn, settings)
    monkeypatch.setattr(handlers, "probe_file", lambda *a, **k: SILENT)
    monkeypatch.setattr(handlers, "extract_wav", lambda *a, **k: pytest.fail("звука нет, извлекать нечего"))
    monkeypatch.setattr(handlers, "build_thumbs", lambda *a, **k: {"count": 3})
    handlers.handle_analyze(conn, settings, take(conn))
    d = asset_dir(settings, USER, asset_id)
    assert json.loads((d / "peaks.json").read_text(encoding="utf-8"))["peaks"] == []
    assert json.loads((d / "analysis.json").read_text(encoding="utf-8"))["silences"] == []
    assert conn.execute("SELECT has_audio, status FROM assets WHERE id = ?", (asset_id,)).fetchone()[0] == 0


def test_analyze_corrects_the_kind_guessed_from_the_extension(conn, settings, monkeypatch):
    """Файл назвали .mp4, а внутри только звук: вид ассета берётся из содержимого."""
    asset_id = make_asset(conn, settings, kind="video")
    monkeypatch.setattr(handlers, "probe_file", lambda *a, **k: AUDIO)
    monkeypatch.setattr(handlers, "extract_wav", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "analyze_audio", lambda *a, **k: fake_analysis())
    handlers.handle_analyze(conn, settings, take(conn))
    assert conn.execute("SELECT kind FROM assets WHERE id = ?", (asset_id,)).fetchone()[0] == "audio"


def test_analyze_marks_the_asset_failed_on_a_broken_file(conn, settings, monkeypatch):
    asset_id = make_asset(conn, settings)

    def boom(*a, **k):
        raise MediaError("no_streams", "В файле нет ни видео, ни звука")

    monkeypatch.setattr(handlers, "probe_file", boom)
    with pytest.raises(MediaError):
        handlers.handle_analyze(conn, settings, take(conn))
    row = conn.execute("SELECT status, error FROM assets WHERE id = ?", (asset_id,)).fetchone()
    assert row["status"] == "failed" and "нет ни видео" in row["error"]
    assert conn.execute("SELECT count(*) FROM jobs WHERE status = 'queued'").fetchone()[0] == 0


def test_repeated_analyze_does_not_duplicate_the_proxy_job(conn, settings, monkeypatch):
    """analyze может выполниться повторно (janitor вернул задание в очередь) — proxy должен остаться один."""
    asset_id = make_asset(conn, settings)
    monkeypatch.setattr(handlers, "probe_file", lambda *a, **k: VIDEO)
    monkeypatch.setattr(handlers, "extract_wav", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "analyze_audio", lambda *a, **k: fake_analysis())
    meta = {"count": 6, "cols": 10, "rows": 1, "interval": 2.0, "width": 160, "height": 90}
    monkeypatch.setattr(handlers, "build_thumbs", lambda *a, **k: meta)
    handlers.handle_analyze(conn, settings, take(conn))
    handlers.handle_analyze(conn, settings, take(conn))  # второй прогон того же analyze
    assert conn.execute(
        "SELECT count(*) FROM jobs WHERE type = 'proxy' AND target_id = ?", (asset_id,)
    ).fetchone()[0] == 1


def test_analyze_of_a_missing_asset_is_not_an_error(conn, settings):
    """Ассет удалили, пока задание ждало очереди: работать не над чем, но и падать незачем."""
    job = take(conn, target_id="ast_00000000dead")
    handlers.handle_analyze(conn, settings, job)


def test_proxy_encodes_and_moves_status_forward(conn, settings, monkeypatch):
    asset_id = make_asset(conn, settings, status="ready")
    conn.execute("UPDATE assets SET duration = 12.0 WHERE id = ?", (asset_id,))
    seen = {}

    def fake_stream(args, *, timeout, on_line, should_stop=None, stop_check_sec=2.0):
        seen["dst"] = args[-1]
        on_line("out_time_us=6000000")
        on_line("progress=continue")
        with open(args[-1], "wb") as f:
            f.write(b"proxy")

    monkeypatch.setattr(handlers, "run_streaming", fake_stream)
    job = take(conn, type_="proxy", priority=5)
    handlers.handle_proxy(conn, settings, job)
    d = asset_dir(settings, USER, asset_id)
    assert (d / "proxy.mp4").read_bytes() == b"proxy"
    assert not list(d.glob("*.part"))  # временный файл переименован
    assert seen["dst"].endswith(".part")
    assert conn.execute("SELECT status FROM assets WHERE id = ?", (asset_id,)).fetchone()[0] == "proxy_ready"
    assert conn.execute("SELECT progress FROM jobs WHERE id = ?", (job["id"],)).fetchone()[0] == 0.5


def test_proxy_of_audio_makes_m4a(conn, settings, monkeypatch):
    asset_id = make_asset(conn, settings, kind="audio", status="ready", ext="mp3")
    conn.execute("UPDATE assets SET duration = 8.0 WHERE id = ?", (asset_id,))
    def write_stub(args, **kwargs):
        Path(args[-1]).write_bytes(b"a")

    monkeypatch.setattr(handlers, "run_streaming", write_stub)
    handlers.handle_proxy(conn, settings, take(conn, type_="proxy", priority=5))
    assert (asset_dir(settings, USER, asset_id) / "proxy.m4a").exists()


def test_proxy_leaves_no_partial_file_when_ffmpeg_fails(conn, settings, monkeypatch):
    asset_id = make_asset(conn, settings, status="ready")
    conn.execute("UPDATE assets SET duration = 12.0 WHERE id = ?", (asset_id,))

    def boom(args, **kwargs):
        with open(args[-1], "wb") as f:
            f.write(b"half")
        raise MediaError("tool_failed", "ffmpeg упал", "x264 error")

    monkeypatch.setattr(handlers, "run_streaming", boom)
    with pytest.raises(MediaError):
        handlers.handle_proxy(conn, settings, take(conn, type_="proxy", priority=5))
    d = asset_dir(settings, USER, asset_id)
    assert not (d / "proxy.mp4").exists() and not list(d.glob("*.part"))
    # ассет остаётся годным для монтажа: прокси нужен только плееру
    assert conn.execute("SELECT status FROM assets WHERE id = ?", (asset_id,)).fetchone()[0] == "ready"


def test_proxy_skips_an_asset_that_is_not_ready(conn, settings, monkeypatch):
    make_asset(conn, settings, status="uploaded")
    monkeypatch.setattr(handlers, "run_streaming", lambda *a, **k: pytest.fail("кодировать нечего"))
    handlers.handle_proxy(conn, settings, take(conn, type_="proxy", priority=5))


def test_analyze_stops_between_steps_when_canceled(conn, settings, monkeypatch):
    """Ассет удалили во время анализа: следующий шаг не начинаем."""
    asset_id = make_asset(conn, settings)
    monkeypatch.setattr(handlers, "probe_file", lambda *a, **k: VIDEO)
    monkeypatch.setattr(handlers, "extract_wav", lambda *a, **k: pytest.fail("шаг после отмены не идёт"))
    job = take(conn)
    conn.execute("UPDATE jobs SET status = 'canceled' WHERE id = ?", (job["id"],))
    with pytest.raises(MediaError) as e:
        handlers.handle_analyze(conn, settings, job)
    assert e.value.reason == "canceled"
    assert conn.execute("SELECT count(*) FROM jobs WHERE type = 'proxy'").fetchone()[0] == 0
    assert not (asset_dir(settings, USER, asset_id) / "peaks.json").exists()
