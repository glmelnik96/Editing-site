from pathlib import Path

import pytest

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.storage import asset_dir
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate
from server.media.run import MediaError
from server.worker import handlers
from server.worker.queue import claim_job

USER = "usr_00000000000a"
ASSET = "ast_000000000001"


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
    c.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, has_audio, "
        "created_at, last_access_at) VALUES "
        "(?, ?, 'video', 'встреча.mp4', 'mp4', 10, 'proxy_ready', 60, 1, ?, ?)",
        (ASSET, USER, now_iso(), now_iso()),
    )
    folder = asset_dir(settings, USER, ASSET)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "source.mp4").write_bytes(b"x" * 10)
    yield c
    c.close()


def take_convert(conn, fmt="mp3", conversion_id="cnv_000000000001"):
    enqueue_job(
        conn, user_id=USER, type_="convert", target_id=ASSET,
        params={"format": fmt, "conversion_id": conversion_id},
    )
    return claim_job(conn, lane="cpu", pid=1)


def fake_ffmpeg(payload=b"audio"):
    def run(args, *, timeout, on_line, should_stop=None, stop_check_sec=2.0):
        on_line("out_time_us=30000000")
        Path(args[-1]).write_bytes(payload)
    return run


def test_handlers_include_convert():
    assert handlers.HANDLERS["convert"] is handlers.handle_convert


def test_convert_writes_file_and_row(conn, settings, monkeypatch):
    monkeypatch.setattr(handlers, "run_streaming", fake_ffmpeg())
    job = take_convert(conn)
    handlers.handle_convert(conn, settings, job)

    row = conn.execute("SELECT * FROM conversions").fetchone()
    assert row["asset_id"] == ASSET and row["format"] == "mp3"
    assert row["user_id"] == USER and row["job_id"] == job["id"]
    assert row["id"] == "cnv_000000000001"
    assert row["duration"] == 60.0 and row["size"] == len(b"audio")
    assert row["expires_at"] > row["created_at"]
    path = Path(row["path"])
    assert path.read_bytes() == b"audio"
    assert path == asset_dir(settings, USER, ASSET) / "conversions" / "cnv_000000000001.mp3"
    assert not list(path.parent.glob("*.part"))


def test_convert_progress_uses_asset_duration(conn, settings, monkeypatch):
    monkeypatch.setattr(handlers, "run_streaming", fake_ffmpeg())
    job = take_convert(conn)
    handlers.handle_convert(conn, settings, job)
    assert conn.execute("SELECT progress FROM jobs WHERE id = ?", (job["id"],)).fetchone()[0] == 0.5


def test_convert_passes_whitelist_command(conn, settings, monkeypatch):
    seen = {}

    def spy(args, **kwargs):
        seen["args"] = args
        Path(args[-1]).write_bytes(b"x")

    monkeypatch.setattr(handlers, "run_streaming", spy)
    handlers.handle_convert(conn, settings, take_convert(conn, fmt="wav"))
    assert seen["args"][seen["args"].index("-c:a") + 1] == "pcm_s16le"
    assert seen["args"][-1].endswith(".part")


def test_missing_asset_is_not_an_error(conn, settings, monkeypatch):
    monkeypatch.setattr(handlers, "run_streaming", lambda *a, **k: pytest.fail("конвертировать нечего"))
    enqueue_job(conn, user_id=USER, type_="convert", target_id="ast_00000000dead",
                params={"format": "mp3", "conversion_id": "cnv_000000000099"})
    job = claim_job(conn, lane="cpu", pid=1)
    handlers.handle_convert(conn, settings, job)
    assert conn.execute("SELECT count(*) FROM conversions").fetchone()[0] == 0


def test_disk_low_refuses_before_ffmpeg(conn, settings, monkeypatch):
    monkeypatch.setattr(handlers, "disk_free_bytes", lambda _p: 1024)
    monkeypatch.setattr(handlers, "run_streaming", lambda *a, **k: pytest.fail("диск полный"))
    with pytest.raises(MediaError) as exc:
        handlers.handle_convert(conn, settings, take_convert(conn))
    assert exc.value.reason == "disk_low"
    assert conn.execute("SELECT count(*) FROM conversions").fetchone()[0] == 0


def test_ffmpeg_failure_leaves_no_partial_and_no_row(conn, settings, monkeypatch):
    def boom(args, **kwargs):
        Path(args[-1]).write_bytes(b"half")
        raise MediaError("tool_failed", "ffmpeg упал", "lame error dump")

    monkeypatch.setattr(handlers, "run_streaming", boom)
    with pytest.raises(MediaError):
        handlers.handle_convert(conn, settings, take_convert(conn))
    folder = asset_dir(settings, USER, ASSET) / "conversions"
    assert conn.execute("SELECT count(*) FROM conversions").fetchone()[0] == 0
    assert not folder.exists() or not list(folder.glob("*"))


def test_cancel_reaches_ffmpeg(conn, settings, monkeypatch):
    job = take_convert(conn)
    conn.execute("UPDATE jobs SET status = 'canceled' WHERE id = ?", (job["id"],))
    seen = {}

    def spy(args, *, timeout, on_line, should_stop=None, stop_check_sec=2.0):
        seen["stop"] = should_stop is not None and should_stop() is True
        Path(args[-1]).write_bytes(b"x")

    monkeypatch.setattr(handlers, "run_streaming", spy)
    handlers.handle_convert(conn, settings, job)
    assert seen["stop"] is True


def test_success_evicts_the_oldest_conversion(conn, settings, monkeypatch):
    """Готовых на файл не больше десяти. Вытесняем в момент успеха, в одной транзакции со вставкой:
    при постановке задания это стоило бы готового файла даже отменённой конвертации."""
    monkeypatch.setattr(handlers, "run_streaming", fake_ffmpeg())
    folder = asset_dir(settings, USER, ASSET) / "conversions"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(10):
        cid = f"cnv_0000000000{i:02d}"
        path = folder / f"{cid}.mp3"
        path.write_bytes(b"x")
        conn.execute(
            "INSERT INTO conversions (id, user_id, asset_id, job_id, format, path, size, duration, "
            "created_at, expires_at) VALUES (?, ?, ?, 'job_old', 'mp3', ?, 1, 1, ?, ?)",
            (cid, USER, ASSET, str(path), f"2026-01-{i + 1:02d}T00:00:00.000Z", "2030-01-01T00:00:00.000Z"),
        )

    job = take_convert(conn, fmt="wav", conversion_id="cnv_000000000099")
    handlers.handle_convert(conn, settings, job)

    ids = {row["id"] for row in conn.execute("SELECT id FROM conversions")}
    assert len(ids) == 10
    assert "cnv_000000000099" in ids
    assert "cnv_000000000000" not in ids
    assert not (folder / "cnv_000000000000.mp3").exists()


def test_failed_conversion_keeps_the_oldest(conn, settings, monkeypatch):
    """Упало ffmpeg — старая конверсия остаётся на месте: заменить её теперь нечем."""
    def boom(args, *, timeout, on_line, should_stop=None, stop_check_sec=2.0):
        raise MediaError("ffmpeg", "не вышло")

    monkeypatch.setattr(handlers, "run_streaming", boom)
    folder = asset_dir(settings, USER, ASSET) / "conversions"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "cnv_000000000000.mp3"
    path.write_bytes(b"x")
    conn.execute(
        "INSERT INTO conversions (id, user_id, asset_id, job_id, format, path, size, duration, "
        "created_at, expires_at) VALUES (?, ?, ?, 'job_old', 'mp3', ?, 1, 1, ?, ?)",
        ("cnv_000000000000", USER, ASSET, str(path), "2026-01-01T00:00:00.000Z", "2030-01-01T00:00:00.000Z"),
    )

    job = take_convert(conn, fmt="wav", conversion_id="cnv_000000000099")
    with pytest.raises(MediaError):
        handlers.handle_convert(conn, settings, job)

    assert path.exists()
    assert conn.execute("SELECT count(*) FROM conversions").fetchone()[0] == 1

