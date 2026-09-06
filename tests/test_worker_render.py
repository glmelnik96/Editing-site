import json

import pytest

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.projects.store import create_project, save_project
from server.app.storage import asset_dir, render_dir, subs_dir, transcript_path
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
        "created_at, last_access_at) VALUES (?, ?, 'video', 'a.mp4', 'mp4', 10, 'proxy_ready', 60, 1, ?, ?)",
        (ASSET, USER, now_iso(), now_iso()),
    )
    folder = asset_dir(settings, USER, ASSET)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "source.mp4").write_bytes(b"x" * 10)
    yield c
    c.close()


def make_project(conn, settings, out=6.0):
    return create_project(
        conn, settings, USER, name="Мой",
        raw_doc={"clips": [{"asset_id": ASSET, "in": 1.0, "out": out}]},
    )


def take_render(conn, project_id, quality="draft"):
    enqueue_job(conn, user_id=USER, type_="render", target_id=project_id,
                params={"quality": quality})
    return claim_job(conn, lane="cpu", pid=1)


def fake_ffmpeg(payload=b"video"):
    def run(args, *, timeout, on_line, should_stop=None, stop_check_sec=2.0):
        on_line("out_time_us=2500000")
        with open(args[-1], "wb") as f:
            f.write(payload)
    return run


def add_transcript(settings):
    words = [{"w": f"слово{i}", "s": round(i * 0.4, 3), "e": round(i * 0.4 + 0.3, 3),
              "interpolated": True} for i in range(12)]
    transcript_path(settings, USER, ASSET).write_text(
        json.dumps({"asset_id": ASSET, "duration": 60.0, "segments": [
            {"id": 1, "start": 0.0, "end": 4.8, "text": " ".join(w["w"] for w in words),
             "words": words},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )


def subs_project(conn, settings, mode="burn"):
    return create_project(conn, settings, USER, name="С субтитрами", raw_doc={
        "clips": [{"asset_id": ASSET, "in": 1.0, "out": 6.0}],
        "subtitles": {"source": "transcript", "asset_id": ASSET, "mode": mode, "style": "default"},
    })


class TestУспех:
    def test_рендер_кладёт_файл_и_строку(self, conn, settings, monkeypatch):
        project = make_project(conn, settings)
        monkeypatch.setattr(handlers, "run_streaming", fake_ffmpeg())
        job = take_render(conn, project["id"])
        handlers.handle_render(conn, settings, job)

        row = conn.execute("SELECT * FROM renders").fetchone()
        assert row["project_id"] == project["id"] and row["quality"] == "draft"
        assert row["user_id"] == USER and row["job_id"] == job["id"]
        assert row["duration"] == 5.0 and row["size"] == len(b"video")
        assert row["expires_at"] > row["created_at"]
        path = render_dir(settings, USER, project["id"]) / f"{row['id']}.mp4"
        assert path.read_bytes() == b"video"
        assert not list(path.parent.glob("*.part"))

    def test_прогресс_идёт_от_длительности_ролика(self, conn, settings, monkeypatch):
        project = make_project(conn, settings)
        monkeypatch.setattr(handlers, "run_streaming", fake_ffmpeg())
        job = take_render(conn, project["id"])
        handlers.handle_render(conn, settings, job)
        # 2.5 с из 5 с ролика
        assert conn.execute("SELECT progress FROM jobs WHERE id = ?", (job["id"],)).fetchone()[0] == 0.5

    def test_качество_берётся_из_задания(self, conn, settings, monkeypatch):
        project = make_project(conn, settings)
        seen = {}

        def spy(args, **kwargs):
            seen["preset"] = args[args.index("-preset") + 1]
            with open(args[-1], "wb") as f:
                f.write(b"v")

        monkeypatch.setattr(handlers, "run_streaming", spy)
        handlers.handle_render(conn, settings, take_render(conn, project["id"], quality="final"))
        assert seen["preset"] == "veryfast"
        assert conn.execute("SELECT quality FROM renders").fetchone()[0] == "final"


class TestСубтитрыИзТранскрипта:
    def spy_args(self, monkeypatch):
        """Перехватывает командную строку: настоящий ffmpeg тут не нужен, важна только сборка."""
        seen = {}

        def run(args, **kwargs):
            seen["args"] = args
            with open(args[-1], "wb") as f:
                f.write(b"v")

        monkeypatch.setattr(handlers, "run_streaming", run)
        return seen

    def test_собранный_файл_попадает_в_команду(self, conn, settings, monkeypatch):
        add_transcript(settings)
        project = subs_project(conn, settings)
        seen = self.spy_args(monkeypatch)
        handlers.handle_render(conn, settings, take_render(conn, project["id"]))

        srt = subs_dir(settings, USER, project["id"]) / f"{project['version']}.srt"
        assert srt.exists() and srt.read_text(encoding="utf-8")
        command = " ".join(seen["args"])
        assert "subtitles=" in command and srt.name in command

    def test_мягкая_дорожка_берёт_тот_же_файл(self, conn, settings, monkeypatch):
        add_transcript(settings)
        project = subs_project(conn, settings, mode="soft")
        seen = self.spy_args(monkeypatch)
        handlers.handle_render(conn, settings, take_render(conn, project["id"]))

        srt = subs_dir(settings, USER, project["id"]) / f"{project['version']}.srt"
        assert str(srt) in seen["args"] and "mov_text" in seen["args"]

    def test_без_расшифровки_задание_падает_внятно(self, conn, settings, monkeypatch):
        project = subs_project(conn, settings)
        monkeypatch.setattr(handlers, "run_streaming", lambda *a, **k: pytest.fail("собирать нечего"))
        with pytest.raises(MediaError) as e:
            handlers.handle_render(conn, settings, take_render(conn, project["id"]))
        # Человек должен прочитать «закажите расшифровку», а не ругань ffmpeg на пустой путь.
        assert e.value.reason == "no_transcript" and "расшифров" in e.value.message.lower()
        assert conn.execute("SELECT count(*) FROM renders").fetchone()[0] == 0

    def test_новая_версия_собирается_заново_а_прежняя_переиспользуется(
        self, conn, settings, monkeypatch
    ):
        add_transcript(settings)
        project = subs_project(conn, settings)
        monkeypatch.setattr(handlers, "run_streaming", fake_ffmpeg())
        handlers.handle_render(conn, settings, take_render(conn, project["id"]))

        first = subs_dir(settings, USER, project["id"]) / f"{project['version']}.srt"
        # Якорь переживёт вторую сборку той же версии — значит файл взяли из кэша.
        first.write_text("ЯКОРЬ", encoding="utf-8")
        handlers.handle_render(conn, settings, take_render(conn, project["id"]))
        assert first.read_text(encoding="utf-8") == "ЯКОРЬ"

        saved = save_project(
            conn, settings, USER, project["id"], name=project["name"],
            raw_doc={**project["doc"], "clips": [{"asset_id": ASSET, "in": 2.0, "out": 6.0}]},
            version=project["version"],
        )
        handlers.handle_render(conn, settings, take_render(conn, saved["id"]))
        second = first.with_name(f"{saved['version']}.srt")
        assert second.exists() and second.read_text(encoding="utf-8") != "ЯКОРЬ"


class TestОтказы:
    def test_проект_исчез(self, conn, settings, monkeypatch):
        monkeypatch.setattr(handlers, "run_streaming", lambda *a, **k: pytest.fail("собирать нечего"))
        handlers.handle_render(conn, settings, take_render(conn, "prj_00000000dead"))
        assert conn.execute("SELECT count(*) FROM renders").fetchone()[0] == 0

    def test_ассет_удалили_после_сохранения(self, conn, settings, monkeypatch):
        project = make_project(conn, settings)
        conn.execute("DELETE FROM assets WHERE id = ?", (ASSET,))
        monkeypatch.setattr(handlers, "run_streaming", lambda *a, **k: pytest.fail("собирать нечего"))
        with pytest.raises(MediaError) as e:
            handlers.handle_render(conn, settings, take_render(conn, project["id"]))
        assert "удал" in e.value.message.lower() or "недоступен" in e.value.message.lower()

    def test_ассет_ещё_не_готов(self, conn, settings, monkeypatch):
        project = make_project(conn, settings)
        conn.execute("UPDATE assets SET status = 'analyzing' WHERE id = ?", (ASSET,))
        monkeypatch.setattr(handlers, "run_streaming", lambda *a, **k: pytest.fail("собирать нечего"))
        with pytest.raises(MediaError):
            handlers.handle_render(conn, settings, take_render(conn, project["id"]))

    def test_мало_места_на_диске(self, conn, settings, monkeypatch):
        project = make_project(conn, settings)
        monkeypatch.setattr(handlers, "disk_free_bytes", lambda _p: 1024)
        monkeypatch.setattr(handlers, "run_streaming", lambda *a, **k: pytest.fail("собирать нечего"))
        with pytest.raises(MediaError) as e:
            handlers.handle_render(conn, settings, take_render(conn, project["id"]))
        assert "мест" in e.value.message.lower()

    def test_ffmpeg_упал_и_не_оставил_огрызка(self, conn, settings, monkeypatch):
        project = make_project(conn, settings)

        def boom(args, **kwargs):
            with open(args[-1], "wb") as f:
                f.write(b"half")
            raise MediaError("tool_failed", "ffmpeg упал", "x264 error")

        monkeypatch.setattr(handlers, "run_streaming", boom)
        with pytest.raises(MediaError):
            handlers.handle_render(conn, settings, take_render(conn, project["id"]))
        folder = render_dir(settings, USER, project["id"])
        assert conn.execute("SELECT count(*) FROM renders").fetchone()[0] == 0
        assert not list(folder.glob("*")) or not any(f.suffix == ".part" for f in folder.glob("*"))

    def test_пустой_проект(self, conn, settings, monkeypatch):
        project = create_project(conn, settings, USER, name="Пустой", raw_doc=None)
        monkeypatch.setattr(handlers, "run_streaming", lambda *a, **k: pytest.fail("собирать нечего"))
        with pytest.raises(MediaError):
            handlers.handle_render(conn, settings, take_render(conn, project["id"]))

    def test_отмена_доходит_до_ffmpeg(self, conn, settings, monkeypatch):
        project = make_project(conn, settings)
        seen = {}

        def spy(args, *, timeout, on_line, should_stop=None, stop_check_sec=2.0):
            seen["есть_проверка"] = should_stop is not None and callable(should_stop)
            seen["таймаут"] = timeout
            with open(args[-1], "wb") as f:
                f.write(b"v")

        monkeypatch.setattr(handlers, "run_streaming", spy)
        handlers.handle_render(conn, settings, take_render(conn, project["id"]))
        assert seen["есть_проверка"] is True
        assert seen["таймаут"] == settings.render_timeout_sec
