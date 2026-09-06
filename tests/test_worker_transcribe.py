"""Задание transcribe целиком: нарезка настоящим ffmpeg, провайдер подменён и в сеть не ходит."""
import json
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.storage import asset_dir
from server.app.transcribe.provider import ProviderError
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate
from server.media.run import MediaError
from server.worker import handlers
from server.worker.queue import STOPPING, claim_job
from tests.media_fixtures import FFMPEG, HAVE_FFMPEG

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="нужен ffmpeg в PATH")

USER = "usr_00000000000a"
ASSET = "ast_000000000001"

# Плотная карта пауз задана руками: здесь проверяется сборка обработчика, а не измерение тишин
# (оно живёт в test_media_audio). Края 3.0 и 5.0 обрамляют реплику, которую отдаёт подставной
# провайдер, — по ним верификация и подтягивает границы.
DENSE = [{"start": 2.4, "end": 3.0}, {"start": 5.0, "end": 5.6}]
SPEECH_START, SPEECH_END = 3.1, 4.9  # чуть внутри измеренных краёв: сдвиг должен быть видно


def reply() -> dict:
    """Ответ провайдера на один кусок: времена от начала куска, как у настоящего."""
    return {
        "segments": [{
            "start": SPEECH_START, "end": SPEECH_END, "text": "привет мир",
            "no_speech_prob": 0.1, "avg_logprob": -0.2, "compression_ratio": 1.1,
        }]
    }


class FakeProvider:
    """Подмена провайдера целиком: ни ключа, ни сети. behave может вернуть ответ или бросить отказ."""

    def __init__(self, behave=None) -> None:
        self.calls: list[str] = []
        self.languages: list[str | None] = []
        self._behave = behave

    def transcribe(self, data: bytes, filename: str, *, language: str | None = None) -> dict:
        assert data, "пустой кусок до провайдера доходить не должен"
        self.calls.append(filename)
        self.languages.append(language)
        return reply() if self._behave is None else self._behave(filename)


def install(monkeypatch, behave=None) -> FakeProvider:
    provider = FakeProvider(behave)

    @contextmanager
    def factory(_settings):
        yield provider

    monkeypatch.setattr(handlers, "transcribe_provider", factory)
    return provider


def make_tone(path: Path, *, seconds: int, mutes: list[tuple[int, int]]) -> Path:
    """Тон 16 кГц моно с провалами громкости: план режет по ним, как по настоящим паузам."""
    chain = ",".join(f"volume=enable='between(t,{a},{b})':volume=0" for a, b in mutes)
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-af", chain, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(path)],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture(scope="module")
def tones(tmp_path_factory) -> dict[str, Path]:
    """Два файла на весь модуль: генерация стоит секунды, а копия в каталог ассета — миллисекунды."""
    folder = tmp_path_factory.mktemp("tones")
    return {
        "short": make_tone(folder / "short.wav", seconds=12, mutes=[(8, 10)]),
        "long": make_tone(folder / "long.wav", seconds=75, mutes=[(29, 32), (59, 62)]),
    }


@pytest.fixture(autouse=True)
def forget_stopping():
    """Флаг остановки воркера глобальный: тест отмены обязан вернуть его на место."""
    yield
    STOPPING.clear()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", transcribe_api_key="k")


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


def prepare(conn, settings, source: Path, *, duration: float, status="ready",
            silences=(), dense=DENSE) -> Path:
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
        "has_audio, created_at, last_access_at) "
        "VALUES (?, ?, 'audio', 'a.wav', 'wav', 10, ?, ?, 1, ?, ?)",
        (ASSET, USER, status, duration, now_iso(), now_iso()),
    )
    folder = asset_dir(settings, USER, ASSET)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, folder / "source.wav")
    (folder / "analysis.json").write_text(
        json.dumps({
            "duration": duration, "speech_level_db": -20.0, "threshold_db": -36.0,
            "silences": list(silences), "silences_dense": list(dense),
        }),
        encoding="utf-8",
    )
    return folder


def take(conn, params=None):
    enqueue_job(conn, user_id=USER, type_="transcribe", target_id=ASSET, params=params)
    return claim_job(conn, lane="net", pid=1)


def read_transcript(folder: Path) -> dict:
    return json.loads((folder / "transcript.json").read_text(encoding="utf-8"))


def no_leftovers(folder: Path) -> bool:
    return not list(folder.glob("chunk-*")) and not (folder / handlers.WAV_NAME).exists()


class TestОтказыДоРаботы:
    def test_ассет_без_анализа_не_расшифровывается(self, conn, settings, tones, monkeypatch):
        folder = prepare(conn, settings, tones["short"], duration=12.0, status="uploaded")
        install(monkeypatch, lambda name: pytest.fail("отправлять нечего"))
        with pytest.raises(MediaError) as exc:
            handlers.handle_transcribe(conn, settings, take(conn))
        assert exc.value.reason == "asset_not_ready" and "анализ" in exc.value.message
        assert no_leftovers(folder)

    def test_пустой_ключ_выключает_транскрипцию(self, conn, settings, tones, monkeypatch):
        """Ключа нет — функции нет: ffmpeg не запускается вовсе, а не режет впустую."""
        folder = prepare(conn, settings, tones["short"], duration=12.0)
        install(monkeypatch, lambda name: pytest.fail("отправлять нечего"))
        for name in ("extract_wav", "run_streaming", "run_tool"):
            monkeypatch.setattr(handlers, name, lambda *a, **k: pytest.fail("ffmpeg звать незачем"))
        mute = settings.model_copy(update={"transcribe_api_key": ""})
        with pytest.raises(MediaError) as exc:
            handlers.handle_transcribe(conn, mute, take(conn))
        assert exc.value.reason == "transcription_unavailable"
        assert no_leftovers(folder)

    def test_без_карты_пауз_ошибка_понятная(self, conn, settings, tones, monkeypatch):
        folder = prepare(conn, settings, tones["short"], duration=12.0)
        (folder / "analysis.json").unlink()
        install(monkeypatch, lambda name: pytest.fail("отправлять нечего"))
        with pytest.raises(MediaError) as exc:
            handlers.handle_transcribe(conn, settings, take(conn))
        assert exc.value.reason == "no_analysis" and "analysis.json" in exc.value.message


class TestУспех:
    def test_транскрипт_на_диске_и_строка_в_базе(self, conn, settings, tones, monkeypatch):
        folder = prepare(conn, settings, tones["short"], duration=12.0, silences=[{"start": 8, "end": 10}])
        provider = install(monkeypatch)
        job = take(conn)
        handlers.handle_transcribe(conn, settings, job)

        data = read_transcript(folder)
        assert data["asset_id"] == ASSET and data["duration"] == 12.0
        assert data["model"] == settings.transcribe_model and data["language"] == "ru"
        assert data["silences_dense"] == DENSE
        segment = data["segments"][0]
        assert segment["id"] == 1 and segment["text"] == "привет мир" and segment["suspect"] is False
        # Границы подтянуты к измеренным краям речи: 3.1 → 3.0 и 4.9 → 5.0.
        assert (segment["start"], segment["end"]) == (3.0, 5.0)
        assert segment["start_verified"] is True and segment["end_verified"] is True
        assert [w["w"] for w in segment["words"]] == ["привет", "мир"]
        assert all(w["interpolated"] and 3.0 <= w["s"] < w["e"] <= 5.0 for w in segment["words"])
        assert data["stats"]["verified_pct"] == 100 and data["stats"]["adjusted"] == 1
        assert data["stats"]["chunks"] == 1 and data["stats"]["seams_fixed"] == 0

        row = conn.execute("SELECT * FROM transcripts").fetchone()
        assert row["asset_id"] == ASSET and row["user_id"] == USER and row["segments"] == 1
        assert row["language"] == "ru" and row["duration"] == 12.0
        # Кто расшифровал — одно и то же в файле и в базе, иначе по строке не найти транскрипт.
        assert row["provider"] == data["provider"] and row["model"] == data["model"]
        assert json.loads(row["stats"])["verified_pct"] == 100

        assert provider.calls == ["chunk-000.mp3"] and provider.languages == ["ru"]
        assert conn.execute("SELECT progress FROM jobs WHERE id = ?", (job["id"],)).fetchone()[0] == 1.0
        assert no_leftovers(folder)

    def test_язык_из_параметров_задания(self, conn, settings, tones, monkeypatch):
        prepare(conn, settings, tones["short"], duration=12.0)
        provider = install(monkeypatch)
        handlers.handle_transcribe(conn, settings, take(conn, params={"language": "en"}))
        assert provider.languages == ["en"]
        assert conn.execute("SELECT language FROM transcripts").fetchone()[0] == "en"

    def test_длинный_звук_режется_и_смещения_прибавлены(self, conn, settings, tones, monkeypatch):
        """Три куска по паузам; реплика второго обязана оказаться за границей между кусками."""
        folder = prepare(
            conn, settings, tones["long"], duration=75.0,
            silences=[{"start": 29, "end": 32}, {"start": 59, "end": 62}],
        )
        tuned = settings.model_copy(
            update={"transcribe_chunk_sec": 30, "transcribe_chunk_window_sec": 5}
        )
        provider = install(monkeypatch)
        handlers.handle_transcribe(conn, tuned, take(conn))

        # Порядок отправки не задан: куски уходят пачкой, важно, что ушли все.
        assert sorted(provider.calls) == ["chunk-000.mp3", "chunk-001.mp3", "chunk-002.mp3"]
        segments = read_transcript(folder)["segments"]
        assert len(segments) == 3
        # Границы кусков — середины пауз: 30.5 и 60.5. Смещение прибавлено ровно один раз.
        assert [s["start"] for s in segments] == [3.0, 30.5 + SPEECH_START, 60.5 + SPEECH_START]
        assert segments[2]["start"] > 60.5
        assert read_transcript(folder)["stats"]["chunks"] == 3
        assert no_leftovers(folder)

    def test_повторный_запуск_заменяет_строку(self, conn, settings, tones, monkeypatch):
        folder = prepare(conn, settings, tones["short"], duration=12.0)
        install(monkeypatch)
        handlers.handle_transcribe(conn, settings, take(conn))
        first = conn.execute("SELECT created_at FROM transcripts").fetchone()[0]
        handlers.handle_transcribe(conn, settings, take(conn))
        rows = conn.execute("SELECT created_at FROM transcripts").fetchall()
        assert len(rows) == 1 and rows[0][0] >= first
        assert read_transcript(folder)["segments"]

    def test_без_libmp3lame_режем_в_wav(self, conn, settings, tones, monkeypatch):
        """Сборка ffmpeg без кодека не должна ронять задание молча: WAV больше, но в предел влезает."""
        folder = prepare(conn, settings, tones["short"], duration=12.0)
        monkeypatch.setattr(handlers, "mp3_encoder_available", lambda _s: False)
        provider = install(monkeypatch)
        handlers.handle_transcribe(conn, settings, take(conn))
        assert provider.calls == ["chunk-000.wav"]
        assert read_transcript(folder)["segments"]
        assert no_leftovers(folder)


class TestОтказыПоХоду:
    def test_отмена_в_середине_не_оставляет_следов(self, conn, settings, tones, monkeypatch):
        """Останов воркера доходит до обработчика между кусками: транскрипта нет, мусора тоже."""
        folder = prepare(
            conn, settings, tones["long"], duration=75.0,
            silences=[{"start": 29, "end": 32}, {"start": 59, "end": 62}],
        )
        tuned = settings.model_copy(update={
            "transcribe_chunk_sec": 30, "transcribe_chunk_window_sec": 5, "transcribe_concurrency": 1,
        })

        def stop_after_first(name):
            STOPPING.set()
            return reply()

        provider = install(monkeypatch, stop_after_first)
        with pytest.raises(MediaError) as exc:
            handlers.handle_transcribe(conn, tuned, take(conn))
        assert exc.value.reason == "canceled"
        assert len(provider.calls) == 1
        assert not (folder / "transcript.json").exists() and no_leftovers(folder)
        assert conn.execute("SELECT count(*) FROM transcripts").fetchone()[0] == 0

    def test_отказ_на_одном_куске_валит_всё_задание(self, conn, settings, tones, monkeypatch):
        """Полутранскрипт хуже отсутствующего: по нему будут монтировать, не зная о дыре."""
        folder = prepare(
            conn, settings, tones["long"], duration=75.0,
            silences=[{"start": 29, "end": 32}, {"start": 59, "end": 62}],
        )
        tuned = settings.model_copy(
            update={"transcribe_chunk_sec": 30, "transcribe_chunk_window_sec": 5}
        )

        def fail_second(name):
            if name == "chunk-001.mp3":
                raise ProviderError("server", "провайдер ответил ошибкой (500)")
            return reply()

        install(monkeypatch, fail_second)
        with pytest.raises(MediaError) as exc:
            handlers.handle_transcribe(conn, tuned, take(conn))
        assert exc.value.reason == "transcribe_failed" and "chunk-001" in exc.value.message
        assert not (folder / "transcript.json").exists() and no_leftovers(folder)
        assert conn.execute("SELECT count(*) FROM transcripts").fetchone()[0] == 0

    def test_413_делит_кусок_пополам(self, conn, settings, tones, monkeypatch):
        """Кусок не приняли по размеру — режем пополам и отправляем обе половины."""
        folder = prepare(conn, settings, tones["short"], duration=12.0)

        def refuse_whole(name):
            if name == "chunk-000.mp3":
                raise ProviderError("too_large", "кусок не приняли по размеру (413)")
            return reply()

        provider = install(monkeypatch, refuse_whole)
        handlers.handle_transcribe(conn, settings, take(conn))

        assert provider.calls[0] == "chunk-000.mp3"
        assert sorted(provider.calls[1:]) == ["chunk-000a.mp3", "chunk-000b.mp3"]
        segments = read_transcript(folder)["segments"]
        assert len(segments) == 2 and segments[1]["start"] > 6.0  # реплика второй половины
        assert read_transcript(folder)["stats"]["chunks"] == 2
        assert conn.execute("SELECT count(*) FROM transcripts").fetchone()[0] == 1
        assert no_leftovers(folder)

    def test_половина_тоже_не_влезла_задание_падает(self, conn, settings, tones, monkeypatch):
        """Второго деления нет: если и половина велика, дело не в длине куска."""
        folder = prepare(conn, settings, tones["short"], duration=12.0)

        def refuse_all(name):
            raise ProviderError("too_large", "кусок не приняли по размеру (413)")

        provider = install(monkeypatch, refuse_all)
        with pytest.raises(MediaError) as exc:
            handlers.handle_transcribe(conn, settings, take(conn))
        assert exc.value.reason == "transcribe_failed"
        assert len(provider.calls) == 3  # целый кусок и обе половины
        assert not (folder / "transcript.json").exists() and no_leftovers(folder)
