"""Ручки субтитров проекта: сборка реплик в документ и отдача файла в srt и vtt.

Нарезка реплик и кэш разобраны в tests/test_project_subtitles.py; здесь — только ответы ручек:
формат, коды отказов и то, кому они вообще доступны.
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


def make_project(client, *, subtitles="transcript", asset=VIDEO, aspect="16:9", clips=None):
    doc = {
        "output": {"aspect": aspect, "fit": "pad", "fps": 30},
        "clips": clips or [{"asset_id": VIDEO, "in": 0.0, "out": 10.0}],
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


def generate(client, project, **body):
    return client.post(
        f"/api/v1/projects/{project['id']}/subtitles/generate", json={"asset_id": VIDEO, **body}
    )


def with_transcript(client, settings, **over):
    """Проект без субтитров и готовая расшифровка исходника: точка, с которой человек жмёт «Собрать»."""
    seed_asset(settings, me(client))
    add_transcript(client)
    return make_project(client, subtitles=None, **over)


def cues_of(response) -> list[dict]:
    assert response.status_code == 200, response.text
    return response.json()["doc"]["subtitles"]["cues"]


def lines_of(cues) -> list[str]:
    return [line for cue in cues for line in cue["text"].split("\n")]


# ── Сборка реплик в документ ───────────────────────────────────────────────────────────────────


def test_cues_land_in_the_document(client, login_as, settings):
    """Реплики ложатся в документ обычным сохранением: версия растёт, значит работают откат,
    точки сохранения и защита от одновременной правки."""
    login_as()
    project = with_transcript(client, settings)

    r = generate(client, project)
    assert r.status_code == 200, r.text
    subs = r.json()["doc"]["subtitles"]
    assert subs["source"] == "cues" and subs["mode"] == "burn" and subs["style"] == "default"
    assert " ".join(lines_of(subs["cues"])).startswith("Мы поехали")
    assert r.json()["version"] == project["version"] + 1
    # Это состояние проекта, а не ответ на один запрос: следующий читатель видит те же реплики.
    assert client.get(f"/api/v1/projects/{project['id']}").json()["doc"]["subtitles"] == subs


def test_cue_times_follow_the_roll_not_the_source(client, login_as, settings):
    """Клип начинается со 2-й секунды исходника: реплики приезжают к началу ролика."""
    login_as()
    project = with_transcript(client, settings, clips=[{"asset_id": VIDEO, "in": 2.0, "out": 10.0}])
    cues = cues_of(generate(client, project))
    assert cues[0]["start"] == 0.0
    assert "Мы поехали" not in " ".join(lines_of(cues))  # эти слова вырезаны вместе с началом


def test_mode_comes_from_the_request(client, login_as, settings):
    login_as()
    project = with_transcript(client, settings)
    r = generate(client, project, mode="soft")
    assert r.json()["doc"]["subtitles"]["mode"] == "soft"


def test_line_width_follows_the_aspect(client, login_as, settings):
    """Ширина строки — по пропорции проекта: в вертикальном кадре длинная строка уезжает за край."""
    login_as()
    seed_asset(settings, me(client))
    add_transcript(client)
    wide = cues_of(generate(client, make_project(client, subtitles=None)))
    tall = cues_of(generate(client, make_project(client, subtitles=None, aspect="9:16")))

    assert max(len(line) for line in lines_of(tall)) <= 24
    assert len(tall) > len(wide)  # та же фраза распадается на большее число реплик


def test_second_generation_replaces_the_edited_cues(client, login_as, settings):
    """«Собрать заново» — это отказ от прежних правок, и предупреждает о нём интерфейс."""
    login_as()
    project = with_transcript(client, settings)
    saved = generate(client, project).json()
    doc = saved["doc"]
    doc["subtitles"]["cues"][0]["text"] = "Я поправил руками"
    edited = client.put(
        f"/api/v1/projects/{project['id']}",
        json={"name": saved["name"], "version": saved["version"], "doc": doc},
    )
    assert edited.status_code == 200, edited.text

    again = generate(client, project)
    assert "Я поправил руками" not in " ".join(lines_of(cues_of(again)))
    assert again.json()["version"] == edited.json()["version"] + 1


# ── Отказы сборки ──────────────────────────────────────────────────────────────────────────────


def test_generate_without_transcript_is_422(client, login_as, settings):
    login_as()
    seed_asset(settings, me(client))
    project = make_project(client, subtitles=None)
    r = generate(client, project)
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_transcript"
    assert "расшифров" in r.json()["error"]["message"].lower()


def test_generate_for_an_empty_project_is_422(client, login_as, settings):
    """В пустом проекте нет клипов: слова расшифровки не через что пересчитывать."""
    login_as()
    seed_asset(settings, me(client))
    add_transcript(client)
    empty = client.post("/api/v1/projects", json={"name": "Пустой"}).json()
    r = generate(client, empty)
    assert r.status_code == 422 and r.json()["error"]["code"] == "empty_project"


def test_no_words_in_the_chosen_pieces_is_422(client, login_as, settings):
    """Пустой список реплик человек заметил бы только на собранном ролике — отказываем сразу."""
    login_as()
    project = with_transcript(client, settings, clips=[{"asset_id": VIDEO, "in": 30.0, "out": 40.0}])
    r = generate(client, project)
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_cues"
    assert client.get(f"/api/v1/projects/{project['id']}").json()["doc"]["subtitles"] is None


def test_stale_version_is_a_conflict(client, login_as, settings):
    """Версию присылать необязательно, но присланная работает как у обычного сохранения."""
    login_as()
    project = with_transcript(client, settings)
    assert generate(client, project).status_code == 200
    r = generate(client, project, version=project["version"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_conflict"
    assert r.json()["error"]["details"]["project"]["version"] == project["version"] + 1


def test_generate_on_a_foreign_project_is_404(client, login_as, settings):
    login_as()
    project = with_transcript(client, settings)
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert generate(client, project).status_code == 404


def test_agent_generates_cues_with_a_token(bearer_client, settings):
    """Путь агента: тем же токеном собрал реплики — и правит их без карточек."""
    project = with_transcript(bearer_client, settings)
    assert cues_of(generate(bearer_client, project))


def test_generate_requires_auth(client):
    r = client.post("/api/v1/projects/prj_000000000001/subtitles/generate",
                    json={"asset_id": VIDEO})
    assert r.status_code == 401


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
