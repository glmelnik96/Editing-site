"""Ручки транскрипта: постановка расшифровки, чтение в трёх форматах, свой транскрипт, удаление.

Воркер здесь не участвует: задание только ставится в очередь, а транскрипт кладётся через PUT —
как это делает агент, у которого расшифровка своя.
"""
import json
import sqlite3

import pytest

from server.app.storage import asset_dir
from server.app.util import now_iso

ASSET = "ast_000000000001"
SEGMENTS = [
    {"start": 1.0, "end": 2.5, "text": "привет мир"},
    {"start": 3.0, "end": 4.25, "text": "как дела"},
]


@pytest.fixture
def settings(settings):
    """Ключ провайдера задан: без него расшифровка выключена целиком, и проверять было бы нечего."""
    settings.transcribe_api_key = "k"
    return settings


def seed_asset(settings, user_id, *, status="ready", has_audio=1, duration=60.0, asset_id=ASSET):
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, has_audio, "
        "created_at, last_access_at) VALUES (?, ?, 'video', 'a.mp4', 'mp4', 1, ?, ?, ?, ?, ?)",
        (asset_id, user_id, status, duration, has_audio, now_iso(), now_iso()),
    )
    conn.commit()
    conn.close()
    return asset_id


def transcript_rows(settings, asset_id=ASSET) -> int:
    conn = sqlite3.connect(str(settings.db_path))
    count = conn.execute("SELECT count(*) FROM transcripts WHERE asset_id = ?", (asset_id,)).fetchone()[0]
    conn.close()
    return count


def put(client, asset_id=ASSET, segments=None, **rest):
    return client.put(
        f"/api/v1/assets/{asset_id}/transcript",
        json={"segments": SEGMENTS if segments is None else segments, **rest},
    )


def ready_asset(client, settings, **over) -> str:
    """Готовый к расшифровке ассет текущего пользователя."""
    return seed_asset(settings, client.get("/api/v1/me").json()["id"], **over)


# ── Постановка расшифровки ─────────────────────────────────────────────────────────────────────


def test_transcribe_queues_a_job(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    r = client.post(f"/api/v1/assets/{ASSET}/transcribe", json={})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"].startswith("job_")
    assert body["language"] == settings.transcribe_language

    job = client.get(f"/api/v1/jobs/{body['job_id']}").json()
    assert job["type"] == "transcribe" and job["status"] == "queued"


def test_language_from_the_body_reaches_the_job(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    r = client.post(f"/api/v1/assets/{ASSET}/transcribe", json={"language": "en"})
    assert r.status_code == 202 and r.json()["language"] == "en"

    conn = sqlite3.connect(str(settings.db_path))
    params = conn.execute("SELECT params FROM jobs WHERE id = ?", (r.json()["job_id"],)).fetchone()[0]
    conn.close()
    assert json.loads(params)["language"] == "en"


def test_unknown_asset_is_404(client, login_as, settings):
    login_as()
    assert client.post(f"/api/v1/assets/{ASSET}/transcribe", json={}).status_code == 404


def test_asset_below_ready_is_422(client, login_as, settings):
    login_as()
    ready_asset(client, settings, status="analyzing")
    r = client.post(f"/api/v1/assets/{ASSET}/transcribe", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "asset_not_ready"


def test_asset_without_audio_is_422(client, login_as, settings):
    login_as()
    ready_asset(client, settings, has_audio=0)
    r = client.post(f"/api/v1/assets/{ASSET}/transcribe", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_audio"


def test_existing_transcript_is_409(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    assert put(client).status_code == 200
    r = client.post(f"/api/v1/assets/{ASSET}/transcribe", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "transcript_exists"


def test_second_run_while_the_first_is_queued_is_409(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    assert client.post(f"/api/v1/assets/{ASSET}/transcribe", json={}).status_code == 202
    r = client.post(f"/api/v1/assets/{ASSET}/transcribe", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_queued"


def test_empty_provider_key_is_503(client, login_as, settings):
    """Пустой ключ выключает расшифровку, а не роняет её в сеть."""
    login_as()
    ready_asset(client, settings)
    settings.transcribe_api_key = ""  # тот же объект настроек держит приложение
    r = client.post(f"/api/v1/assets/{ASSET}/transcribe", json={})
    assert r.status_code == 503 and r.json()["error"]["code"] == "transcription_unavailable"


def test_transcribe_again_after_delete(client, login_as, settings):
    """Перезапуск только после удаления — и после него он обязан пройти."""
    login_as()
    ready_asset(client, settings)
    assert put(client).status_code == 200
    assert client.delete(f"/api/v1/assets/{ASSET}/transcript").status_code == 204
    assert client.post(f"/api/v1/assets/{ASSET}/transcribe", json={}).status_code == 202


# ── Чтение ─────────────────────────────────────────────────────────────────────────────────────


def test_transcript_is_read_as_json(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    put(client, language="ru")
    r = client.get(f"/api/v1/assets/{ASSET}/transcript")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["asset_id"] == ASSET and body["language"] == "ru" and body["duration"] == 60.0
    assert [s["id"] for s in body["segments"]] == [1, 2]
    assert body["segments"][0]["text"] == "привет мир"
    assert body["stats"] == {"source": "uploaded"}


def test_transcript_is_read_as_srt(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    put(client)
    r = client.get(f"/api/v1/assets/{ASSET}/transcript", params={"format": "srt"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.text == (
        "1\n00:00:01,000 --> 00:00:02,500\nпривет мир\n\n"
        "2\n00:00:03,000 --> 00:00:04,250\nкак дела\n"
    )


def test_transcript_is_read_as_vtt(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    put(client)
    r = client.get(f"/api/v1/assets/{ASSET}/transcript", params={"format": "vtt"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.text.startswith("WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nпривет мир\n")


def test_unknown_format_is_422(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    put(client)
    assert client.get(f"/api/v1/assets/{ASSET}/transcript", params={"format": "ass"}).status_code == 422


def test_missing_transcript_is_404(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    assert client.get(f"/api/v1/assets/{ASSET}/transcript").status_code == 404
    assert client.delete(f"/api/v1/assets/{ASSET}/transcript").status_code == 404


# ── Свой транскрипт ────────────────────────────────────────────────────────────────────────────


def test_uploaded_words_keep_their_real_times(client, login_as, settings):
    """Единственный случай, когда времена слов настоящие: пометки interpolated на них быть не должно,
    иначе по ним нельзя будет резать."""
    login_as()
    ready_asset(client, settings)
    words = [{"w": "привет", "s": 1.0, "e": 1.7}, {"w": "мир", "s": 1.8, "e": 2.5}]
    r = put(client, segments=[{"start": 1.0, "end": 2.5, "text": "привет мир", "words": words}])
    assert r.status_code == 200, r.text
    saved = r.json()["segments"][0]["words"]
    assert saved == words
    assert not any("interpolated" in word for word in saved)


def test_uploaded_times_are_clamped_to_the_asset(client, login_as, settings):
    login_as()
    ready_asset(client, settings, duration=10.0)
    r = put(client, segments=[{"start": 8.0, "end": 99.0, "text": "хвост за концом"}])
    assert r.status_code == 200
    assert r.json()["segments"][0]["end"] == 10.0


def test_uploaded_segments_are_sorted_and_numbered(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    r = put(client, segments=[
        {"start": 5.0, "end": 6.0, "text": "поздняя"},
        {"start": 1.0, "end": 2.0, "text": "ранняя"},
    ])
    assert [(s["id"], s["text"]) for s in r.json()["segments"]] == [(1, "ранняя"), (2, "поздняя")]


def test_uploaded_transcript_carries_the_measured_pauses(client, login_as, settings):
    """Агент забирает текст и паузы одним запросом, поэтому карты пауз лежат и в чужом транскрипте."""
    login_as()
    user_id = client.get("/api/v1/me").json()["id"]
    seed_asset(settings, user_id)
    folder = asset_dir(settings, user_id, ASSET)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "analysis.json").write_text(
        json.dumps({"silences": [{"start": 2.5, "end": 3.0}], "silences_dense": []}), encoding="utf-8"
    )
    body = put(client).json()
    assert body["silences"] == [{"start": 2.5, "end": 3.0}] and body["silences_dense"] == []


def test_upload_replaces_the_previous_transcript(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    assert put(client).status_code == 200
    r = put(client, segments=[{"start": 0.5, "end": 1.5, "text": "другой текст"}])
    assert r.status_code == 200 and len(r.json()["segments"]) == 1
    assert transcript_rows(settings) == 1
    assert client.get(f"/api/v1/assets/{ASSET}/transcript").json()["segments"][0]["text"] == "другой текст"


def test_empty_segment_list_is_422(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    r = put(client, segments=[])
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_transcript"


def test_segment_without_required_fields_is_422(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    assert put(client, segments=[{"start": 1.0, "end": 2.0}]).status_code == 422
    assert put(client, segments=[{"start": 1.0, "text": "без конца"}]).status_code == 422
    assert put(client, segments=[{"start": 1.0, "end": 2.0, "text": "  "}]).status_code == 422
    assert put(client, segments=[{"start": 2.0, "end": 1.0, "text": "задом наперёд"}]).status_code == 422
    assert transcript_rows(settings) == 0


# ── Карточка ассета, удаление, чужое ────────────────────────────────────────────────────────────


def test_card_and_list_show_the_transcript(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    assert client.get(f"/api/v1/assets/{ASSET}").json()["files"]["transcript"] is None
    put(client)
    link = f"/api/v1/assets/{ASSET}/transcript"
    assert client.get(f"/api/v1/assets/{ASSET}").json()["files"]["transcript"] == link
    assert client.get("/api/v1/assets").json()["assets"][0]["files"]["transcript"] == link


def test_delete_removes_the_row_and_the_file(client, login_as, settings):
    login_as()
    user_id = client.get("/api/v1/me").json()["id"]
    seed_asset(settings, user_id)
    put(client)
    path = asset_dir(settings, user_id, ASSET) / "transcript.json"
    assert path.is_file() and transcript_rows(settings) == 1

    assert client.delete(f"/api/v1/assets/{ASSET}/transcript").status_code == 204
    assert not path.exists() and transcript_rows(settings) == 0
    assert client.get(f"/api/v1/assets/{ASSET}/transcript").status_code == 404
    assert client.get(f"/api/v1/assets/{ASSET}").json()["files"]["transcript"] is None


def test_foreign_asset_is_404_everywhere(client, login_as, settings):
    login_as()
    ready_asset(client, settings)
    put(client)
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")

    assert client.post(f"/api/v1/assets/{ASSET}/transcribe", json={}).status_code == 404
    assert client.get(f"/api/v1/assets/{ASSET}/transcript").status_code == 404
    assert put(client).status_code == 404
    assert client.delete(f"/api/v1/assets/{ASSET}/transcript").status_code == 404
    # Чужой транскрипт цел: отказ отказом, а трогать чужое нельзя даже случайно.
    assert transcript_rows(settings) == 1


def test_agent_works_with_a_token(bearer_client, settings):
    """Сценарий агента: расшифровать, положить свой транскрипт, забрать субтитры, убрать за собой."""
    ready_asset(bearer_client, settings)
    assert bearer_client.post(f"/api/v1/assets/{ASSET}/transcribe", json={}).status_code == 202
    assert put(bearer_client).status_code == 200
    r = bearer_client.get(f"/api/v1/assets/{ASSET}/transcript", params={"format": "srt"})
    assert r.status_code == 200 and r.text.startswith("1\n")
    assert bearer_client.delete(f"/api/v1/assets/{ASSET}/transcript").status_code == 204


def test_transcript_requires_auth(client):
    assert client.get(f"/api/v1/assets/{ASSET}/transcript").status_code == 401
    assert client.post(f"/api/v1/assets/{ASSET}/transcribe", json={}).status_code == 401
