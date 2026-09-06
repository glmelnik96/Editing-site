"""Сборка проекта с субтитрами из расшифровки на настоящем ffmpeg.

Ролик генерируется на лету и идёт секунды. Здесь проверяется то, чего не видно на фикстурах:
что путь к собранному файлу субтитров доживает до ffmpeg целым и тот его понимает.
"""
import json
import subprocess

import pytest

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.projects.store import create_project
from server.app.storage import asset_dir, subs_dir, transcript_path
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate
from server.media.probe import probe_file
from server.worker import handlers
from server.worker.queue import claim_job
from tests.media_fixtures import FFPROBE, HAVE_FFMPEG, make_video

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="нужен ffmpeg в PATH")

USER = "usr_00000000000a"
ASSET = "ast_000000000001"
SECONDS = 6
PHRASE = "Мы поехали в большой старый дом на окраине города рано утром и стояли у ворот"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data")


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = connect(settings.db_path)
    migrate(c)
    c.execute(
        "INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)", (USER, now_iso())
    )
    folder = asset_dir(settings, USER, ASSET)
    folder.mkdir(parents=True, exist_ok=True)
    make_video(folder / "source.mp4", seconds=SECONDS)
    c.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
        "has_audio, created_at, last_access_at) "
        "VALUES (?, ?, 'video', 'a.mp4', 'mp4', 1, 'proxy_ready', ?, 1, ?, ?)",
        (ASSET, USER, float(SECONDS), now_iso(), now_iso()),
    )
    words = PHRASE.split()
    step = SECONDS / len(words)
    marked = [{"w": word, "s": round(i * step, 3), "e": round((i + 1) * step, 3),
               "interpolated": True} for i, word in enumerate(words)]
    transcript_path(settings, USER, ASSET).write_text(
        json.dumps({"asset_id": ASSET, "duration": float(SECONDS), "segments": [
            {"id": 1, "start": 0.0, "end": float(SECONDS), "text": PHRASE, "words": marked},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    yield c
    c.close()


def render(conn, settings, mode):
    """Проект из двух кусков разных мест исходника — на нём и видно пересчёт времён.

    mode=None собирает тот же ролик без субтитров: он нужен как образец для сравнения кадров.
    """
    subtitles = None if mode is None else {
        "source": "transcript", "asset_id": ASSET, "mode": mode, "style": "default",
    }
    project = create_project(conn, settings, USER, name="С субтитрами", raw_doc={
        "output": {"aspect": "16:9", "fit": "pad", "fps": 25},
        "clips": [
            {"asset_id": ASSET, "in": 0.0, "out": 2.0},
            {"asset_id": ASSET, "in": 4.0, "out": 6.0},
        ],
        "subtitles": subtitles,
    })
    enqueue_job(conn, user_id=USER, type_="render", target_id=project["id"],
                params={"quality": "draft"})
    handlers.handle_render(conn, settings, claim_job(conn, lane="cpu", pid=1))
    row = conn.execute(
        "SELECT * FROM renders WHERE project_id = ?", (project["id"],)
    ).fetchone()
    return project, row


def frame_hashes(settings, path) -> list[str]:
    """Хеши декодированных кадров: по ним видно, изменилась ли сама картинка."""
    out = subprocess.run(
        [settings.ffmpeg_path, "-v", "error", "-i", str(path), "-map", "0:v", "-f", "framemd5", "-"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [line for line in out.splitlines() if line and not line.startswith("#")]


def subtitle_codecs(path) -> list[str]:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "s", "-show_entries", "stream=codec_name",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return [stream["codec_name"] for stream in json.loads(out).get("streams", [])]


def test_burned_subtitles_produce_a_playable_render(conn, settings):
    _, row = render(conn, settings, "burn")
    assert row["duration"] == 4.0
    info = probe_file(settings, row["path"])
    assert info.duration == pytest.approx(4.0, abs=0.3)
    assert (info.width, info.height) == (1280, 720) and info.has_audio is True
    # Вжигание не оставляет дорожки субтитров: текст теперь часть картинки.
    assert subtitle_codecs(row["path"]) == []


def test_burned_text_actually_reaches_the_picture(conn, settings):
    """Пустой или непрочитанный файл субтитров ffmpeg проглотил бы молча, и ролик собрался бы
    прежним. Поэтому сверяем кадры с тем же роликом без субтитров."""
    _, burned = render(conn, settings, "burn")
    _, plain = render(conn, settings, None)
    assert frame_hashes(settings, burned["path"]) != frame_hashes(settings, plain["path"])


def test_soft_subtitles_become_a_track_in_the_container(conn, settings):
    _, row = render(conn, settings, "soft")
    assert subtitle_codecs(row["path"]) == ["mov_text"]
    assert probe_file(settings, row["path"]).duration == pytest.approx(4.0, abs=0.3)


def test_cues_stay_inside_the_render(conn, settings):
    """Времена реплик пересчитаны через клипы: середина исходника вырезана, и субтитры сдвинулись."""
    project, _ = render(conn, settings, "burn")
    text = (subs_dir(settings, USER, project["id"]) / f"{project['version']}.srt").read_text(
        encoding="utf-8"
    )
    # Таймкоды одной ширины, поэтому сравниваются как строки.
    stamps = [line.split(" --> ") for line in text.splitlines() if "-->" in line]
    assert stamps and all(end <= "00:00:04,000" for _, end in stamps)
    # Кусок 4–6 с исходника звучит во второй половине ролика, а не в конце шестисекундной шкалы.
    assert stamps[-1][1] > "00:00:02,000"
