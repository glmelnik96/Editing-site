"""Ручка субтитров проекта: GET /api/v1/projects/{id}/subtitles в srt и vtt.

Нарезка реплик и кэш разобраны в tests/test_project_subtitles.py; здесь — только ответы ручки:
формат, коды отказов и то, кому она вообще доступна.
"""
import sqlite3

from server.app.util import now_iso

VIDEO = "ast_000000000001"
SUBS_FILE = "ast_000000000002"

PHRASE = "Мы поехали в большой старый дом на окраине города рано утром"


def seed_asset(settings, user_id, *, asset_id=VIDEO, kind="video", ext="mp4", duration=60.0):
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, has_audio, "
        "created_at, last_access_at) VALUES (?, ?, ?, ?, ?, 1, 'ready', ?, ?, ?, ?)",
        (asset_id, user_id, kind, f"a.{ext}", ext, duration, int(kind == "video"), now_iso(), now_iso()),
    )
    conn.commit()
    conn.close()
    return asset_id


def add_transcript(client, asset_id=VIDEO):
    """Расшифровка через ручку транскрипта: она кладёт тот самый transcript.json, который читает сборка.

    Слова обязательны: реплики режутся по ним, и без слов вышел бы пустой файл вместо субтитров.
    """
    words = [
        {"w": word, "s": round(i * 0.3, 3), "e": round((i + 1) * 0.3, 3)}
        for i, word in enumerate(PHRASE.split())
    ]
    r = client.put(
        f"/api/v1/assets/{asset_id}/transcript",
        json={"segments": [{"start": 0.0, "end": words[-1]["e"], "text": PHRASE, "words": words}]},
    )
    assert r.status_code == 200, r.text


def make_project(client, *, subtitles="transcript", asset=VIDEO):
    doc = {
        "output": {"aspect": "16:9", "fit": "pad", "fps": 30},
        "clips": [{"asset_id": VIDEO, "in": 0.0, "out": 10.0}],
    }
    if subtitles:
        doc["subtitles"] = {"source": subtitles, "asset_id": asset, "mode": "burn", "style": "default"}
    r = client.post("/api/v1/projects", json={"name": "С субтитрами", "doc": doc})
    assert r.status_code == 201, r.text
    return r.json()


def me(client) -> str:
    return client.get("/api/v1/me").json()["id"]


def ready_project(client, settings):
    """Проект с субтитрами из расшифровки и готовой расшифровкой исходника."""
    seed_asset(settings, me(client))
    add_transcript(client)
    return make_project(client)


def subtitles(client, project, **params):
    return client.get(f"/api/v1/projects/{project['id']}/subtitles", params=params)


# ── Отдача ─────────────────────────────────────────────────────────────────────────────────────


def test_both_formats_are_served(client, login_as, settings):
    login_as()
    project = ready_project(client, settings)

    srt = subtitles(client, project, format="srt")
    assert srt.status_code == 200, srt.text
    assert srt.headers["content-type"] == "text/plain; charset=utf-8"
    assert srt.text.startswith("1\n") and "Мы поехали" in srt.text

    vtt = subtitles(client, project, format="vtt")
    assert vtt.status_code == 200, vtt.text
    assert vtt.headers["content-type"] == "text/plain; charset=utf-8"
    assert vtt.text.startswith("WEBVTT") and "Мы поехали" in vtt.text


def test_default_format_is_srt(client, login_as, settings):
    login_as()
    project = ready_project(client, settings)
    assert subtitles(client, project).text == subtitles(client, project, format="srt").text


def test_unknown_format_is_422(client, login_as, settings):
    login_as()
    project = ready_project(client, settings)
    assert subtitles(client, project, format="ass").status_code == 422


# ── Когда собирать нечего ──────────────────────────────────────────────────────────────────────


def test_project_without_subtitles_is_422(client, login_as, settings):
    """Расшифровка есть, но документ её не просит: отказ говорит про документ, а не про транскрипт."""
    login_as()
    seed_asset(settings, me(client))
    add_transcript(client)
    project = make_project(client, subtitles=None)
    r = subtitles(client, project)
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_transcript_subtitles"


def test_file_subtitles_are_not_built(client, login_as, settings):
    """source=file собирать нечего: этот файл лежит у своего ассета и берётся оттуда же."""
    login_as()
    user_id = me(client)
    seed_asset(settings, user_id)
    seed_asset(settings, user_id, asset_id=SUBS_FILE, kind="subtitle", ext="srt", duration=0.0)
    add_transcript(client)
    project = make_project(client, subtitles="file", asset=SUBS_FILE)
    r = subtitles(client, project)
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_transcript_subtitles"


def test_without_transcript_is_422(client, login_as, settings):
    login_as()
    seed_asset(settings, me(client))
    project = make_project(client)
    r = subtitles(client, project)
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_transcript"
    # Отказ обязан подсказать выход: расшифровку надо заказать, иначе собирать не из чего.
    assert "расшифров" in r.json()["error"]["message"].lower()


# ── Доступ ─────────────────────────────────────────────────────────────────────────────────────


def test_foreign_project_is_404(client, login_as, settings):
    """Чужой проект неотличим от несуществующего: чужие идентификаторы наружу не подтверждаем."""
    login_as()
    project = ready_project(client, settings)
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert subtitles(client, project).status_code == 404


def test_agent_gets_subtitles_with_a_token(bearer_client, settings):
    """Сценарий агента: смонтировал по транскрипту — забирает субтитры тем же токеном."""
    project = ready_project(bearer_client, settings)
    r = subtitles(bearer_client, project, format="vtt")
    assert r.status_code == 200 and r.text.startswith("WEBVTT")


def test_subtitles_require_auth(client):
    assert client.get("/api/v1/projects/prj_000000000001/subtitles").status_code == 401
