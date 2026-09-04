# M1b: воркер, анализ, полоска кадров, прокси

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** загруженный файл сам доходит до состояния, в котором его можно монтировать: измерены длительность и кодеки, посчитаны звуковые пики и карта пауз, собрана полоска кадров, сделан прокси для плеера. Статусы `uploaded` → `analyzing` → `ready` → `proxy_ready` меняет отдельный процесс-воркер, интерфейс показывает прогресс и играет прокси.

**Architecture:** новый пакет `server/media/` (тонкие обёртки над ffmpeg и ffprobe плюс чистые разборщики их вывода) и `server/worker/` (выбор задания из таблицы `jobs`, пульс, отмена, обработчики `analyze` и `proxy`). Воркер отдельный systemd-сервис `video-worker`, одна полоса `cpu`, одно задание за раз. Всё тяжёлое считает ffmpeg: пики берутся из фильтра `astats` окнами по 20 мс, паузы из `silencedetect` двумя проходами, полоска из `fps` + `tile`. В Python только разбор текста, никакой поэлементной обработки семплов (модуль `audioop` удалён в Python 3.13, numpy в зависимости не берём).

**Tech Stack:** Python 3.12+ (`subprocess`, stdlib), ffmpeg 6+/8 с `libx264` и `aac`, SQLite (`UPDATE … RETURNING`), systemd, Vite + TypeScript.

**Спека:** `docs/superpowers/specs/2026-09-03-video-editor-design.md`, разделы 3, 7, 9.1, 9.3, 12, 14. **Предыдущий план:** `docs/superpowers/plans/2026-09-04-m1a-uploads-assets.md` (загрузка, ассеты, janitor, раздача файлов).

---

## Проверено на живом ffmpeg перед написанием плана (2026-09-04)

| Что | Результат |
|---|---|
| `astats` с `asetnsamples=n=320` | 50 значений `Peak_level` в секунду, в тишине печатает `-inf` |
| `silencedetect` | пишет `silence_start:`/`silence_end:`/`silence_duration:` в stderr на уровне `-v info` |
| `fps=1/2,scale=160:-2,tile=10x2` при 6 кадрах | отдаёт полный кадр сетки 1600×180, пустые клетки чёрные |
| `-progress pipe:1 -nostats` | строки `out_time_us=…`, в конце `progress=end` |
| прокси 640 px, `veryfast`, CRF 28, `-g 30` | собирается, `+faststart` работает |

## Решения M1b

| Вопрос | Решение | Почему |
|---|---|---|
| Пики | `astats` окнами 20 мс, значения в дБ → амплитуда 0..255 | Без numpy и без `audioop` (удалён в 3.13); Python только парсит текст |
| Уровень речи | медиана самых громких 2 % окон RMS 50 мс | Абсолютный порог не годится: тихий гость и шумная запись (раздел 10.4 спеки) |
| Порог тишины | уровень речи минус 16 дБ, не ниже −60 дБ; если пауз нет и порог выше −55 дБ, повтор на 10 дБ ниже | Правило из плагина Premiere, перенесено в спеку |
| Полоска | `fps=1/интервал` + `tile`, интервал 2 с, кадр 160 px, сетка 10 колонок, не больше 600 кадров | Один проход ffmpeg, предсказуемая раскладка |
| Ключевые кадры для полоски | не используем `-skip_frame nokey` | С `fps` он даёт неравномерные интервалы; полный декод 640-пиксельного потока дешевле путаницы |
| Прогресс | только у `proxy` (`-progress pipe:1`) | `analyze` состоит из коротких шагов, прогресс ставится по шагам: 0.2 / 0.5 / 0.8 |
| Отмена | воркер проверяет статус задания при каждом пульсе, шлёт процессу `SIGTERM`, через 10 с `SIGKILL` | Раздел 9.3 спеки |
| Одно задание за раз | цикл в один поток, пульс из отдельного потока-демона | Слабая VM: параллелить нечего |
| Выбор задания | `UPDATE … WHERE id = (SELECT … ORDER BY priority DESC, created_at) RETURNING` | Индекс `jobs_queue_idx` уже под этот порядок (M1a) |
| Честность к пользователям | среди заданий с одинаковым приоритетом первым берётся пользователь, чьё последнее задание закончилось раньше | Один человек не занимает воркер на час |
| Таймауты | `analyze` 30 мин, `proxy` 4 ч | Часовая запись из 4K идёт около часа (замер) |
| Ассет без звука | `analyze` доходит до `ready`, `peaks.json` и `analysis.json` пустые | Немое видео монтируется так же |
| Ассет-субтитры | воркер не трогает, статус `ready` ставится при загрузке | Уже сделано в M1a |
| Провал `analyze` | статус `failed`, текст ошибки в карточке | Пользователь должен видеть, что файл битый |
| `/healthz` | отсутствие пульса по-прежнему не деградация, устаревший дольше 2 минут — деградация | Иначе первый деплой падает до старта воркера |

## Структура файлов

| Файл | Обязанность |
|---|---|
| `server/app/config.py` | + настройки ffmpeg, таймауты, параметры полоски и пиков |
| `server/media/__init__.py` | пустой |
| `server/media/run.py` | запуск ffmpeg/ffprobe: `run_tool`, `MediaError`, хвост stderr |
| `server/media/probe.py` | `parse_probe` (чистая), `probe_file` |
| `server/media/audio.py` | извлечение WAV, разбор `astats` и `silencedetect`, пики, уровень речи, порог, карты пауз |
| `server/media/thumbs.py` | раскладка сетки, команда полоски, метаданные |
| `server/media/proxy.py` | команда прокси для видео и звука, разбор `-progress` |
| `server/worker/__init__.py`, `queue.py`, `handlers.py`, `__main__.py` | выбор задания, пульс, отмена, обработчики, цикл |
| `server/app/health.py` | без изменений логики, только тест на семантику пульса |
| `deploy/video-worker.service`, `deploy/deploy.sh`, `deploy/bootstrap.sh` | юнит воркера и его установка |
| `web/src/assets.ts`, `web/src/player.ts` | плеер прокси и длительность в списке |
| `tests/test_media_*.py`, `tests/test_worker_*.py`, `tests/test_media_integration.py` | тесты |

Команды: `uv run python -m pytest` (не `uv run pytest`), `uv run ruff check .`, `cd web && npm test && npm run build`. Ветка: `m1b-worker-analyze` от `main`.

---

### Task 1: Настройки, запуск ffmpeg, разбор ffprobe

**Files:**
- Modify: `server/app/config.py`, `.env.example`
- Create: `server/media/__init__.py` (пустой), `server/media/run.py`, `server/media/probe.py`
- Test: `tests/test_media_probe.py`

- [ ] **Step 1: Тесты разбора и запуска**

Создать `tests/test_media_probe.py`:

```python
import json

import pytest

from server.app.config import Settings
from server.media.probe import MediaInfo, parse_probe, probe_args
from server.media.run import MediaError, run_tool, tail_lines

VIDEO_JSON = {
    "format": {"duration": "12.000000", "size": "2789362"},
    "streams": [
        {
            "codec_type": "video", "codec_name": "h264", "width": 640, "height": 360,
            "avg_frame_rate": "25/1", "r_frame_rate": "25/1", "duration": "12.0",
        },
        {"codec_type": "audio", "codec_name": "aac", "channels": 1, "duration": "12.0"},
    ],
}


def test_parse_video_with_sound():
    info = parse_probe(VIDEO_JSON)
    assert info == MediaInfo(
        duration=12.0, width=640, height=360, fps=25.0, has_audio=True,
        video_codec="h264", audio_codec="aac",
    )


def test_parse_audio_only():
    info = parse_probe({"format": {"duration": "3.5"}, "streams": [
        {"codec_type": "audio", "codec_name": "mp3", "channels": 2},
    ]})
    assert info.width is None and info.height is None and info.fps is None
    assert info.has_audio is True and info.video_codec is None and info.audio_codec == "mp3"
    assert info.duration == 3.5


def test_parse_video_without_sound():
    info = parse_probe({"format": {"duration": "1"}, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 2, "height": 2, "avg_frame_rate": "0/0"},
    ]})
    assert info.has_audio is False and info.fps is None


def test_duration_falls_back_to_stream():
    info = parse_probe({"format": {}, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 2, "height": 2,
         "avg_frame_rate": "30000/1001", "duration": "4.25"},
    ]})
    assert info.duration == 4.25
    assert info.fps == pytest.approx(29.97, abs=0.01)


def test_cover_art_is_not_video():
    """Обложка mp3 приходит видеопотоком: считаем такой файл звуком, а не видео."""
    info = parse_probe({"format": {"duration": "10"}, "streams": [
        {"codec_type": "video", "codec_name": "mjpeg", "width": 300, "height": 300,
         "avg_frame_rate": "0/0", "disposition": {"attached_pic": 1}},
        {"codec_type": "audio", "codec_name": "mp3", "channels": 2},
    ]})
    assert info.width is None and info.video_codec is None and info.has_audio is True


def test_broken_file_raises():
    with pytest.raises(MediaError) as e:
        parse_probe({"format": {"duration": "0"}, "streams": []})
    assert e.value.reason == "no_streams"
    with pytest.raises(MediaError):
        parse_probe({"format": {"duration": "nonsense"}, "streams": [
            {"codec_type": "audio", "codec_name": "mp3"},
        ]})


def test_probe_args_asks_for_json_only():
    args = probe_args(Settings(_env_file=None), "/x/source.mp4")
    assert args[0] == "ffprobe"
    assert "-print_format" in args and "json" in args
    assert args[-1] == "/x/source.mp4"


def test_tail_lines_keeps_the_end():
    assert tail_lines("a\nb\nc\nd", 2) == "c\nd"
    assert tail_lines("", 5) == ""
    assert tail_lines("одна строка", 5) == "одна строка"


def test_run_tool_reports_exit_code(tmp_path):
    with pytest.raises(MediaError) as e:
        run_tool(["python", "-c", "import sys; sys.stderr.write('плохо\\n'); sys.exit(3)"], timeout=30)
    assert e.value.reason == "tool_failed"
    assert "плохо" in e.value.stderr
    out = run_tool(["python", "-c", "print('привет')"], timeout=30)
    assert out.strip() == "привет"


def test_run_tool_timeout():
    with pytest.raises(MediaError) as e:
        run_tool(["python", "-c", "import time; time.sleep(5)"], timeout=0.3)
    assert e.value.reason == "timeout"
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_media_probe.py`
Expected: FAIL, нет пакета `server.media`.

- [ ] **Step 3: Настройки**

В `server/app/config.py` после блока хранения добавить:

```python
    # Обработка медиа. Пути к бинарям берутся из PATH, если не заданы явно.
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    worker_poll_sec: float = Field(default=2.0, ge=0.1, le=60.0)
    analyze_timeout_sec: int = Field(default=1800, ge=10)
    proxy_timeout_sec: int = Field(default=14400, ge=10)
    peaks_per_sec: int = Field(default=50, ge=1, le=200)
    thumb_width: int = Field(default=160, ge=32, le=640)
    thumb_interval_sec: float = Field(default=2.0, gt=0)
    thumb_max_frames: int = Field(default=600, ge=1, le=5000)
    thumb_cols: int = Field(default=10, ge=1, le=50)
    proxy_long_side: int = Field(default=640, ge=160, le=1920)
    silence_min_sec: float = Field(default=0.5, gt=0)
    silence_dense_min_sec: float = Field(default=0.15, gt=0)
    speech_offset_db: float = Field(default=16.0, ge=1.0, le=60.0)
```

В `.env.example` перед `VIDEO_LOG_LEVEL`:

```
# Обработка медиа (воркер). Пути пустые = искать в PATH.
VIDEO_FFMPEG_PATH=ffmpeg
VIDEO_FFPROBE_PATH=ffprobe
VIDEO_WORKER_POLL_SEC=2
VIDEO_ANALYZE_TIMEOUT_SEC=1800
VIDEO_PROXY_TIMEOUT_SEC=14400
VIDEO_PROXY_LONG_SIDE=640
```

- [ ] **Step 4: Запуск инструментов**

Создать пустой `server/media/__init__.py` и `server/media/run.py`:

```python
"""Запуск ffmpeg и ffprobe. Наружу только текст: ошибка несёт короткий хвост stderr и причину,
которую можно показать пользователю в карточке ассета.
"""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("video.media")

STDERR_TAIL_LINES = 50


class MediaError(Exception):
    def __init__(self, reason: str, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.stderr = stderr


def tail_lines(text: str, count: int = STDERR_TAIL_LINES) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-count:])


def run_tool(args: list[str], *, timeout: float, capture_stderr: bool = False) -> str:
    """Запускает инструмент и возвращает stdout. При ненулевом коде или таймауте бросает MediaError.

    capture_stderr=True возвращает stderr вместо stdout: silencedetect пишет находки именно туда.
    """
    log.debug("run: %s", " ".join(args[:6]))
    try:
        proc = subprocess.run(  # noqa: S603
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise MediaError("tool_missing", f"Не найден {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("timeout", f"{args[0]} не уложился в {timeout:.0f} с") from exc
    if proc.returncode != 0:
        raise MediaError(
            "tool_failed",
            f"{args[0]} завершился с кодом {proc.returncode}",
            tail_lines(proc.stderr or ""),
        )
    return proc.stderr if capture_stderr else proc.stdout
```

- [ ] **Step 5: Разбор ffprobe**

Создать `server/media/probe.py`:

```python
"""ffprobe: параметры файла. Разбор ответа отделён от запуска, поэтому проверяется на фикстурах."""
from __future__ import annotations

import json
from dataclasses import dataclass

from server.app.config import Settings
from server.media.run import MediaError, run_tool

PROBE_TIMEOUT_SEC = 60


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None

    @property
    def kind(self) -> str:
        return "video" if self.video_codec else "audio"


def _fps(stream: dict) -> float | None:
    """avg_frame_rate вида «30000/1001»; «0/0» у обложек и части контейнеров."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = str(stream.get(key) or "")
        if "/" not in value:
            continue
        num, den = value.split("/", 1)
        try:
            num_f, den_f = float(num), float(den)
        except ValueError:
            continue
        if den_f > 0 and num_f > 0:
            return round(num_f / den_f, 3)
    return None


def _is_cover(stream: dict) -> bool:
    """Обложка звукового файла приходит видеопотоком: у неё стоит attached_pic."""
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def _duration(data: dict, video: dict | None, audio: dict | None) -> float:
    for source in (data.get("format") or {}, video or {}, audio or {}):
        raw = source.get("duration")
        if raw in (None, "", "N/A"):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return round(value, 3)
    raise MediaError("no_duration", "Не удалось определить длительность файла")


def parse_probe(data: dict) -> MediaInfo:
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and not _is_cover(s)), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise MediaError("no_streams", "В файле нет ни видео, ни звука")
    return MediaInfo(
        duration=_duration(data, video, audio),
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        fps=_fps(video) if video else None,
        has_audio=audio is not None,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
    )


def probe_args(settings: Settings, path: str) -> list[str]:
    return [
        settings.ffprobe_path, "-v", "error",
        "-print_format", "json", "-show_format", "-show_streams", path,
    ]


def probe_file(settings: Settings, path: str) -> MediaInfo:
    raw = run_tool(probe_args(settings, path), timeout=PROBE_TIMEOUT_SEC)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MediaError("bad_probe", "ffprobe вернул не JSON") from exc
    return parse_probe(data)
```

- [ ] **Step 6: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_media_probe.py && uv run ruff check .`
Expected: PASS, `All checks passed!`. Если ruff требует `# noqa` на `subprocess.run`, оставить как в коде выше.

- [ ] **Step 7: Commit**

```bash
git add server/app/config.py .env.example server/media tests/test_media_probe.py
git commit -m "feat(media): ffmpeg runner and ffprobe parsing"
```

---

### Task 2: Звук: пики, уровень речи, карты пауз

**Files:**
- Create: `server/media/audio.py`
- Test: `tests/test_media_audio.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_media_audio.py`:

```python
import math

import pytest

from server.app.config import Settings
from server.media.audio import (
    SILENCE_FLOOR_DB,
    db_to_amplitude,
    parse_levels,
    parse_silences,
    peaks_from_levels,
    silence_threshold_db,
    speech_level_db,
    wav_args,
)

ASTATS = """frame:0    pts:0       pts_time:0
lavfi.astats.Overall.Peak_level=-17.792199
frame:1    pts:320     pts_time:0.02
lavfi.astats.Overall.Peak_level=-inf
frame:2    pts:640     pts_time:0.04
lavfi.astats.Overall.Peak_level=0.000000
"""

SILENCE_LOG = """[silencedetect @ 0x1] silence_start: 3.018625
[silencedetect @ 0x1] silence_end: 6.014 | silence_duration: 2.995375
[silencedetect @ 0x1] silence_start: 9.5
"""


def test_parse_levels_handles_silence_and_full_scale():
    assert parse_levels(ASTATS) == [-17.792199, -math.inf, 0.0]
    assert parse_levels("мусор без чисел") == []


def test_db_to_amplitude():
    assert db_to_amplitude(0.0) == 255
    assert db_to_amplitude(-math.inf) == 0
    assert db_to_amplitude(-6.0) == pytest.approx(128, abs=2)
    assert db_to_amplitude(-100.0) == 0


def test_peaks_are_bytes_in_order():
    peaks = peaks_from_levels([0.0, -math.inf, -6.0])
    assert peaks == [255, 0, db_to_amplitude(-6.0)]
    assert all(0 <= p <= 255 for p in peaks)


def test_speech_level_is_median_of_the_loudest_windows():
    quiet = [-40.0] * 98
    loud = [-12.0, -10.0]
    assert speech_level_db(quiet + loud) == pytest.approx(-11.0, abs=0.5)
    assert speech_level_db([]) is None
    assert speech_level_db([-math.inf] * 10) is None


def test_threshold_follows_speech_and_has_a_floor():
    assert silence_threshold_db(-20.0, offset=16.0) == -36.0
    assert silence_threshold_db(-50.0, offset=16.0) == SILENCE_FLOOR_DB
    assert silence_threshold_db(None, offset=16.0) == -35.0  # запасной абсолютный порог


def test_parse_silences_pairs_starts_and_ends():
    assert parse_silences(SILENCE_LOG, duration=12.0) == [
        {"start": 3.019, "end": 6.014},
        {"start": 9.5, "end": 12.0},
    ]
    assert parse_silences("", duration=5.0) == []


def test_parse_silences_clamps_to_duration_and_drops_empty():
    log = "silence_start: 4.9\nsilence_end: 20.0 | silence_duration: 15\n"
    assert parse_silences(log, duration=5.0) == [{"start": 4.9, "end": 5.0}]
    assert parse_silences("silence_start: 5.0\n", duration=5.0) == []


def test_wav_args_are_16k_mono():
    args = wav_args(Settings(_env_file=None), "/x/source.mp4", "/x/audio16k.wav")
    assert "-ar" in args and "16000" in args
    assert "-ac" in args and "1" in args
    assert args[-1] == "/x/audio16k.wav"
    assert "-vn" in args
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_media_audio.py`
Expected: FAIL, нет модуля `server.media.audio`.

- [ ] **Step 3: Реализация**

Создать `server/media/audio.py`:

```python
"""Звук: WAV 16 кГц, пики, уровень речи и карты пауз.

Считает всё ffmpeg, Python только разбирает текст: поэлементная обработка семплов в Python слишком
медленная, numpy в зависимости не берём, а audioop удалён в Python 3.13.
"""
from __future__ import annotations

import math
import re
import statistics

from server.app.config import Settings
from server.media.run import run_tool

WAV_TIMEOUT_SEC = 3600
RMS_WINDOW_SEC = 0.05  # окно для оценки уровня речи (раздел 10.4 спеки)
LOUD_FRACTION = 0.02  # доля самых громких окон, по которым берётся медиана
SILENCE_FLOOR_DB = -60.0  # ниже не опускаемся: там уже собственный шум записи
FALLBACK_THRESHOLD_DB = -35.0  # если уровень речи оценить не удалось
RETRY_ABOVE_DB = -55.0  # если пауз не нашлось, а порог выше — повторяем на 10 дБ ниже
RETRY_STEP_DB = 10.0

_LEVEL_RE = re.compile(r"^lavfi\.astats\.Overall\.(?:Peak|RMS)_level=(-?[\d.]+|-inf|inf|nan)$", re.M)
_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


def wav_args(settings: Settings, src: str, dst: str) -> list[str]:
    return [
        settings.ffmpeg_path, "-v", "error", "-y", "-i", src,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst,
    ]


def levels_args(settings: Settings, wav: str, *, window_sec: float, key: str) -> list[str]:
    """Уровень в дБ на каждое окно: ffmpeg печатает метаданные astats в stdout."""
    samples = max(1, round(16000 * window_sec))
    chain = (
        f"asetnsamples=n={samples}:p=0,astats=metadata=1:reset=1,"
        f"ametadata=print:key=lavfi.astats.Overall.{key}:file=-"
    )
    return [settings.ffmpeg_path, "-v", "error", "-nostats", "-i", wav, "-af", chain, "-f", "null", "-"]


def silence_args(settings: Settings, wav: str, *, threshold_db: float, min_sec: float) -> list[str]:
    chain = f"silencedetect=noise={threshold_db:.1f}dB:d={min_sec}"
    return [settings.ffmpeg_path, "-v", "info", "-nostats", "-i", wav, "-af", chain, "-f", "null", "-"]


def parse_levels(text: str) -> list[float]:
    """Значения в дБ по порядку окон; тишина приходит как -inf."""
    out: list[float] = []
    for raw in _LEVEL_RE.findall(text):
        try:
            out.append(float(raw))
        except ValueError:
            out.append(-math.inf)
    return out


def db_to_amplitude(db: float) -> int:
    """дБ → 0..255. −inf и всё тише −60 дБ считаем нулём."""
    if not math.isfinite(db) or db <= SILENCE_FLOOR_DB:
        return 0
    return max(0, min(255, round(255 * (10 ** (min(db, 0.0) / 20)))))


def peaks_from_levels(levels: list[float]) -> list[int]:
    return [db_to_amplitude(db) for db in levels]


def speech_level_db(levels: list[float]) -> float | None:
    """Медиана самых громких LOUD_FRACTION окон. None, если звука нет вовсе."""
    finite = [db for db in levels if math.isfinite(db)]
    if not finite:
        return None
    finite.sort(reverse=True)
    take = max(1, round(len(finite) * LOUD_FRACTION))
    return round(statistics.median(finite[:take]), 3)


def silence_threshold_db(speech_db: float | None, *, offset: float) -> float:
    if speech_db is None:
        return FALLBACK_THRESHOLD_DB
    return round(max(SILENCE_FLOOR_DB, speech_db - offset), 1)


def parse_silences(text: str, *, duration: float) -> list[dict]:
    """Пары start/end из лога silencedetect. Последняя пауза без конца закрывается концом файла."""
    starts = [float(v) for v in _START_RE.findall(text)]
    ends = [float(v) for v in _END_RE.findall(text)]
    out: list[dict] = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else duration
        start = max(0.0, min(start, duration))
        end = max(0.0, min(end, duration))
        if end - start <= 0:
            continue
        out.append({"start": round(start, 3), "end": round(end, 3)})
    return out


def measure_levels(settings: Settings, wav: str, *, window_sec: float, key: str) -> list[float]:
    text = run_tool(levels_args(settings, wav, window_sec=window_sec, key=key), timeout=WAV_TIMEOUT_SEC)
    return parse_levels(text)


def detect_silences(settings: Settings, wav: str, *, threshold_db: float, min_sec: float, duration: float) -> list[dict]:
    text = run_tool(
        silence_args(settings, wav, threshold_db=threshold_db, min_sec=min_sec),
        timeout=WAV_TIMEOUT_SEC,
        capture_stderr=True,
    )
    return parse_silences(text, duration=duration)


def analyze_audio(settings: Settings, wav: str, *, duration: float) -> dict:
    """peaks.json и analysis.json одним проходом по звуку.

    Пики берутся окнами 1/peaks_per_sec, уровень речи — окнами 50 мс (раздел 10.4 спеки).
    Если при выбранном пороге пауз не нашлось, а порог высокий, повторяем ниже: тихая запись
    целиком «звучит» и снэп резов остаётся без опоры.
    """
    peak_levels = measure_levels(settings, wav, window_sec=1 / settings.peaks_per_sec, key="Peak_level")
    rms_levels = measure_levels(settings, wav, window_sec=RMS_WINDOW_SEC, key="RMS_level")
    speech = speech_level_db(rms_levels)
    threshold = silence_threshold_db(speech, offset=settings.speech_offset_db)
    silences = detect_silences(
        settings, wav, threshold_db=threshold, min_sec=settings.silence_min_sec, duration=duration
    )
    if not silences and threshold > RETRY_ABOVE_DB:
        threshold = round(max(SILENCE_FLOOR_DB, threshold - RETRY_STEP_DB), 1)
        silences = detect_silences(
            settings, wav, threshold_db=threshold, min_sec=settings.silence_min_sec, duration=duration
        )
    dense = detect_silences(
        settings, wav, threshold_db=threshold, min_sec=settings.silence_dense_min_sec, duration=duration
    )
    peaks = {"rate": settings.peaks_per_sec, "peaks": peaks_from_levels(peak_levels)}
    analysis = {
        "duration": round(duration, 3),
        "speech_level_db": speech,
        "threshold_db": threshold,
        "silences": silences,
        "silences_dense": dense,
    }
    return {"peaks": peaks, "analysis": analysis}
```

- [ ] **Step 4: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_media_audio.py && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/media/audio.py tests/test_media_audio.py
git commit -m "feat(media): peaks, speech level and silence maps from ffmpeg output"
```

---
### Task 3: Полоска кадров и команда прокси

**Files:**
- Create: `server/media/thumbs.py`, `server/media/proxy.py`
- Test: `tests/test_media_thumbs.py`, `tests/test_media_proxy.py`

- [ ] **Step 1: Тесты полоски**

Создать `tests/test_media_thumbs.py`:

```python
from server.app.config import Settings
from server.media.thumbs import GridLayout, grid_layout, thumbs_args, thumbs_meta


def s(**over) -> Settings:
    return Settings(_env_file=None, **over)


def test_layout_one_frame_per_interval():
    layout = grid_layout(s(), duration=12.0, width=640, height=360)
    assert layout == GridLayout(count=6, cols=10, rows=1, interval=2.0, frame_width=160, frame_height=90)


def test_layout_rounds_rows_up_and_caps_frames():
    layout = grid_layout(s(), duration=3600.0, width=1920, height=1080)
    assert layout.count == 600 and layout.cols == 10 and layout.rows == 60
    assert layout.interval == 6.0  # 3600 / 600: интервал растянут, чтобы уложиться в предел
    assert layout.frame_width == 160 and layout.frame_height == 90


def test_layout_never_empty_and_keeps_even_height():
    layout = grid_layout(s(), duration=0.4, width=101, height=57)
    assert layout.count == 1 and layout.rows == 1
    assert layout.frame_height % 2 == 0


def test_layout_for_vertical_video():
    layout = grid_layout(s(), duration=10.0, width=1080, height=1920)
    assert layout.frame_width == 160 and layout.frame_height == 284


def test_args_use_fps_and_tile():
    layout = grid_layout(s(), duration=12.0, width=640, height=360)
    args = thumbs_args(s(), "/x/source.mp4", "/x/thumbs.jpg", layout)
    chain = args[args.index("-vf") + 1]
    assert chain == "fps=1/2.0,scale=160:-2,tile=10x1"
    assert "-frames:v" in args and args[args.index("-frames:v") + 1] == "1"
    assert args[-1] == "/x/thumbs.jpg"


def test_meta_describes_the_sprite():
    layout = grid_layout(s(), duration=12.0, width=640, height=360)
    assert thumbs_meta(layout) == {
        "count": 6, "cols": 10, "rows": 1, "interval": 2.0, "width": 160, "height": 90,
    }
```

- [ ] **Step 2: Тесты прокси**

Создать `tests/test_media_proxy.py`:

```python
import pytest

from server.app.config import Settings
from server.media.proxy import parse_progress, proxy_args, proxy_name


def s(**over) -> Settings:
    return Settings(_env_file=None, **over)


def test_video_proxy_is_h264_with_short_gop():
    args = proxy_args(s(), "/x/source.mp4", "/x/proxy.mp4", kind="video")
    assert "libx264" in args and "veryfast" in args
    assert args[args.index("-crf") + 1] == "28"
    assert args[args.index("-g") + 1] == "30"
    assert "-sc_threshold" in args and args[args.index("-sc_threshold") + 1] == "0"
    assert args[args.index("-b:a") + 1] == "96k"
    assert "+faststart" in args
    scale = args[args.index("-vf") + 1]
    assert scale == "scale=w='if(gte(iw,ih),640,-2)':h='if(gte(iw,ih),-2,640)'"
    assert "-progress" in args and args[args.index("-progress") + 1] == "pipe:1"


def test_audio_proxy_has_no_video():
    args = proxy_args(s(), "/x/source.mp3", "/x/proxy.m4a", kind="audio")
    assert "-vn" in args and "libx264" not in args
    assert args[args.index("-b:a") + 1] == "96k"


def test_long_side_is_configurable():
    args = proxy_args(s(proxy_long_side=480), "/x/a.mp4", "/x/p.mp4", kind="video")
    assert "480" in args[args.index("-vf") + 1]


def test_proxy_name_by_kind():
    assert proxy_name("video") == "proxy.mp4"
    assert proxy_name("audio") == "proxy.m4a"
    with pytest.raises(ValueError):
        proxy_name("subtitle")


def test_progress_lines():
    assert parse_progress("out_time_us=1500000", total=3.0) == pytest.approx(0.5)
    assert parse_progress("out_time_us=9000000", total=3.0) == 1.0
    assert parse_progress("out_time_us=N/A", total=3.0) is None
    assert parse_progress("frame=12", total=3.0) is None
    assert parse_progress("out_time_us=100", total=0.0) is None
```

- [ ] **Step 3: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_media_thumbs.py tests/test_media_proxy.py`
Expected: FAIL, нет модулей.

- [ ] **Step 4: Полоска**

Создать `server/media/thumbs.py`:

```python
"""Полоска кадров: один спрайт JPEG плюс раскладка в JSON.

Кадры отбираются фильтром fps (равномерно по времени), а не по ключевым кадрам: с fps интервал
предсказуем, а декодирование мелкого потока стоит недорого. Неполная сетка тоже отдаётся целиком,
пустые клетки чёрные, поэтому клиент считает координату кадра прямо из номера.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from server.app.config import Settings


@dataclass(frozen=True)
class GridLayout:
    count: int
    cols: int
    rows: int
    interval: float
    frame_width: int
    frame_height: int


def grid_layout(settings: Settings, *, duration: float, width: int | None, height: int | None) -> GridLayout:
    """Сколько кадров, с каким шагом и какой сеткой. Кадров не больше thumb_max_frames:
    у длинного файла шаг растягивается, а не растёт число кадров."""
    interval = settings.thumb_interval_sec
    count = max(1, math.ceil(max(duration, 0.0) / interval))
    if count > settings.thumb_max_frames:
        count = settings.thumb_max_frames
        interval = round(max(duration, 0.0) / count, 3)
    cols = min(settings.thumb_cols, count)
    rows = math.ceil(count / cols)
    fw = settings.thumb_width
    ratio = (height / width) if width and height else 9 / 16
    fh = max(2, round(fw * ratio / 2) * 2)  # чётная высота: scale=-2 округляет так же
    return GridLayout(count=count, cols=cols, rows=rows, interval=interval, frame_width=fw, frame_height=fh)


def thumbs_args(settings: Settings, src: str, dst: str, layout: GridLayout) -> list[str]:
    chain = f"fps=1/{layout.interval},scale={layout.frame_width}:-2,tile={layout.cols}x{layout.rows}"
    return [
        settings.ffmpeg_path, "-v", "error", "-y", "-i", src,
        "-vf", chain, "-frames:v", "1", "-q:v", "5", dst,
    ]


def thumbs_meta(layout: GridLayout) -> dict:
    return {
        "count": layout.count,
        "cols": layout.cols,
        "rows": layout.rows,
        "interval": layout.interval,
        "width": layout.frame_width,
        "height": layout.frame_height,
    }
```

- [ ] **Step 5: Прокси**

Создать `server/media/proxy.py`:

```python
"""Прокси для плеера: H.264 640 px с частыми ключевыми кадрами или AAC для звука.

Короткий интервал ключевых кадров нужен для точной перемотки: подрезка клипа в браузере должна
попадать в нужный кадр (раздел 7 спеки).
"""
from __future__ import annotations

from server.app.config import Settings

GOP = 30
CRF = "28"
AUDIO_BITRATE = "96k"
PRESET = "veryfast"


def proxy_name(kind: str) -> str:
    if kind == "video":
        return "proxy.mp4"
    if kind == "audio":
        return "proxy.m4a"
    raise ValueError(f"нет прокси для вида {kind}")


def scale_filter(long_side: int) -> str:
    """Длинная сторона в long_side, короткая пропорционально и чётной (-2)."""
    return f"scale=w='if(gte(iw,ih),{long_side},-2)':h='if(gte(iw,ih),-2,{long_side})'"


def proxy_args(settings: Settings, src: str, dst: str, *, kind: str) -> list[str]:
    args = [
        settings.ffmpeg_path, "-v", "error", "-y",
        "-progress", "pipe:1", "-nostats",
        "-i", src,
    ]
    if kind == "video":
        args += [
            "-vf", scale_filter(settings.proxy_long_side),
            "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
            "-g", str(GOP), "-keyint_min", str(GOP), "-sc_threshold", "0",
            "-pix_fmt", "yuv420p",
        ]
    else:
        args += ["-vn"]
    args += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-movflags", "+faststart", dst]
    return args


def parse_progress(line: str, *, total: float) -> float | None:
    """Доля выполнения из строки -progress. Возвращает None, если строка не про время."""
    key, _, value = line.strip().partition("=")
    if key != "out_time_us" or total <= 0:
        return None
    try:
        micros = int(value)
    except ValueError:
        return None
    return min(1.0, max(0.0, micros / 1_000_000 / total))
```

- [ ] **Step 6: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_media_thumbs.py tests/test_media_proxy.py && uv run ruff check .`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add server/media/thumbs.py server/media/proxy.py tests/test_media_thumbs.py tests/test_media_proxy.py
git commit -m "feat(media): thumbnail grid layout and proxy command"
```

---

### Task 4: Очередь воркера: выбор задания, пульс, отмена

**Files:**
- Create: `server/worker/__init__.py` (пустой), `server/worker/queue.py`
- Test: `tests/test_worker_queue.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_worker_queue.py`:

```python
import sqlite3

import pytest

from server.app.jobs import enqueue_job
from server.app.util import iso, now_iso, utcnow
from server.db.core import connect
from server.db.migrate import migrate
from server.worker.queue import (
    claim_job,
    fail_job,
    finish_job,
    heartbeat,
    is_canceled,
    set_progress,
    write_worker_heartbeat,
)

A = "usr_00000000000a"
B = "usr_00000000000b"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    for uid in (A, B):
        c.execute(
            "INSERT INTO users (id, email, name, created_at) VALUES (?, ?, 'U', ?)",
            (uid, f"{uid}@ya.ru", now_iso()),
        )
    yield c
    c.close()


def test_claim_takes_priority_then_age(conn):
    old_render = enqueue_job(conn, user_id=A, type_="render", target_id="prj_1")
    analyze = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1", priority=10)
    job = claim_job(conn, lane="cpu", pid=42)
    assert job["id"] == analyze
    assert job["status"] == "running" and job["worker_pid"] == 42
    assert job["attempts"] == 1 and job["started_at"] and job["heartbeat_at"]
    finish_job(conn, analyze)
    assert claim_job(conn, lane="cpu", pid=42)["id"] == old_render


def test_claim_returns_none_when_queue_is_empty_or_other_lane(conn):
    assert claim_job(conn, lane="cpu", pid=1) is None
    enqueue_job(conn, user_id=A, type_="transcribe", target_id="ast_1")
    assert claim_job(conn, lane="cpu", pid=1) is None
    assert claim_job(conn, lane="net", pid=1)["type"] == "transcribe"


def test_claim_rotates_between_users(conn):
    """У одного человека очередь из трёх, у второго одно задание: второй не ждёт всю очередь."""
    first = enqueue_job(conn, user_id=A, type_="proxy", target_id="ast_1")
    enqueue_job(conn, user_id=A, type_="proxy", target_id="ast_2")
    enqueue_job(conn, user_id=A, type_="proxy", target_id="ast_3")
    other = enqueue_job(conn, user_id=B, type_="proxy", target_id="ast_4")
    got = claim_job(conn, lane="cpu", pid=1)
    assert got["id"] == first
    finish_job(conn, first)
    assert claim_job(conn, lane="cpu", pid=1)["id"] == other


def test_two_workers_do_not_take_the_same_job(conn, tmp_path):
    job_id = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1")
    other = connect(tmp_path / "t.db")
    try:
        first = claim_job(conn, lane="cpu", pid=1)
        second = claim_job(other, lane="cpu", pid=2)
    finally:
        other.close()
    assert first["id"] == job_id and second is None


def test_heartbeat_and_cancel(conn):
    job_id = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1")
    claim_job(conn, lane="cpu", pid=7)
    stale = iso(utcnow().replace(year=2020))
    conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (stale, job_id))
    heartbeat(conn, job_id)
    assert conn.execute("SELECT heartbeat_at FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] > stale
    assert is_canceled(conn, job_id) is False
    conn.execute("UPDATE jobs SET status = 'canceled' WHERE id = ?", (job_id,))
    assert is_canceled(conn, job_id) is True
    assert is_canceled(conn, "job_missing") is True  # задание исчезло — работать дальше незачем


def test_progress_is_clamped_and_rounded(conn):
    job_id = enqueue_job(conn, user_id=A, type_="proxy", target_id="ast_1")
    claim_job(conn, lane="cpu", pid=1)
    set_progress(conn, job_id, 0.4567)
    assert conn.execute("SELECT progress FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == 0.457
    set_progress(conn, job_id, 5.0)
    assert conn.execute("SELECT progress FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == 1.0
    set_progress(conn, job_id, -1.0)
    assert conn.execute("SELECT progress FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == 0.0


def test_finish_and_fail_are_final(conn):
    job_id = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1")
    claim_job(conn, lane="cpu", pid=1)
    fail_job(conn, job_id, "битый файл")
    row = conn.execute("SELECT status, error, finished_at, progress FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "failed" and row["error"] == "битый файл" and row["finished_at"]
    finish_job(conn, job_id)  # уже завершено: не воскрешаем
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "failed"


def test_fail_trims_a_long_message(conn):
    job_id = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1")
    claim_job(conn, lane="cpu", pid=1)
    fail_job(conn, job_id, "х" * 5000)
    stored = conn.execute("SELECT error FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert len(stored) <= 2000


def test_worker_heartbeat_row(conn):
    write_worker_heartbeat(conn)
    first = conn.execute("SELECT at FROM heartbeats WHERE name = 'worker'").fetchone()[0]
    write_worker_heartbeat(conn)
    assert conn.execute("SELECT at FROM heartbeats WHERE name = 'worker'").fetchone()[0] >= first
    assert conn.execute("SELECT count(*) FROM heartbeats").fetchone()[0] == 1


def test_claim_ignores_a_job_whose_target_is_gone(conn):
    """Ассет удалили, а задание осталось queued: janitor его отменит, воркер не должен за него браться."""
    job_id = enqueue_job(conn, user_id=A, type_="analyze", target_id="ast_1")
    conn.execute("UPDATE jobs SET status = 'canceled' WHERE id = ?", (job_id,))
    assert claim_job(conn, lane="cpu", pid=1) is None
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_worker_queue.py`
Expected: FAIL, нет модуля `server.worker.queue`.

- [ ] **Step 3: Реализация**

Создать пустой `server/worker/__init__.py` и `server/worker/queue.py`:

```python
"""Очередь заданий со стороны воркера: атомарный захват, пульс, отмена, финал.

Захват одним UPDATE … RETURNING: два процесса не возьмут одно задание даже без внешней блокировки
(SQLite ≥ 3.35). Порядок выбора — приоритет, затем справедливость между пользователями, затем возраст.
"""
from __future__ import annotations

import sqlite3

from server.app.util import now_iso

ERROR_MAX_CHARS = 2000

# Среди заданий одного приоритета первым идёт пользователь, чьё последнее задание закончилось раньше:
# один человек с длинной очередью не занимает воркер целиком (раздел 9.1 спеки).
_PICK_SQL = """
UPDATE jobs SET
    status = 'running',
    started_at = :now,
    heartbeat_at = :now,
    worker_pid = :pid,
    attempts = attempts + 1,
    progress = 0
WHERE id = (
    SELECT j.id FROM jobs AS j
    LEFT JOIN (
        SELECT user_id, max(coalesce(finished_at, started_at, created_at)) AS last_at
        FROM jobs WHERE status IN ('done', 'failed', 'running') GROUP BY user_id
    ) AS seen ON seen.user_id = j.user_id
    WHERE j.status = 'queued' AND j.lane = :lane
    ORDER BY j.priority DESC, coalesce(seen.last_at, ''), j.created_at
    LIMIT 1
)
RETURNING *
"""


def claim_job(conn: sqlite3.Connection, *, lane: str, pid: int) -> sqlite3.Row | None:
    row = conn.execute(_PICK_SQL, {"now": now_iso(), "pid": pid, "lane": lane}).fetchone()
    return row


def heartbeat(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (now_iso(), job_id))


def is_canceled(conn: sqlite3.Connection, job_id: str) -> bool:
    """True и когда задание отменили, и когда его строки уже нет: работать дальше незачем."""
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row is None or row["status"] != "running"


def set_progress(conn: sqlite3.Connection, job_id: str, value: float) -> None:
    clamped = round(min(1.0, max(0.0, value)), 3)
    conn.execute("UPDATE jobs SET progress = ? WHERE id = ? AND status = 'running'", (clamped, job_id))


def finish_job(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'done', progress = 1, finished_at = ? WHERE id = ? AND status = 'running'",
        (now_iso(), job_id),
    )


def fail_job(conn: sqlite3.Connection, job_id: str, error: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'failed', finished_at = ?, error = ? WHERE id = ? AND status = 'running'",
        (now_iso(), error[:ERROR_MAX_CHARS], job_id),
    )


def write_worker_heartbeat(conn: sqlite3.Connection) -> None:
    """Пульс процесса для /healthz: одна строка на воркер."""
    conn.execute(
        "INSERT INTO heartbeats (name, at) VALUES ('worker', ?) "
        "ON CONFLICT(name) DO UPDATE SET at = excluded.at",
        (now_iso(),),
    )
```

- [ ] **Step 4: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_worker_queue.py && uv run ruff check .`
Expected: PASS. Если `RETURNING *` не поддерживается установленной сборкой SQLite, записать это в «Поправки» и заменить на `RETURNING id` плюс отдельный `SELECT`.

- [ ] **Step 5: Commit**

```bash
git add server/worker tests/test_worker_queue.py
git commit -m "feat(worker): atomic job claim with fair user rotation, heartbeat, cancel"
```

---
### Task 5: Длинные процессы с прогрессом и отменой

**Files:**
- Modify: `server/media/run.py`
- Test: `tests/test_media_stream.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_media_stream.py`:

```python
import sys
import time

import pytest

from server.media.run import MediaError, run_streaming

COUNTER = "import sys, time\nfor i in range(50):\n    print(i, flush=True)\n    time.sleep(0.05)\n"
NOISY_FAIL = "import sys\nsys.stderr.write('плохой кодек\\n')\nsys.exit(2)\n"


def test_lines_are_streamed_in_order():
    seen = []
    run_streaming([sys.executable, "-c", "print('a'); print('b')"], timeout=30, on_line=seen.append)
    assert [s.strip() for s in seen if s.strip()] == ["a", "b"]


def test_stop_check_terminates_the_process():
    started = time.monotonic()
    with pytest.raises(MediaError) as e:
        run_streaming(
            [sys.executable, "-c", COUNTER],
            timeout=60,
            on_line=lambda _l: None,
            should_stop=lambda: True,
            stop_check_sec=0.05,
        )
    assert e.value.reason == "canceled"
    assert time.monotonic() - started < 15  # не ждём полного прогона в 2.5 с × запас


def test_failure_carries_stderr():
    with pytest.raises(MediaError) as e:
        run_streaming([sys.executable, "-c", NOISY_FAIL], timeout=30, on_line=lambda _l: None)
    assert e.value.reason == "tool_failed" and "плохой кодек" in e.value.stderr


def test_timeout_kills_the_process():
    with pytest.raises(MediaError) as e:
        run_streaming([sys.executable, "-c", COUNTER], timeout=0.2, on_line=lambda _l: None)
    assert e.value.reason == "timeout"


def test_missing_tool():
    with pytest.raises(MediaError) as e:
        run_streaming(["ffmpeg-которого-нет"], timeout=5, on_line=lambda _l: None)
    assert e.value.reason == "tool_missing"
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_media_stream.py`
Expected: FAIL, нет `run_streaming`.

- [ ] **Step 3: Реализация**

В `server/media/run.py` добавить импорты `import os`, `import signal`, `import threading`, `import time`, `from collections.abc import Callable`, константу и функцию:

```python
KILL_AFTER_SEC = 10.0  # после SIGTERM даём процессу столько на выход, затем убиваем


def _terminate(proc: subprocess.Popen) -> None:
    """Сначала мягко, потом жёстко: ffmpeg по SIGTERM дописывает контейнер и выходит сам."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=KILL_AFTER_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=KILL_AFTER_SEC)


def run_streaming(
    args: list[str],
    *,
    timeout: float,
    on_line: Callable[[str], None],
    should_stop: Callable[[], bool] | None = None,
    stop_check_sec: float = 2.0,
) -> None:
    """Запускает инструмент и отдаёт строки stdout по мере поступления.

    Нужен для долгих кодирований: on_line получает строки -progress, а should_stop опрашивается
    раз в stop_check_sec и позволяет прервать работу по отмене задания. stderr копится в памяти
    (у ffmpeg он короткий) и попадает в MediaError при ненулевом коде.
    """
    try:
        proc = subprocess.Popen(  # noqa: S603
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise MediaError("tool_missing", f"Не найден {args[0]}") from exc

    stderr_parts: list[str] = []

    def drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_parts.append(line)

    def watch() -> None:
        """Отдельный поток: отмена и таймаут не должны ждать следующей строки stdout."""
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if time.monotonic() > deadline:
                reasons.append("timeout")
                _terminate(proc)
                return
            if should_stop is not None and should_stop():
                reasons.append("canceled")
                _terminate(proc)
                return
            time.sleep(min(stop_check_sec, 0.5))

    reasons: list[str] = []
    err_thread = threading.Thread(target=drain_stderr, daemon=True)
    watch_thread = threading.Thread(target=watch, daemon=True)
    err_thread.start()
    watch_thread.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line(line.rstrip("\n"))
    finally:
        proc.wait()
        err_thread.join(timeout=5)
        watch_thread.join(timeout=KILL_AFTER_SEC + 5)

    stderr_text = tail_lines("".join(stderr_parts))
    if reasons:
        reason = reasons[0]
        message = "Отменено" if reason == "canceled" else f"{args[0]} не уложился в {timeout:.0f} с"
        raise MediaError(reason, message, stderr_text)
    if proc.returncode != 0:
        raise MediaError("tool_failed", f"{args[0]} завершился с кодом {proc.returncode}", stderr_text)
```

Строку `import os` и `import signal` не добавлять, если они не используются: `_terminate` обходится методами `Popen`.

- [ ] **Step 4: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_media_stream.py && uv run ruff check .`
Expected: PASS. Если ruff требует `# noqa: S603` на `Popen`, поставить как в коде.

- [ ] **Step 5: Commit**

```bash
git add server/media/run.py tests/test_media_stream.py
git commit -m "feat(media): streaming runner with progress lines, cancel and timeout"
```

---

### Task 6: Обработчики analyze и proxy

**Files:**
- Create: `server/worker/handlers.py`
- Test: `tests/test_worker_handlers.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_worker_handlers.py`:

```python
import json
import sqlite3

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
    c.execute("INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)", (USER, now_iso()))
    yield c
    c.close()


def make_asset(conn, settings, asset_id="ast_000000000001", kind="video", status="uploaded", ext="mp4"):
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, created_at, last_access_at) "
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
    monkeypatch.setattr(handlers, "build_thumbs", lambda *a, **k: {"count": 6, "cols": 10, "rows": 1,
                                                                  "interval": 2.0, "width": 160, "height": 90})
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
    monkeypatch.setattr(handlers, "run_streaming",
                        lambda args, **k: open(args[-1], "wb").write(b"a"))
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
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_worker_handlers.py`
Expected: FAIL, нет модуля `server.worker.handlers`.

- [ ] **Step 3: Реализация**

Создать `server/worker/handlers.py`:

```python
"""Обработчики заданий воркера.

analyze доводит ассет до состояния «можно монтировать»: параметры файла, пики, карты пауз, полоска
кадров. proxy делает лёгкое видео для плеера. Оба пишут во временный файл и переименовывают: половина
результата на диске не должна выглядеть готовой.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.storage import asset_dir
from server.app.util import now_iso
from server.media.audio import analyze_audio, wav_args
from server.media.probe import probe_file
from server.media.proxy import parse_progress, proxy_args, proxy_name
from server.media.run import MediaError, run_streaming, run_tool
from server.media.thumbs import grid_layout, thumbs_args, thumbs_meta

log = logging.getLogger("video.worker")

PROXY_PRIORITY = 5  # ниже analyze (10) и выше рендера (0): раздел 9.1 спеки
WAV_NAME = "audio16k.wav"
PROGRESS_AFTER_PROBE = 0.2
PROGRESS_AFTER_AUDIO = 0.5
PROGRESS_AFTER_THUMBS = 0.8


def _asset(conn: sqlite3.Connection, job: sqlite3.Row) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM assets WHERE id = ? AND user_id = ?", (job["target_id"], job["user_id"])
    ).fetchone()


def _source(settings: Settings, asset: sqlite3.Row) -> Path:
    return asset_dir(settings, asset["user_id"], asset["id"]) / f"source.{asset['ext']}"


def _write_json(path: Path, data: dict) -> None:
    """Через временный файл: читатель никогда не увидит половину JSON."""
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def extract_wav(settings: Settings, src: Path, dst: Path) -> None:
    run_tool(wav_args(settings, str(src), str(dst)), timeout=settings.analyze_timeout_sec)


def build_thumbs(settings: Settings, src: Path, dst: Path, *, duration: float, width, height) -> dict:
    layout = grid_layout(settings, duration=duration, width=width, height=height)
    run_tool(thumbs_args(settings, str(src), str(dst), layout), timeout=settings.analyze_timeout_sec)
    return thumbs_meta(layout)


def _set_asset_failed(conn: sqlite3.Connection, asset_id: str, message: str) -> None:
    conn.execute(
        "UPDATE assets SET status = 'failed', error = ? WHERE id = ?", (message[:1000], asset_id)
    )


def handle_analyze(conn: sqlite3.Connection, settings: Settings, job: sqlite3.Row) -> None:
    from server.worker.queue import set_progress  # локально: очередь не должна зависеть от обработчиков

    asset = _asset(conn, job)
    if asset is None:
        log.info("analyze: ассет %s уже удалён, пропускаем", job["target_id"])
        return
    asset_id = asset["id"]
    folder = asset_dir(settings, asset["user_id"], asset_id)
    conn.execute("UPDATE assets SET status = 'analyzing', error = NULL WHERE id = ?", (asset_id,))
    try:
        info = probe_file(settings, str(_source(settings, asset)))
    except MediaError as exc:
        _set_asset_failed(conn, asset_id, exc.message)
        raise
    conn.execute(
        "UPDATE assets SET kind = ?, duration = ?, width = ?, height = ?, fps = ?, has_audio = ?, "
        "video_codec = ?, audio_codec = ? WHERE id = ?",
        (
            info.kind, info.duration, info.width, info.height, info.fps, int(info.has_audio),
            info.video_codec, info.audio_codec, asset_id,
        ),
    )
    set_progress(conn, job["id"], PROGRESS_AFTER_PROBE)

    peaks = {"rate": settings.peaks_per_sec, "peaks": []}
    analysis = {
        "duration": info.duration, "speech_level_db": None, "threshold_db": None,
        "silences": [], "silences_dense": [],
    }
    if info.has_audio:
        wav = folder / WAV_NAME
        try:
            extract_wav(settings, _source(settings, asset), wav)
            result = analyze_audio(settings, str(wav), duration=info.duration)
            peaks, analysis = result["peaks"], result["analysis"]
        except MediaError as exc:
            _set_asset_failed(conn, asset_id, exc.message)
            raise
        finally:
            wav.unlink(missing_ok=True)  # звук для транскрипции пересоберём в M4, диск дороже
    _write_json(folder / "peaks.json", peaks)
    _write_json(folder / "analysis.json", analysis)
    set_progress(conn, job["id"], PROGRESS_AFTER_AUDIO)

    if info.kind == "video":
        try:
            meta = build_thumbs(
                settings, _source(settings, asset), folder / "thumbs.jpg",
                duration=info.duration, width=info.width, height=info.height,
            )
        except MediaError as exc:
            _set_asset_failed(conn, asset_id, exc.message)
            raise
        _write_json(folder / "thumbs.json", meta)
    set_progress(conn, job["id"], PROGRESS_AFTER_THUMBS)

    conn.execute(
        "UPDATE assets SET status = 'ready', last_access_at = ? WHERE id = ?", (now_iso(), asset_id)
    )
    enqueue_job(
        conn, user_id=job["user_id"], type_="proxy", target_id=asset_id, priority=PROXY_PRIORITY
    )
    log.info("analyze: %s готов (%s, %.1f с)", asset_id, info.kind, info.duration)


def handle_proxy(conn: sqlite3.Connection, settings: Settings, job: sqlite3.Row) -> None:
    from server.worker.queue import is_canceled, set_progress

    asset = _asset(conn, job)
    if asset is None:
        log.info("proxy: ассет %s уже удалён, пропускаем", job["target_id"])
        return
    if asset["status"] not in ("ready", "proxy_ready"):
        log.info("proxy: ассет %s в статусе %s, кодировать нечего", asset["id"], asset["status"])
        return
    folder = asset_dir(settings, asset["user_id"], asset["id"])
    dst = folder / proxy_name(asset["kind"])
    tmp = dst.with_suffix(dst.suffix + ".part")
    total = float(asset["duration"] or 0)

    def on_line(line: str) -> None:
        value = parse_progress(line, total=total)
        if value is not None:
            set_progress(conn, job["id"], value)

    try:
        run_streaming(
            proxy_args(settings, str(_source(settings, asset)), str(tmp), kind=asset["kind"]),
            timeout=settings.proxy_timeout_sec,
            on_line=on_line,
            should_stop=lambda: is_canceled(conn, job["id"]),
        )
    except MediaError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dst)
    conn.execute(
        "UPDATE assets SET status = 'proxy_ready', last_access_at = ? WHERE id = ?",
        (now_iso(), asset["id"]),
    )
    log.info("proxy: %s готов", asset["id"])


HANDLERS = {"analyze": handle_analyze, "proxy": handle_proxy}
```

- [ ] **Step 4: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_worker_handlers.py && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/worker/handlers.py tests/test_worker_handlers.py
git commit -m "feat(worker): analyze and proxy handlers"
```

---

### Task 7: Цикл воркера и systemd-юнит

**Files:**
- Create: `server/worker/__main__.py`, `deploy/video-worker.service`
- Modify: `deploy/deploy.sh`, `deploy/bootstrap.sh`
- Test: `tests/test_worker_loop.py`, `tests/test_deploy_files.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_worker_loop.py`:

```python
import sqlite3
import threading

import pytest

from server.app.config import Settings
from server.app.jobs import enqueue_job
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate
from server.media.run import MediaError
from server.worker import __main__ as worker_main

USER = "usr_00000000000a"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", worker_poll_sec=0.1)


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = connect(settings.db_path)
    migrate(c)
    c.execute("INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)", (USER, now_iso()))
    yield c
    c.close()


def test_run_once_takes_a_job_and_marks_it_done(conn, settings, monkeypatch):
    job_id = enqueue_job(conn, user_id=USER, type_="analyze", target_id="ast_1", priority=10)
    seen = []
    monkeypatch.setitem(worker_main.HANDLERS, "analyze", lambda c, s, job: seen.append(job["id"]))
    assert worker_main.run_once(conn, settings) is True
    assert seen == [job_id]
    row = conn.execute("SELECT status, progress, finished_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "done" and row["progress"] == 1.0 and row["finished_at"]


def test_run_once_on_empty_queue(conn, settings):
    assert worker_main.run_once(conn, settings) is False


def test_failure_marks_the_job_failed_and_keeps_the_loop_alive(conn, settings, monkeypatch):
    job_id = enqueue_job(conn, user_id=USER, type_="analyze", target_id="ast_1")

    def boom(c, s, job):
        raise MediaError("tool_failed", "ffmpeg упал", "хвост stderr")

    monkeypatch.setitem(worker_main.HANDLERS, "analyze", boom)
    assert worker_main.run_once(conn, settings) is True
    row = conn.execute("SELECT status, error FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "failed" and "ffmpeg упал" in row["error"] and "stderr" in row["error"]


def test_unexpected_error_also_fails_the_job(conn, settings, monkeypatch):
    job_id = enqueue_job(conn, user_id=USER, type_="analyze", target_id="ast_1")

    def boom(c, s, job):
        raise ZeroDivisionError("делить на ноль")

    monkeypatch.setitem(worker_main.HANDLERS, "analyze", boom)
    assert worker_main.run_once(conn, settings) is True
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "failed"


def test_canceled_job_is_left_canceled(conn, settings, monkeypatch):
    job_id = enqueue_job(conn, user_id=USER, type_="proxy", target_id="ast_1")

    def cancel_midway(c, s, job):
        c.execute("UPDATE jobs SET status = 'canceled' WHERE id = ?", (job["id"],))
        raise MediaError("canceled", "Отменено")

    monkeypatch.setitem(worker_main.HANDLERS, "proxy", cancel_midway)
    worker_main.run_once(conn, settings)
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] == "canceled"


def test_unknown_job_type_fails_loudly(conn, settings, monkeypatch):
    job_id = enqueue_job(conn, user_id=USER, type_="render", target_id="prj_1")
    worker_main.run_once(conn, settings)  # обработчика render в M1b нет
    row = conn.execute("SELECT status, error FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "failed" and "render" in row["error"]


def test_heartbeat_thread_updates_the_row(conn, settings):
    job_id = enqueue_job(conn, user_id=USER, type_="analyze", target_id="ast_1")
    stop = threading.Event()
    beat = worker_main.Heartbeat(settings, job_id=job_id, interval=0.05)
    beat.start()
    try:
        assert beat.wait_for_first(timeout=5) is True
    finally:
        beat.stop()
        stop.set()
    row = conn.execute("SELECT heartbeat_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["heartbeat_at"] is not None
    assert conn.execute("SELECT at FROM heartbeats WHERE name = 'worker'").fetchone() is not None
```

Добавить в `tests/test_deploy_files.py`:

```python
def test_worker_unit_is_limited_and_installed():
    unit = (DEPLOY / "video-worker.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/editing-site/.venv/bin/python -m server.worker" in unit
    assert "User=video" in unit and "Nice=10" in unit
    assert "CPUQuota=" in unit and "MemoryMax=" in unit
    assert "ProtectSystem=strict" in unit and "ReadWritePaths=/srv/video" in unit
    assert "Restart=always" in unit and "TimeoutStopSec=" in unit
    for name in ("bootstrap.sh", "deploy.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "video-worker.service" in text, name
    assert "systemctl restart video-api video-worker" in (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_worker_loop.py tests/test_deploy_files.py`
Expected: FAIL, нет `server/worker/__main__.py` и юнита.

- [ ] **Step 3: Цикл**

Создать `server/worker/__main__.py`:

```python
"""Воркер: python -m server.worker. Один процесс, полоса cpu, одно задание за раз.

Пульс идёт из отдельного потока со своим соединением: пока ffmpeg кодирует час, janitor не должен
считать задание зависшим. Отмена и остановка сервиса доходят до ffmpeg через should_stop у run_streaming.
"""
from __future__ import annotations

import logging
import signal
import sqlite3
import threading
import time
from types import FrameType

from server.app.config import Settings
from server.app.main import configure_logging
from server.db.core import connect
from server.media.run import MediaError
from server.worker.handlers import HANDLERS
from server.worker.queue import (
    claim_job,
    fail_job,
    finish_job,
    heartbeat,
    write_worker_heartbeat,
)

log = logging.getLogger("video.worker")

LANE = "cpu"
HEARTBEAT_SEC = 10.0
IDLE_LOG_EVERY = 300  # раз в сколько пустых кругов писать, что воркер жив
_stopping = threading.Event()


class Heartbeat(threading.Thread):
    """Обновляет пульс задания и процесса, пока идёт работа. Своё соединение: чужое занято ffmpeg-циклом."""

    def __init__(self, settings: Settings, *, job_id: str, interval: float = HEARTBEAT_SEC) -> None:
        super().__init__(daemon=True)
        self.settings = settings
        self.job_id = job_id
        self.interval = interval
        self._stop = threading.Event()
        self._first = threading.Event()

    def run(self) -> None:
        conn = connect(self.settings.db_path)
        try:
            while not self._stop.is_set():
                try:
                    heartbeat(conn, self.job_id)
                    write_worker_heartbeat(conn)
                    self._first.set()
                except sqlite3.Error as exc:  # база занята — не повод ронять работу
                    log.warning("пульс не записался: %s", exc)
                self._stop.wait(self.interval)
        finally:
            conn.close()

    def wait_for_first(self, timeout: float) -> bool:
        return self._first.wait(timeout)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=5)


def run_once(conn: sqlite3.Connection, settings: Settings) -> bool:
    """Взять одно задание и выполнить. True, если работа была."""
    job = claim_job(conn, lane=LANE, pid=_pid())
    if job is None:
        return False
    log.info("взято задание %s (%s, %s)", job["id"], job["type"], job["target_id"])
    beat = Heartbeat(settings, job_id=job["id"])
    beat.start()
    try:
        handler = HANDLERS.get(job["type"])
        if handler is None:
            raise MediaError("unknown_job", f"нет обработчика для задания {job['type']}")
        handler(conn, settings, job)
    except MediaError as exc:
        if exc.reason == "canceled":
            log.info("задание %s отменено", job["id"])
        else:
            log.warning("задание %s не выполнено: %s", job["id"], exc.message)
            fail_job(conn, job["id"], f"{exc.message}\n{exc.stderr}".strip())
    except Exception as exc:  # noqa: BLE001 — воркер не должен падать из-за одного задания
        log.exception("задание %s упало", job["id"])
        fail_job(conn, job["id"], f"внутренняя ошибка: {exc}")
    else:
        finish_job(conn, job["id"])
        log.info("задание %s выполнено", job["id"])
    finally:
        beat.stop()
    return True


def _pid() -> int:
    import os

    return os.getpid()


def _handle_stop(signum: int, _frame: FrameType | None) -> None:
    log.info("получен сигнал %s, останавливаемся после текущего задания", signum)
    _stopping.set()


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    conn = connect(settings.db_path)
    idle = 0
    try:
        write_worker_heartbeat(conn)
        log.info("воркер запущен, полоса %s", LANE)
        while not _stopping.is_set():
            try:
                worked = run_once(conn, settings)
            except sqlite3.Error as exc:
                log.warning("база недоступна: %s", exc)
                worked = False
            if worked:
                idle = 0
                continue
            idle += 1
            if idle % IDLE_LOG_EVERY == 0:
                log.info("очередь пуста, ждём")
            write_worker_heartbeat(conn)
            time.sleep(settings.worker_poll_sec)
    finally:
        conn.close()
        log.info("воркер остановлен")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Юнит и установка**

Создать `deploy/video-worker.service`:

```ini
[Unit]
Description=Editing site worker (анализ, прокси, рендер)
After=network-online.target
Wants=network-online.target

[Service]
User=video
Group=video
WorkingDirectory=/opt/editing-site
EnvironmentFile=/opt/editing-site/.env
ExecStart=/opt/editing-site/.venv/bin/python -m server.worker
Restart=always
RestartSec=5
# Обработка не должна отбирать процессор у API: доля меньше 100 % от одного ядра и потолок памяти.
Nice=10
CPUQuota=150%
MemoryMax=2G
# Долгий рендер дописывает файл по SIGTERM: даём время выйти самому.
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/video

[Install]
WantedBy=multi-user.target
```

В `deploy/deploy.sh` рядом с установкой юнита API добавить `install -m 644 "$APP_DIR/deploy/video-worker.service" /etc/systemd/system/video-worker.service`, а `systemctl restart video-api` заменить на `systemctl restart video-api video-worker`. Перед перезапуском добавить `systemctl enable video-worker >/dev/null`.

В `deploy/bootstrap.sh` добавить установку того же файла и `video-worker` в список `systemctl enable`.

- [ ] **Step 5: Прогнать тесты и линтер**

Run: `uv run python -m pytest && uv run ruff check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/worker/__main__.py deploy tests/test_worker_loop.py tests/test_deploy_files.py
git commit -m "feat(worker): main loop with heartbeat, cancellation and systemd unit"
```

---
### Task 8: Интеграция на синтетическом медиа

**Files:**
- Create: `tests/test_media_integration.py`, `tests/media_fixtures.py`
- Test: он же

- [ ] **Step 1: Фикстуры синтетического медиа**

Создать `tests/media_fixtures.py`:

```python
"""Короткие ролики, которые ffmpeg генерирует сам: тесты не тащат бинарные файлы в репозиторий."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
HAVE_FFMPEG = bool(FFMPEG and FFPROBE)


def make_video(path: Path, *, seconds: int = 6, size: str = "320x180", silent_from: float = 2.0,
               silent_to: float = 4.0) -> Path:
    """Видео со звуком, в середине участок тишины: на нём проверяется карта пауз."""
    mute = f"volume=enable='between(t,{silent_from},{silent_to})':volume=0"
    subprocess.run(  # noqa: S603
        [
            FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-filter_complex", f"[1:a]{mute}[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def make_silent_video(path: Path, *, seconds: int = 3) -> Path:
    subprocess.run(  # noqa: S603
        [
            FFMPEG, "-v", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc2=size=160x120:rate=25:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def make_audio(path: Path, *, seconds: int = 4) -> Path:
    subprocess.run(  # noqa: S603
        [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
         "-c:a", "aac", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def make_broken(path: Path) -> Path:
    path.write_bytes(b"not a video at all" * 100)
    return path
```

- [ ] **Step 2: Тесты полного пути**

Создать `tests/test_media_integration.py`:

```python
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
    return Settings(_env_file=None, data_dir=tmp_path / "data", worker_poll_sec=0.05)


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = connect(settings.db_path)
    migrate(c)
    c.execute("INSERT INTO users (id, email, name, created_at) VALUES (?, 'a@b.c', 'A', ?)", (USER, now_iso()))
    yield c
    c.close()


def add_asset(conn, settings, source_maker, *, asset_id, kind, ext):
    folder = asset_dir(settings, USER, asset_id)
    folder.mkdir(parents=True, exist_ok=True)
    source_maker(folder / f"source.{ext}")
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, created_at, last_access_at) "
        "VALUES (?, ?, ?, ?, ?, 1, 'uploaded', ?, ?)",
        (asset_id, USER, kind, f"a.{ext}", ext, now_iso(), now_iso()),
    )
    enqueue_job(conn, user_id=USER, type_="analyze", target_id=asset_id, priority=10)
    return folder


def drain(conn, settings, limit=4):
    """Прокрутить очередь: analyze ставит proxy, поэтому кругов больше одного."""
    for _ in range(limit):
        if not worker_main.run_once(conn, settings):
            return


def test_video_goes_all_the_way_to_proxy(conn, settings):
    folder = add_asset(conn, settings, make_video, asset_id="ast_000000000001", kind="video", ext="mp4")
    drain(conn, settings)

    row = conn.execute("SELECT * FROM assets WHERE id = 'ast_000000000001'").fetchone()
    assert row["status"] == "proxy_ready"
    assert row["duration"] == pytest.approx(6.0, abs=0.3)
    assert (row["width"], row["height"]) == (320, 180)
    assert row["fps"] == pytest.approx(25.0, abs=0.1)
    assert row["has_audio"] == 1 and row["video_codec"] == "h264"

    peaks = json.loads((folder / "peaks.json").read_text(encoding="utf-8"))
    assert peaks["rate"] == 50
    assert len(peaks["peaks"]) == pytest.approx(300, abs=20)
    assert max(peaks["peaks"]) > 100  # звук слышен
    assert min(peaks["peaks"]) == 0  # тишина в середине

    analysis = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["speech_level_db"] is not None and analysis["threshold_db"] < 0
    pause = next(s for s in analysis["silences"] if s["end"] - s["start"] > 1)
    assert pause["start"] == pytest.approx(2.0, abs=0.3)
    assert pause["end"] == pytest.approx(4.0, abs=0.3)
    assert analysis["silences_dense"]

    meta = json.loads((folder / "thumbs.json").read_text(encoding="utf-8"))
    assert meta["count"] == 3 and meta["cols"] == 3 and meta["rows"] == 1
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
    folder = add_asset(conn, settings, make_silent_video, asset_id="ast_000000000002", kind="video", ext="mp4")
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
    worker_main.run_once(conn, settings)  # analyze
    conn.execute("DELETE FROM assets WHERE id = 'ast_000000000005'")
    assert worker_main.run_once(conn, settings) is True  # proxy взято и мирно завершилось
    assert conn.execute("SELECT status FROM jobs WHERE type = 'proxy'").fetchone()[0] == "done"


def test_probe_reports_a_broken_file(settings, tmp_path):
    with pytest.raises(MediaError):
        probe_file(settings, str(make_broken(tmp_path / "broken.mp4")))
```

- [ ] **Step 3: Прогон**

Run: `uv run python -m pytest tests/test_media_integration.py -p no:randomly`
Expected: PASS за секунды. Если какая-то проверка расходится с реальным ffmpeg (число пиков, границы пауз), подправить допуски в тесте, а не логику, и записать это в «Поправки».

- [ ] **Step 4: Commit**

```bash
git add tests/media_fixtures.py tests/test_media_integration.py
git commit -m "test(media): end-to-end analyze and proxy on generated clips"
```

---

### Task 9: Плеер и прогресс обработки в браузере

**Files:**
- Create: `web/src/player.ts`, `web/src/player.test.ts`
- Modify: `web/src/assets.ts`, `web/src/assets.test.ts`, `web/src/style.css`

- [ ] **Step 1: Тесты**

Создать `web/src/player.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { playerMarkup, progressText, thumbAt } from './player'

const meta = { count: 6, cols: 3, rows: 2, interval: 2, width: 160, height: 90 }

describe('player helpers', () => {
  it('maps a moment to a sprite cell', () => {
    expect(thumbAt(meta, 0)).toEqual({ index: 0, x: 0, y: 0 })
    expect(thumbAt(meta, 2.5)).toEqual({ index: 1, x: -160, y: 0 })
    expect(thumbAt(meta, 7)).toEqual({ index: 3, x: 0, y: -90 })
    expect(thumbAt(meta, 999)).toEqual({ index: 5, x: -320, y: -90 })
    expect(thumbAt(meta, -5)).toEqual({ index: 0, x: 0, y: 0 })
  })

  it('describes processing progress in words', () => {
    expect(progressText('uploaded', null)).toBe('ждёт обработки')
    expect(progressText('analyzing', 0.5)).toBe('анализ, 50 %')
    expect(progressText('analyzing', null)).toBe('анализ')
    expect(progressText('ready', 0.25)).toBe('готовим прокси, 25 %')
    expect(progressText('proxy_ready', 1)).toBe('')
    expect(progressText('failed', null)).toBe('')
  })

  it('builds a video element for a video proxy and audio for sound', () => {
    expect(playerMarkup({ proxy: '/files/u/assets/a/proxy.mp4' }, 'video')).toContain('<video')
    expect(playerMarkup({ proxy: '/files/u/assets/a/proxy.m4a' }, 'audio')).toContain('<audio')
    expect(playerMarkup({ proxy: null }, 'video')).toBe('')
    expect(playerMarkup({ proxy: '/x"onerror="alert(1)' }, 'video')).not.toContain('onerror="alert')
  })
})
```

Добавить в `web/src/assets.test.ts`:

```ts
  it('keeps polling while anything is still being processed', () => {
    expect(needsPolling([{ status: 'uploaded' }])).toBe(true)
    expect(needsPolling([{ status: 'analyzing' }])).toBe(true)
    expect(needsPolling([{ status: 'ready' }])).toBe(true)
    expect(needsPolling([{ status: 'proxy_ready' }, { status: 'failed' }])).toBe(false)
  })
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `cd web && npm test`
Expected: FAIL: нет `./player`; и проверка `needsPolling` для `ready` возвращает `false`.

- [ ] **Step 3: Плеер**

Создать `web/src/player.ts`:

```ts
import { escapeHtml } from './html'

export type ThumbsMeta = { count: number; cols: number; rows: number; interval: number; width: number; height: number }
export type AssetFiles = { proxy: string | null }

/** Клетка спрайта для момента времени: индекс и смещение фона. */
export function thumbAt(meta: ThumbsMeta, seconds: number): { index: number; x: number; y: number } {
  const raw = Math.floor(Math.max(0, seconds) / meta.interval)
  const index = Math.min(meta.count - 1, Math.max(0, raw))
  return {
    index,
    x: -(index % meta.cols) * meta.width,
    y: -Math.floor(index / meta.cols) * meta.height,
  }
}

/** Человеческое описание обработки: пока идёт анализ и прокси, пользователь видит, чего ждать. */
export function progressText(status: string, progress: number | null): string {
  const pct = progress === null ? null : Math.round(Math.min(1, Math.max(0, progress)) * 100)
  if (status === 'uploaded') return 'ждёт обработки'
  if (status === 'analyzing') return pct === null ? 'анализ' : `анализ, ${pct} %`
  if (status === 'ready') return pct === null ? 'готовим прокси' : `готовим прокси, ${pct} %`
  return ''
}

/** Плеер прокси. Пока прокси нет, ничего не рисуем: исходник наружу не отдаётся. */
export function playerMarkup(files: AssetFiles, kind: string): string {
  if (!files.proxy) return ''
  const src = escapeHtml(files.proxy)
  return kind === 'audio'
    ? `<audio class="player" controls preload="metadata" src="${src}"></audio>`
    : `<video class="player" controls preload="metadata" src="${src}"></video>`
}
```

- [ ] **Step 4: Панель**

В `web/src/assets.ts`:

- тип `Asset` дополнить полями `duration: number | null`, `progress?: number | null`, `files: { proxy: string | null; thumbs: string | null; thumbs_meta: string | null }`;
- `FINAL` оставить `['proxy_ready', 'failed']`, чтобы опрос продолжался, пока делается прокси (статус `ready` промежуточный);
- в `row(a)` добавить колонку с кнопкой «Смотреть» для ассета с `files.proxy` и текстом `progressText(a.status, a.progress ?? null)` рядом со статусом;
- по нажатию «Смотреть» показать под таблицей `playerMarkup(a.files, a.kind)` и подставить разметку в отдельный элемент `#player`, повторное нажатие закрывает.

Разметку панели дополнить строкой `<div id="player"></div>` после таблицы.

В `web/src/style.css` добавить:

```css
.player { display: block; width: 100%; max-width: 640px; margin: 12px 0; background: #000; border-radius: 6px; }
```

- [ ] **Step 5: Прогон**

Run: `cd web && npm test && npm run build`
Expected: зелено.

- [ ] **Step 6: Commit**

```bash
git add web/src/player.ts web/src/player.test.ts web/src/assets.ts web/src/assets.test.ts web/src/style.css
git commit -m "feat(web): proxy player and processing progress in the assets panel"
```

---

### Task 10: Документация и выкатка

**Files:**
- Modify: `README.md`
- Живая проверка на VM (координатор, не субагент)

- [ ] **Step 1: README**

В раздел «Загрузка и файлы» добавить подраздел:

```markdown
### Обработка (M1b)

- Воркер `video-worker` (`python -m server.worker`) берёт задания из таблицы `jobs`, одно за раз, полоса `cpu`. Пульс раз в 10 с виден в `/healthz` полем `worker_seen_sec_ago`; пульс старше 2 минут переводит здоровье в `degraded`.
- `analyze` (приоритет 10): `ffprobe` → длительность, размеры, кадры, кодеки; звук в WAV 16 кГц → пики (`peaks.json`, 50 значений в секунду) и карты пауз (`analysis.json`, обычная и плотная); полоска кадров (`thumbs.jpg` плюс раскладка `thumbs.json`). Ассет переходит в `ready`.
- `proxy` (приоритет 5): H.264 640 px по длинной стороне, CRF 28, ключевой кадр каждые 30 кадров, звук AAC 96 кбит; для звуковых ассетов `proxy.m4a`. Ассет переходит в `proxy_ready`.
- Порог тишины не абсолютный: берётся уровень речи (медиана самых громких 2 % окон) минус 16 дБ, не ниже −60 дБ. Если пауз не нашлось, порог опускается ещё на 10 дБ.
- Отмена: удаление ассета отменяет его задания, воркер видит это при следующем пульсе и останавливает ffmpeg (`SIGTERM`, через 10 с `SIGKILL`).
- Логи: `journalctl -u video-worker`.
```

- [ ] **Step 2: Прогон и коммит**

Run: `uv run python -m pytest && uv run ruff check . && cd web && npm test && npm run build`

```bash
git add README.md
git commit -m "docs: worker, analyze and proxy"
```

- [ ] **Step 3: Слияние и выкатка** (координатор)

`git checkout main && git merge --ff-only m1b-worker-analyze && git push origin main m1b-worker-analyze`, затем на VM `sudo bash /opt/editing-site/deploy/deploy.sh`.

- [ ] **Step 4: Живая проверка** (координатор)

1. `systemctl is-active video-worker` → `active`; `journalctl -u video-worker -n 20` → строка «воркер запущен».
2. `curl -s http://127.0.0.1:8010/healthz` → `worker_seen_sec_ago` число, `status` = `ok`.
3. Загрузить настоящий ролик (не синтетический) через `tools/upload_file.py`, дождаться `proxy_ready`, проверить `GET /api/v1/assets/{id}`: длительность, размеры, ссылки на `proxy`, `thumbs`, `peaks`.
4. Скачать `peaks.json` и `analysis.json` через `/files/...`: пики не пустые, паузы найдены.
5. Открыть прокси в браузере через плеер в панели файлов.
6. Замерить время: `journalctl -u video-worker` покажет длительность обработки; сравнить с замером `docs/benchmarks/2026-09-04-editing.md` и, если прокси идёт медленнее реального времени, записать это в «Поправки» и в спеку.
7. Удалить ассет во время кодирования и убедиться, что воркер прервал ffmpeg и не оставил `.part`.

---

## Поправки по ходу выполнения

- **Task 1** (`cd866b4`, `55236aa`): ruff в проекте не включает `S603`/`BLE001`, поэтому `# noqa` из плана отвергаются как RUF100; в тестах вместо `python` нужен `sys.executable`, а дочернему процессу с кириллицей в выводе — флаг `-X utf8` (консоль cp1251). Ревью: пустая переменная окружения означала бы запуск пустой строки, добавлен валидатор путей к инструментам; `PermissionError` теперь даёт понятное сообщение; `probe_file` покрыта тестами.
- **Task 2, 3** (`634ae4c`, `30732a8`, `6aa4073`): `re.M` заменён на `re.MULTILINE` (правило FURB167). В плане `grid_layout` урезал ширину сетки под число кадров, что противоречило его же тестам и докстроке: сетка постоянной ширины, пустые клетки чёрные. Ревью: регулярное выражение уровней теперь ловит `-nan`; прокси больше не увеличивает кадр меньше целевого (`min(iw, 640)`), иначе прокси из мелкого ролика весил бы больше исходника.
- **Task 4–7** (`b0fe2a0`, `3a35eda`, `48cdbc6`, `1e717f6`): запрос захвата задания с `UPDATE … RETURNING *` и честной ротацией пользователей проверен на живой базе. Ревью нашло, что перезапуск воркера во время кодирования оставлял задание в статусе `running` до прихода janitor, то есть до часа: теперь при старте воркер возвращает осиротевшие задания в очередь, а по сигналу остановки возвращает текущее. Событие остановки доходит до ffmpeg через ту же проверку, что и отмена. В юнит добавлен `KillMode=mixed`. Повторный анализ больше не задваивает задание прокси.
- **Task 8** (`400e71c`, `f02b611`): интеграционные тесты на настоящем ffmpeg вскрыли реальный баг, который не ловили тесты с подменами: прокси писался во временный файл `proxy.mp4.part`, и ffmpeg отказывался выбирать контейнер по такому имени, то есть кодирование падало всегда. Формат теперь задаётся явно (`-f mp4`, `-f ipod`). Ожидания плана поправлены под реальность: сетка полоски 10 колонок, синтетический тон `sine` даёт пик около 33 из 255, ролик для проверки прокси взят крупнее 640 px, `worker_poll_sec` не может быть меньше 0.1.
- **Живая проверка на VM 2026-09-05** (`9591df1`): выкатка прошла, воркер поднялся, `/healthz` показывает пульс. Сквозной путь на ролике 1080p длиной 2 минуты: анализ 12 с, прокси 29 с, тишина найдена ровно там, где заглушена (40.008–50.016 при заглушке 40–50). Прокси 640×360 и 4 МБ из 78 МБ, полоска 60 кадров сеткой 10×6. Удаление ассета во время кодирования отменило задание, каталог удалён, обрезков `.part` не осталось, воркер выжил.
  **Найден баг, который не ловится локально:** поток пульса объявлял поле `_stop`, перекрывая внутренний метод `threading.Thread._stop`, и на Python 3.12 (боевая VM) `join()` падал с `TypeError` после каждого задания, после чего systemd перезапускал воркер. Локальный Python 3.14 этого не воспроизводит, поэтому все 267 тестов были зелёными. Поля переименованы, добавлен тест. Вывод на будущее: расхождение версий Python между разработкой и боем ловится только живым прогоном, смотреть счётчик `NRestarts` у юнита.
- **Task 9** (`1aba092`): в `thumbAt` координата 0 давала `-0`, который vitest отличает от нуля; добавлена явная развилка. Опрос статусов теперь прекращается только на `proxy_ready` и `failed`, потому что `ready` промежуточный. Поля `progress` в карточке ассета нет, прогресс задания появится вместе с API заданий.

## Что остаётся на M2

API проектов с проверкой и версиями (`projects`, `PUT` с `version`, 409 при устаревшей версии), интерфейс шкалы с блоками клипов и волной из пиков, плеер склейки двумя элементами `video`, автосохранение через 500 мс тишины, `snap_to_pauses` по картам из `analysis.json`, конвертация SRT в VTT при загрузке.
