# M4a. Транскрипция — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ ПОД-НАВЫК: выполнять этот план задача за задачей через
> superpowers:subagent-driven-development. Шаги помечены чекбоксами (`- [ ]`).

**Цель:** ассет с речью получает транскрипт: сегменты с текстом, словами и честными флагами подтверждения границ, плюс отдача в SRT и VTT. Всё серверное; панель транскрипта и субтитры из транскрипта — следующий этап M4b.

**Архитектура:** задание `transcribe` в полосе `net`. Воркер режет `audio16k.wav` на чанки по паузам, шлёт их в OpenAI-совместимый маршрут провайдера, склеивает результат одной функцией нормализации, чинит швы, клэмпит к длине аудио, верифицирует границы по плотной карте тишин из `analysis.json` и кладёт `transcript.json` рядом с ассетом.

**Технологии:** httpx (уже есть), ffmpeg, SQLite, pytest. Чистые функции отдельно от ввода-вывода — как в `server/media/render.py`.

Источник правды по решениям — раздел 10 спеки `docs/superpowers/specs/2026-09-03-video-editor-design.md`. Ниже он не пересказывается, а превращается в задачи.

---

## Что уже готово и на что опираемся

| | |
|---|---|
| Полоса `net` | `server/app/jobs.py:9` — `LANES = {…, "transcribe": "net"}`. Воркер её пока не разбирает: `LANE = "cpu"` зашита в `server/worker/__main__.py:33` |
| Карта пауз | Задание `analyze` уже пишет в `analysis.json` обе карты (`silences` от 0.5 с и `silences_dense` от 0.15 с), порог и уровень речи. Считать заново не нужно |
| Звук для транскрипции | `audio16k.wav` удаляется сразу после анализа (`handlers.py:124`, «пересоберём в M4, диск дороже») — задание `transcribe` собирает его заново из исходника |
| Снэп резов к паузам | `server/app/projects/snap.py` работает с M2a; транскрипт на него не влияет |
| Отмена и пульс | `should_stop`, `Heartbeat`, `run_streaming` — готовы, взяты как есть |

## Проверено в родственных проектах (не нами, но живьём)

Опыт плагинов `Extensions-LLM-Chat_Pr` (Premiere) и `Extensions-LLM-Chat` (After Effects), прочитано 2026-09-06:

| Что | Как выяснилось |
|---|---|
| Пословные таймкоды | Cloud.ru Whisper `openai/whisper-large-v3` их **не отдаёт**: параметр принимается молча, слов в ответе нет (проверено 2026-07-17 и 2026-09-02) |
| `language` | Без него `verbose_json` отвечает **400** — параметр обязателен (`hostBridge.js:243`) |
| Швы чанков | Сегмент на границе приходит дважды; `fixChunkBoundarySegments` подрезает начало к концу предыдущего, а остаток короче 0.3 с выбрасывает |
| Хвост за концом аудио | Whisper регулярно тянет последний сегмент за конец: окно 600–780 → конец 783.9. Нужен клэмп |
| Верификация границ | `verifySegmentBoundaries` с окнами 1.0 с в сторону расширения речи и 0.25 с в сторону сжатия; граница внутри паузы — точный ответ без окна |
| Параллельность | `promisePool(tasks, concurrency)` — чанки уходят пачкой, а не по одному |

Эти цифры уже перенесены в раздел 10 нашей спеки; план им следует.

## Решения по ходу планирования

**Две полосы — два потока в одном процессе.** Спека говорит «один процесс, две полосы». Транскрипция ждёт сеть десятками минут, и держать за ней очередь рендеров нельзя. Значит в `server/worker/__main__.py` два цикла в потоках, по одному на полосу, **у каждого своё соединение с базой**: `sqlite3.Connection` не потокобезопасен, и общее соединение здесь — гарантированная гонка, а не экономия.

**Ключ провайдера не блокирует работу.** Пока `VIDEO_TRANSCRIBE_API_KEY` пуст, запуск транскрипции отвечает `503 transcription_unavailable` с внятным текстом. Пустой ключ запрещает, а не «пробует и падает в сеть» — то же правило, что у служебных токенов кабинета.

**Транскрипт живёт файлом рядом с ассетом**, в базе только метаданные и статистика. Он большой (часовой разговор — сотни килобайт), а нужен целиком; хранить его в SQLite значит тащить блоб через каждый `SELECT *`.

**Один путь нормализации.** `normalize_chunk(result, offset)` — единственное место, где ко временам прибавляется смещение. В плагине именно дублирующий путь однажды потерял вычитание точки входа и сдвинул весь транскрипт (спека §10.3).

**Слова интерполируются, но резать по ним нельзя.** Провайдер слов не даёт, поэтому слова размечаются по слогам внутри сегмента и помечаются `interpolated: true`. Для резов есть `snap_to_pauses` по измеренным паузам — на этом стоит вся M2a.

---

## Структура файлов

| Файл | За что отвечает |
|---|---|
| `server/db/migrations/0008_transcripts.sql` | Таблица `transcripts`: метаданные и статистика |
| `server/media/transcribe.py` (создать) | Чистое: план нарезки, нормализация чанка, швы, клэмп, `suspect`, интерполяция слов |
| `server/media/verify.py` (создать) | Чистое: верификация границ по плотной карте тишин |
| `server/media/subs.py` (создать) | Чистое: транскрипт → SRT и VTT |
| `server/app/transcribe/provider.py` (создать) | HTTP-адаптер провайдера: multipart, коды, повторы, деление при 413 |
| `server/worker/handlers.py` | Обработчик `handle_transcribe` |
| `server/worker/__main__.py` | Вторая полоса потоком, соединение на поток |
| `server/app/assets/routes.py` | `POST /assets/{id}/transcribe`, `GET /assets/{id}/transcript`, `PUT /assets/{id}/transcript` |
| `server/app/config.py`, `.env.example` | Настройки провайдера и нарезки |
| `deploy/video-worker.service` | Ничего не меняется: полосы внутри одного процесса |

---

### Task 1: Таблица, настройки, вторая полоса

**Files:**
- Create: `server/db/migrations/0008_transcripts.sql`
- Modify: `server/app/config.py`, `.env.example`, `server/worker/__main__.py`
- Test: `tests/test_db_migrate.py`, `tests/test_config.py`, `tests/test_worker_loop.py`

- [ ] **Step 1: Миграция**

```sql
-- Транскрипт ассета. Сам текст лежит файлом transcript.json рядом с исходником: он большой,
-- нужен целиком, и в базе только мешал бы каждому SELECT.
CREATE TABLE transcripts (
    asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    language TEXT NOT NULL,
    duration REAL NOT NULL,
    segments INTEGER NOT NULL,
    stats TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX transcripts_user_idx ON transcripts(user_id, created_at DESC);
```

- [ ] **Step 2: Настройки**

В `server/app/config.py`, рядом с блоком рендера:

```python
    # Транскрипция (раздел 10 спеки). Пустой ключ запрещает запуск, а не пытается ходить в сеть.
    transcribe_base_url: str = "https://foundation-models.api.cloud.ru/v1"
    transcribe_api_key: str = ""
    transcribe_model: str = "openai/whisper-large-v3"
    transcribe_language: str = "ru"
    transcribe_chunk_sec: int = Field(default=600, ge=30, le=1800)
    transcribe_chunk_window_sec: int = Field(default=60, ge=0, le=300)
    transcribe_concurrency: int = Field(default=4, ge=1, le=20)
    transcribe_timeout_sec: int = Field(default=600, ge=30)
    transcribe_max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    transcribe_retries: int = Field(default=3, ge=1, le=10)
```

В `.env.example` — те же имена с префиксом `VIDEO_`, ключ пустым, с комментарием: «пустой ключ выключает транскрипцию; сервис отвечает 503, а не падает в сеть».

Тест в `tests/test_config.py`: умолчания на месте, пустой ключ по умолчанию.

- [ ] **Step 3: Вторая полоса потоком**

`server/worker/__main__.py`: `LANE = "cpu"` заменяется на список полос из настроек (`VIDEO_WORKER_LANES`, по умолчанию `cpu,net`). Главный цикл выносится в функцию `serve(lane)`, каждая полоса запускается своим потоком со **своим** соединением (`connect(settings.db_path)` внутри потока).

```python
def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    lanes = [lane.strip() for lane in settings.worker_lanes.split(",") if lane.strip()]
    threads = [threading.Thread(target=serve, args=(settings, lane), name=f"lane-{lane}", daemon=False)
               for lane in lanes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
```

Сигнал остановки общий: существующий обработчик выставляет флаг, оба цикла его читают.

Тест в `tests/test_worker_loop.py`: задание в полосе `net` подхватывается, задание в `cpu` — по-прежнему; два потока не берут одно и то же задание (проверяется на общей базе).

- [ ] **Step 4: Прогон и коммит**

Run: `uv run python -m pytest && uv run ruff check .`

```bash
git add server/db/migrations/0008_transcripts.sql server/app/config.py .env.example server/worker/__main__.py tests/
git commit -m "feat(transcribe): transcripts table, provider settings and the net lane"
```

---

### Task 2: Адаптер провайдера

**Files:**
- Create: `server/app/transcribe/__init__.py`, `server/app/transcribe/provider.py`
- Test: `tests/test_transcribe_provider.py`

- [ ] **Step 1: Тесты на подменённом транспорте**

```python
import httpx
import pytest

from server.app.config import Settings
from server.app.transcribe.provider import ProviderError, TranscribeProvider


def make_settings(**kw) -> Settings:
    return Settings(_env_file=None, **{"transcribe_api_key": "k", **kw})


def provider(handler, **kw) -> TranscribeProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TranscribeProvider(make_settings(**kw), client)


def test_sends_multipart_with_language_and_verbose_json():
    """Без language verbose_json отвечает 400 — проверено живьём в плагине."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        body = request.content.decode("utf-8", errors="replace")
        seen["fields"] = [name for name in ("model", "language", "response_format") if name in body]
        return httpx.Response(200, json={"segments": [{"start": 0.0, "end": 1.0, "text": "привет"}]})

    result = provider(handler).transcribe(b"\0" * 10, "chunk.mp3")
    assert seen["path"].endswith("/audio/transcriptions")
    assert seen["auth"] == "Bearer k"
    assert seen["fields"] == ["model", "language", "response_format"]
    assert result["segments"][0]["text"] == "привет"


def test_too_large_is_told_apart():
    """413 разбирается по статусу: тело успешного verbose_json содержит числа,
    и разбор тела ради классификации ошибки только вводит в заблуждение."""
    p = provider(lambda r: httpx.Response(413, text="too large"))
    with pytest.raises(ProviderError) as exc:
        p.transcribe(b"x", "chunk.mp3")
    assert exc.value.kind == "too_large"


@pytest.mark.parametrize("status,kind", [(401, "auth"), (429, "busy"), (500, "server")])
def test_error_kinds(status, kind):
    p = provider(lambda r: httpx.Response(status, text="no"))
    with pytest.raises(ProviderError) as exc:
        p.transcribe(b"x", "chunk.mp3")
    assert exc.value.kind == kind


def test_retries_on_busy_then_succeeds():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, text="wait")
        return httpx.Response(200, json={"segments": []})

    p = provider(handler, transcribe_retries=3)
    p.transcribe(b"x", "chunk.mp3")
    assert len(calls) == 3


def test_empty_key_refuses_before_the_network():
    called = []
    p = provider(lambda r: called.append(1), transcribe_api_key="")
    with pytest.raises(ProviderError) as exc:
        p.transcribe(b"x", "chunk.mp3")
    assert exc.value.kind == "unconfigured" and not called
```

- [ ] **Step 2: Реализация**

`TranscribeProvider(settings, client)` с единственным публичным методом `transcribe(data: bytes, filename: str) -> dict`:

- поля multipart: `file`, `model`, `language`, `response_format=verbose_json`, `temperature=0`;
- заголовок `Authorization: Bearer {key}`;
- пустой ключ → `ProviderError("unconfigured", …)` **до** запроса;
- коды: 413 → `too_large`, 401/403 → `auth`, 429 и 5xx → `busy`/`server` и повтор с задержкой (`transcribe_retries`, пауза 2 с × номер попытки), прочие 4xx → `bad_request`;
- сетевой сбой → `unavailable`, повторяется;
- тело разбирается только у успешного ответа; у ошибочного берётся первые 300 символов в текст ошибки;
- пауза между повторами делается через переданную функцию `sleep` (по умолчанию `time.sleep`), чтобы тесты не ждали.

- [ ] **Step 3: Прогон и коммит**

```bash
git add server/app/transcribe/ tests/test_transcribe_provider.py
git commit -m "feat(transcribe): openai-compatible provider adapter"
```

---

### Task 3: Нарезка на чанки

**Files:**
- Create: `server/media/transcribe.py` (часть 1)
- Test: `tests/test_media_transcribe.py` (часть 1)

- [ ] **Step 1: Тесты плана нарезки**

```python
from server.media.transcribe import chunk_plan


def test_short_audio_is_one_chunk():
    assert chunk_plan(duration=120.0, silences=[], target=600, window=60) == [(0.0, 120.0)]


def test_boundary_goes_to_the_nearest_pause():
    """Граница на паузе не рвёт слово пополам: в окне ±60 с от цели ищем ближайшую тишину."""
    silences = [{"start": 570.0, "end": 572.0}, {"start": 640.0, "end": 641.0}]
    plan = chunk_plan(duration=1200.0, silences=silences, target=600, window=60)
    assert plan[0] == (0.0, 571.0)  # середина паузы
    assert plan[1][0] == 571.0


def test_hard_cut_when_no_pause_in_window():
    plan = chunk_plan(duration=1200.0, silences=[], target=600, window=60)
    assert plan[0] == (0.0, 600.0) and plan[1] == (600.0, 1200.0)


def test_tail_is_not_a_crumb():
    """Огрызок в конце приклеивается к предыдущему чанку, а не едет отдельным запросом."""
    plan = chunk_plan(duration=610.0, silences=[], target=600, window=60)
    assert plan == [(0.0, 610.0)]
```

- [ ] **Step 2: Реализация `chunk_plan`**

```python
def chunk_plan(*, duration: float, silences: list[dict], target: int, window: int) -> list[tuple[float, float]]:
    """Границы чанков по паузам. Цель — target секунд; в пределах ±window ищем ближайшую паузу
    и режем по её середине, иначе режем жёстко. Хвост короче четверти цели приклеиваем к
    предыдущему чанку: отдельный запрос ради двадцати секунд не окупается."""
```

- [ ] **Step 3: Нарезка ffmpeg**

`chunk_args(settings, src, dst, start, end)` — аргументы `ffmpeg -ss … -to … -c:a libmp3lame -b:a 64k -ar 16000 -ac 1`. Формат MP3 (около 5 МБ на 10 минут при 64 кбит); при отсутствии кодека — WAV. Имя временного файла с суффиксом `.part` **не используется**: контейнер по такому имени не угадывается (грабли M1b), пишем сразу в `chunk-{n}.mp3`.

Тест на аргументах, без запуска.

- [ ] **Step 4: Интеграционный тест на настоящем звуке**

На сгенерированном ffmpeg файле (`sine` с паузами, 30 с): нарезка даёт файлы ненулевого размера, сумма длительностей равна исходной ±0.1 с. Пустой файл чанка (меньше 1 КБ) — ошибка нарезки, а не пустая речь.

- [ ] **Step 5: Прогон и коммит**

```bash
git add server/media/transcribe.py tests/test_media_transcribe.py
git commit -m "feat(transcribe): chunk plan on pauses and ffmpeg slicing"
```

---

### Task 4: Нормализация, швы, клэмп, слова

**Files:**
- Modify: `server/media/transcribe.py` (часть 2)
- Test: `tests/test_media_transcribe.py` (часть 2)

- [ ] **Step 1: Тесты**

```python
from server.media.transcribe import (
    clamp_segments, fix_seams, interpolate_words, mark_suspect, normalize_chunk,
)


def test_normalize_adds_the_offset_once():
    raw = {"segments": [{"start": 1.0, "end": 2.0, "text": " Привет  мир "}]}
    out = normalize_chunk(raw, offset=600.0)
    assert out == [{"start": 601.0, "end": 602.0, "text": "Привет мир"}]


def test_normalize_drops_empty_and_inverted():
    raw = {"segments": [{"start": 5.0, "end": 4.0, "text": "назад"}, {"start": 1.0, "end": 2.0, "text": "  "}]}
    assert normalize_chunk(raw, offset=0.0) == []


def test_seam_segment_is_trimmed_to_the_previous_end():
    """Фраза на границе приходит дважды: хвост в чанке N, голова в N+1."""
    segments = [{"start": 595.0, "end": 601.2, "text": "…"}, {"start": 600.0, "end": 604.0, "text": "…"}]
    fixed, stats = fix_seams(segments, boundaries=[600.0])
    assert fixed[1]["start"] == 601.2 and stats["fixed"] == 1


def test_seam_crumb_is_dropped():
    segments = [{"start": 595.0, "end": 601.2, "text": "…"}, {"start": 600.0, "end": 601.4, "text": "…"}]
    fixed, stats = fix_seams(segments, boundaries=[600.0])
    assert len(fixed) == 1 and stats["dropped"] == 1


def test_clamp_cuts_the_tail_beyond_the_audio():
    """Whisper регулярно тянет последний сегмент за конец аудио — проверено живьём в плагине."""
    segments = [{"start": 770.0, "end": 783.9, "text": "…"}]
    out = clamp_segments(segments, duration=780.0)
    assert out[0]["end"] == 780.0


def test_suspect_is_marked_not_deleted():
    segments = [{"start": 0.0, "end": 1.0, "text": "…", "no_speech_prob": 0.9}]
    assert mark_suspect(segments)[0]["suspect"] is True


def test_words_are_syllable_weighted_and_flagged():
    """Слова провайдер не отдаёт: раскладываем по слогам и честно помечаем."""
    seg = {"start": 0.0, "end": 4.0, "text": "Привет большой мир"}
    words = interpolate_words(seg, silences=[])
    assert [w["w"] for w in words] == ["Привет", "большой", "мир"]
    assert all(w["interpolated"] for w in words)
    assert words[0]["s"] == 0.0 and abs(words[-1]["e"] - 4.0) < 1e-6
    # «большой» длиннее «мир» по слогам, значит и по времени
    assert (words[1]["e"] - words[1]["s"]) > (words[2]["e"] - words[2]["s"])


def test_words_skip_measured_silences():
    """Слова не должны «говориться» в тишине: пауза внутри сегмента вырезается из раскладки."""
    seg = {"start": 0.0, "end": 6.0, "text": "раз два"}
    words = interpolate_words(seg, silences=[{"start": 2.0, "end": 4.0}])
    assert words[0]["e"] <= 2.0 + 1e-6 and words[1]["s"] >= 4.0 - 1e-6
```

- [ ] **Step 2: Реализация**

Все функции чистые, без ввода-вывода.

- `normalize_chunk(result, offset)` — единственный путь прибавления смещения (спека §10.3).
- `fix_seams(segments, boundaries)` — сегмент, начавшийся в пределах 0.05 с от границы и накрытый хвостом предыдущего, подрезается к концу предыдущего; остаток короче 0.3 с выбрасывается. Возвращает `(segments, {"fixed": n, "dropped": m})`.
- `clamp_segments(segments, duration)` — обрезка к `[0, duration]`, короче 0.05 с выбрасывается.
- `mark_suspect(segments)` — `no_speech_prob > 0.5`, `avg_logprob < −1`, `compression_ratio > 2.4` → `suspect: true`. Не удаляем: решать человеку.
- `interpolate_words(segment, silences)` — раскладка по слогам (гласные, цифра считается за две), минимум 1 слог, добавка 0.4 на слово; интервалы речи внутри сегмента = сегмент минус тишины ≥ 0.3 с. Приём взят из `Extensions-LLM-Chat/lib/pure/subtitles.js` (`alignWords`), где обкатан на часовых подкастах.

- [ ] **Step 3: Прогон и коммит**

```bash
git add server/media/transcribe.py tests/test_media_transcribe.py
git commit -m "feat(transcribe): normalization, chunk seams, clamp and word timing"
```

---

### Task 5: Верификация границ по паузам

**Files:**
- Create: `server/media/verify.py`
- Test: `tests/test_media_verify.py`

- [ ] **Step 1: Тесты**

```python
from server.media.verify import verify_segment_boundaries


def test_boundary_inside_a_pause_is_exact():
    """Граница внутри паузы — точный ответ без ограничения окна: речь возобновляется в её конце."""
    segments = [{"start": 5.0, "end": 9.0, "text": "…"}]
    silences = [{"start": 4.0, "end": 6.0}, {"start": 8.5, "end": 10.0}]
    out, stats = verify_segment_boundaries(segments, silences)
    assert out[0]["start"] == 6.0 and out[0]["start_verified"] is True
    assert out[0]["end"] == 8.5 and out[0]["end_verified"] is True


def test_never_squeezes_speech_more_than_a_quarter_second():
    """Окна несимметричны: расширять речь можно на секунду, сжимать — на четверть."""
    segments = [{"start": 10.0, "end": 20.0, "text": "…"}]
    silences = [{"start": 9.0, "end": 9.2}, {"start": 20.9, "end": 21.5}]
    out, _ = verify_segment_boundaries(segments, silences)
    assert out[0]["start"] == 9.2 and out[0]["end"] == 20.9


def test_unverifiable_boundary_is_left_alone():
    """«Проверить нечем» честнее, чем двигать вслепую."""
    segments = [{"start": 10.0, "end": 20.0, "text": "…"}]
    out, stats = verify_segment_boundaries(segments, [{"start": 100.0, "end": 101.0}])
    assert out[0]["start"] == 10.0 and out[0]["start_verified"] is False
    assert stats["verified_pct"] == 0


def test_snap_does_not_cross_into_the_next_segment():
    segments = [{"start": 0.0, "end": 5.0, "text": "…"}, {"start": 5.4, "end": 9.0, "text": "…"}]
    out, _ = verify_segment_boundaries(segments, [{"start": 5.6, "end": 6.0}])
    assert out[0]["end"] <= 5.4


def test_zero_start_is_not_touched():
    segments = [{"start": 0.0, "end": 5.0, "text": "…"}]
    out, _ = verify_segment_boundaries(segments, [{"start": 0.0, "end": 0.5}])
    assert out[0]["start"] == 0.0


def test_stats_report_the_work_done():
    segments = [{"start": 5.0, "end": 9.0, "text": "…"}]
    silences = [{"start": 4.0, "end": 6.0}, {"start": 8.5, "end": 10.0}]
    _, stats = verify_segment_boundaries(segments, silences)
    assert stats["verified_pct"] == 100 and stats["adjusted"] == 1 and stats["max_drift"] > 0
```

- [ ] **Step 2: Реализация**

`verify_segment_boundaries(segments, dense_silences, *, wide=1.0, narrow=0.25, min_seg=0.05)` возвращает `(segments, stats)`; правило «никогда не сжимать речь» и остальные условия — спека §10.5. Поиск ближайшего края речи: пауза, накрывающая границу, даёт точный ответ; иначе ближайший край в асимметричном окне. Порядок сегментов не ломается: конец не заходит за начало следующего, начало не заходит за подтверждённый конец предыдущего.

- [ ] **Step 3: Прогон и коммит**

```bash
git add server/media/verify.py tests/test_media_verify.py
git commit -m "feat(transcribe): verify segment boundaries against measured pauses"
```

---

### Task 6: Задание transcribe в воркере

**Files:**
- Modify: `server/worker/handlers.py`
- Test: `tests/test_worker_transcribe.py`

- [ ] **Step 1: Тесты**

Провайдер подменяется целиком (фабрика в модуле обработчика), ffmpeg — настоящий на коротком сгенерированном файле.

Проверяется:
1. Ассет без анализа (`status` ниже `ready`) → задание падает с внятной ошибкой, а не молча.
2. Пустой ключ провайдера → задание падает с `transcription_unavailable`, чанки не режутся.
3. Успешный путь: `transcript.json` появился, в базе строка, `stats` заполнены, прогресс дошёл до 1.0, временные чанки удалены.
4. Отмена в середине: файлы чанков убраны, `transcript.json` не создан.
5. Провайдер отвечает 413 → чанк делится пополам и уходит заново (проверяется по числу вызовов).
6. Часть чанков не удалась после всех повторов → задание падает целиком: полутранскрипт хуже отсутствующего, потому что по нему монтируют.

- [ ] **Step 2: Реализация `handle_transcribe`**

Порядок: проверить ассет и ключ → собрать `audio16k.wav` из исходника → прочитать `analysis.json` → `chunk_plan` → нарезать → отправить чанки пулом (`transcribe_concurrency`) → `normalize_chunk` каждому → объединить, отсортировать → `fix_seams` → `clamp_segments` → `mark_suspect` → `verify_segment_boundaries` → `interpolate_words` → записать `transcript.json` во временный файл и переименовать → строка в `transcripts` → убрать `audio16k.wav` и чанки.

Прогресс: доля отправленных чанков до 0.9, остальное на сборку. Пульс — существующий `Heartbeat`. Отмена проверяется между чанками и внутри ffmpeg через `should_stop`.

- [ ] **Step 3: Прогон и коммит**

```bash
git add server/worker/handlers.py tests/test_worker_transcribe.py
git commit -m "feat(worker): transcribe job with chunked upload and verification"
```

---

### Task 7: API транскрипта

**Files:**
- Modify: `server/app/assets/routes.py`
- Create: `server/media/subs.py`
- Test: `tests/test_transcript_api.py`, `tests/test_media_subs.py`

- [ ] **Step 1: SRT и VTT из транскрипта (чистое)**

```python
def to_srt(transcript: dict) -> str: ...
def to_vtt(transcript: dict) -> str: ...
```

Тесты: нумерация с единицы, время `00:00:01,230` для SRT и `00:00:01.230` для VTT, заголовок `WEBVTT`, перевод строки между репликами, пустой транскрипт даёт корректный пустой файл, текст экранируется по правилам формата.

- [ ] **Step 2: Ручки**

- `POST /api/v1/assets/{id}/transcribe` `{language?}` → `202 {job_id}`; `409` если транскрипт уже есть (перезапуск — только после удаления), `422` если ассет не готов, `503 transcription_unavailable` при пустом ключе.
- `GET /api/v1/assets/{id}/transcript?format=json|srt|vtt` → транскрипт целиком; `404`, если его нет.
- `PUT /api/v1/assets/{id}/transcript` — свой транскрипт в нашем формате: проверка формата и клэмп, **без** швов и верификации (спека §10.7); `stats.source = "uploaded"`.
- `DELETE /api/v1/assets/{id}/transcript`.

Тесты: чужой ассет — 404 во всех четырёх; агентский токен работает (это его сценарий); формат `srt` отдаёт `text/plain` с корректным телом.

- [ ] **Step 3: Прогон и коммит**

```bash
git add server/app/assets/routes.py server/media/subs.py tests/
git commit -m "feat(api): transcript endpoints and srt/vtt export"
```

---

### Task 8: Документация, выкатка, живая проверка

**Files:**
- Modify: `README.md`, `tools/agent_smoke.py`

- [ ] **Step 1: README** — раздел «Транскрипция (M4a)»: ручки, формат, что провайдер не даёт слов и что резать надо через `snap_to_pauses`, пустой ключ выключает функцию.

- [ ] **Step 2: Смоук** — в `tools/agent_smoke.py` добавить шаг: запустить транскрипцию, дождаться, прочитать транскрипт, напечатать долю подтверждённых границ. Шаг пропускается с внятной строкой, если ключ не настроен.

- [ ] **Step 3: Слияние и выкатка** (координатор)

- [ ] **Step 4: Живая проверка** (координатор)

1. Ключ провайдера положен в `/opt/editing-site/.env` **владельцем** (у него он уже есть для плагина Premiere), сервис перезапущен.
2. Настоящая запись с речью на 10–20 минут: транскрипт собрался, доля подтверждённых границ и число сдвигов попали в `stats`.
3. Сверить на слух три случайных сегмента: текст соответствует, границы не режут слово.
4. Швы: на записи длиннее одного чанка убедиться, что фраза на границе не задвоилась.
5. Отмена на середине: задание `canceled`, чанки с диска убраны.
6. Замер: сколько заняла транскрипция и сколько при этом отвечал эфир соседа (как в M3).
7. Проверить, что рендер в это же время не встал в очередь за транскрипцией — полосы работают параллельно.

---

## Что остаётся на M4b

Панель транскрипта в браузере (клик по слову перематывает, выделение создаёт клип со снэпом), субтитры из транскрипта с пересчётом слов через клипы и сборкой реплик по типографским правилам, кэш `subs/{version}.srt|.vtt`.

**Типографику для реплик берём из `Extensions-LLM-Chat/lib/pure/subtitles.js`** — она обкатана на часовых подкастах и сильно богаче того абзаца, что сейчас в спеке §10.9: реплика не кончается предлогом или союзом (слово уезжает в следующую), строка тоже не кончается ими, нет разрыва между числом и словом, не переносится после открывающей кавычки, строки уравновешены по длине, при равенстве верхняя короче, точка и запятая на конце реплики снимаются, а вопросительный и восклицательный знаки остаются. Это стоит внести в спеку отдельной правкой перед M4b.

## Поправки по ходу выполнения

- **Контракт провайдера проверен живьём 2026-09-06** нашим же адаптером и ключом владельца (`secrets/.env`, поле `apiKey`). Ответ `200`, верхние ключи `duration, language, segments, text, words`; поля сегмента — `avg_logprob, compression_ratio, end, id, no_speech_prob, seek, start, temperature, text, tokens`, **пословных таймкодов нет**; верхнее поле `words` приходит пустым. Запрос **без `language` отвечает 400**. Все три факта, на которых стоит план, подтвердились.
- **Задача 3.** Запасной путь «нет libmp3lame → WAV» не сделан: чистой функцией его не выразить, нужен опрос `ffmpeg -encoders`. Решается в обработчике воркера (задача 6).
- **Задача 4.** В исходном приёме из `subtitles.js` нашлась настоящая ошибка: начало и конец слова считались одной функцией, и слово сразу после паузы получало начало **в** тишине. Разведено на два режима — конец слова садится на край паузы, начало уходит за неё.
- **Задача 4.** Загруженный чужой транскрипт придёт с настоящими пословными таймкодами и без флага `interpolated`. Значит проверка «есть `words` → можно резать» неверна: резать можно только когда `interpolated is not True`.
- **Задача 5.** В исходной верификации нашлись три дыры: пустая карта пауз возвращала сегменты вообще без флагов, `verified_pct` делился на ноль на пустом транскрипте, вырожденные сегменты уходили без флагов. Все три закрыты.
- **Задача 7.** Загрузка своего транскрипта отбивается, пока идёт наше задание: иначе доехавший воркер молча перезаписал бы чужой файл. Слова чужого транскрипта прижимаются к своему сегменту — именно они считаются настоящими и разрешёнными для резов.
- **Живая проверка на боевом 2026-09-06.** Сквозной сценарий агента с расшифровкой прошёл целиком: 12 минут → 24 сегмента, 48 % границ подтверждено, SRT из 24 реплик. Отдельный прогон на 20 минутах поднял многочанковый путь: `chunks: 2`, `seams_fixed: 1`, хвоста за концом аудио нет, 21 сегмент после границы 600 с — смещения прибавлены верно.
- **Про `max_drift` 3.973 с.** Это не промах верификации, а свойство материала: у синтетической записи паузы по четыре секунды, и граница, попавшая внутрь измеренной тишины, получает точный ответ без ограничения окна (спека §10.5). На настоящей речи паузы 0.5–2 с, и сдвиг будет соответственно меньше.
- **Что осталось непокрытым.** Нахлёсты соседних сегментов, пришедшие от самого Whisper внутри чанка, верификация не чинит — только не создаёт новых (в прогоне на 20 минутах остался один). Проверка текста на слух — за настоящим материалом: синтетический тон Whisper расшифровывает выдумками, и содержание проверять нечем.

- **Задача 5, замер на синтетическом часе:** 615 сегментов против 1211 плотных пауз — 38 мс. Границы ровно на целой секунде 61/59 % → 19/22 %, медиана расстояния до края речи 0.224 → 0.0 с. Ориентир из плагина воспроизвёлся.
