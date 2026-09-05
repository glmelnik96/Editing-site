# M2a: проекты, проверка документа, версии, снэп к паузам

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** проект живёт на сервере как единый документ: список клипов с точками входа и выхода, музыка, субтитры, формат выхода. Документ проверяется целиком при каждом сохранении, времена нормализуются, резы с пометкой подтягиваются к измеренным паузам, одновременная правка ловится по версии. Интерфейс шкалы идёт отдельным планом M2b.

**Architecture:** новый пакет `server/app/projects/`: `doc.py` (чистая проверка и нормализация документа, без базы и диска), `snap.py` (подтяжка резов к паузам из `analysis.json`), `store.py` (работа с таблицей, версии, завершение), `routes.py` (`/api/v1/projects`). Миграция `0005_projects.sql`. Плюс конвертация субтитров SRT в VTT при загрузке (`server/media/subtitles.py`), потому что плеер в браузере понимает только VTT.

**Tech Stack:** FastAPI, SQLite (stdlib), Pydantic для тела запроса, чистые функции для правил документа.

**Спека:** `docs/superpowers/specs/2026-09-03-video-editor-design.md`, разделы 3, 4, 5, 6.3, 10.6. **Предыдущие планы:** `docs/superpowers/plans/2026-09-04-m1a-uploads-assets.md`, `docs/superpowers/plans/2026-09-04-m1b-worker-analyze.md`.

---

## Решения M2a

| Вопрос | Решение | Почему |
|---|---|---|
| Где правила документа | Чистая функция `validate_doc(raw, assets, settings)` без доступа к базе и диску | Правила проверяются на фикстурах, а не через HTTP; их много и они меняются |
| Идентификаторы клипов | Присланные сохраняются, пропущенные сервер выдаёт сам (`c1`, `c2`, …); дубликаты — ошибка | Агенту удобно не выдумывать идентификаторы, интерфейсу удобно их сохранять |
| Ошибки проверки | Одна ошибка `422 invalid_project` со списком в `details.errors`: путь поля и текст | Клиент подсвечивает конкретный клип, а не гадает |
| Версия | В теле `PUT`; несовпадение — `409 version_conflict` с актуальным проектом в `details.project` | Раздел 4 спеки: клиент перечитывает и показывает уведомление |
| Ассеты для клипов | Только видеоассеты владельца в статусе `ready` или `proxy_ready` | Звук монтировать нечем, `analyzing` ещё не имеет длительности |
| Снэп | По плотной карте пауз из `analysis.json`, окно ±0.35 с, буфер 0.3 с внутрь паузы, не дальше середины | Раздел 10.6 спеки |
| Снэп сломал клип | Откат к присланным значениям, флаг подтверждения `false` | Лучше неподтверждённая граница, чем испорченный клип |
| Нет `analysis.json` | Снэп не делается, флаги `false`, ошибки нет | Файл мог истечь по сроку, сохранять проект это не мешает |
| Завершение проекта | Удаляет ассеты владельца, не занятые другими незавершёнными проектами; сам проект остаётся с `status = finished` | Раздел 6.3 спеки. Рендеры появятся в M3 |
| Удаление ассета, занятого в проекте | `409 asset_in_use` со списком проектов | Обещано в M1a, где проверка была отложена |
| Субтитры | При загрузке ассета вида `subtitle` рядом кладётся `subs.vtt`; SRT конвертируется, VTT копируется с проверкой заголовка | Плеер понимает только VTT; конвертировать при каждом показе расточительно |
| Длительность ролика | Сумма `out − in` не больше 3 часов | Раздел 4 спеки |

## Структура файлов

| Файл | Обязанность |
|---|---|
| `server/db/migrations/0005_projects.sql` | Таблица `projects` |
| `server/app/config.py` | + пределы проекта и параметры снэпа |
| `server/app/projects/doc.py` | Проверка и нормализация документа, чистые функции |
| `server/app/projects/snap.py` | Чтение карты пауз, подтяжка резов |
| `server/app/projects/store.py` | Создание, чтение, список, сохранение с версией, удаление, завершение, «занят ли ассет» |
| `server/app/projects/routes.py` | `/api/v1/projects` |
| `server/app/assets/routes.py` | Запрет удаления ассета, занятого в проекте |
| `server/media/subtitles.py` | SRT → VTT |
| `server/app/uploads/store.py`, `server/app/assets/views.py` | Конвертация при загрузке и ссылка `vtt` в карточке |

Команды: `uv run python -m pytest`, `uv run ruff check .`, `cd web && npm test && npm run build`. Ветка: `m2a-projects` от `main`.

---

### Task 1: Миграция и настройки проекта

**Files:**
- Create: `server/db/migrations/0005_projects.sql`
- Modify: `server/app/config.py`, `.env.example`, `tests/test_db_migrate.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Тесты настроек**

Добавить в `tests/test_config.py`:

```python
def test_project_limits_have_sane_defaults():
    s = Settings(_env_file=None)
    assert s.max_clips == 100
    assert s.max_total_duration_sec == 3 * 3600
    assert s.min_clip_sec == 0.1
    assert s.snap_window_sec == 0.35
    assert s.snap_buffer_sec == 0.3
    assert s.max_projects_per_user == 200


def test_snap_window_is_bounded():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, snap_window_sec=-1)
```

В `tests/test_db_migrate.py` заменить оба ожидания списка версий: `[1, 2, 3, 4]` → `[1, 2, 3, 4, 5]` и `[2, 3, 4]` → `[2, 3, 4, 5]` (искать по `migrate(conn) ==`, правило из M1a). В множество `TABLES` добавить `"projects"`.

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_config.py tests/test_db_migrate.py`
Expected: FAIL.

- [ ] **Step 3: Настройки**

В `server/app/config.py` после блока обработки медиа добавить:

```python
    # Пределы проекта (раздел 4 спеки) и подтяжка резов к паузам (раздел 10.6).
    max_clips: int = Field(default=100, ge=1, le=1000)
    max_total_duration_sec: int = Field(default=3 * 3600, ge=1)
    min_clip_sec: float = Field(default=0.1, gt=0)
    snap_window_sec: float = Field(default=0.35, ge=0.0, le=5.0)
    snap_buffer_sec: float = Field(default=0.3, ge=0.0, le=5.0)
    max_projects_per_user: int = Field(default=200, ge=1)
```

В `.env.example` перед `VIDEO_LOG_LEVEL`:

```
# Проекты: пределы документа и подтяжка резов к паузам
VIDEO_MAX_CLIPS=100
VIDEO_MAX_TOTAL_DURATION_SEC=10800
VIDEO_SNAP_WINDOW_SEC=0.35
VIDEO_SNAP_BUFFER_SEC=0.3
```

- [ ] **Step 4: Миграция**

Создать `server/db/migrations/0005_projects.sql`:

```sql
-- doc: документ проекта целиком (JSON, раздел 4 спеки). Отдельных таблиц под клипы нет:
-- проект всегда сохраняется и читается целиком, а запросов «найди клип» не бывает.
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    doc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'finished')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX projects_user_idx ON projects(user_id, updated_at DESC);
```

- [ ] **Step 5: Прогнать тесты и линтер**

Run: `uv run python -m pytest && uv run ruff check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/db/migrations/0005_projects.sql server/app/config.py .env.example tests/test_config.py tests/test_db_migrate.py
git commit -m "feat(db): migration 0005 (projects) and project limits"
```

---

### Task 2: Проверка и нормализация документа

**Files:**
- Create: `server/app/projects/__init__.py` (пустой), `server/app/projects/doc.py`
- Test: `tests/test_project_doc.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_project_doc.py`:

```python
import pytest

from server.app.config import Settings
from server.app.projects.doc import AssetInfo, ProjectInvalid, validate_doc

S = Settings(_env_file=None)
ASSETS = {
    "ast_000000000001": AssetInfo(kind="video", status="proxy_ready", duration=120.0),
    "ast_000000000002": AssetInfo(kind="video", status="ready", duration=60.0),
    "ast_000000000003": AssetInfo(kind="audio", status="proxy_ready", duration=200.0),
    "ast_000000000004": AssetInfo(kind="subtitle", status="ready", duration=None),
    "ast_000000000005": AssetInfo(kind="video", status="analyzing", duration=None),
}


def clip(**over) -> dict:
    return {"asset_id": "ast_000000000001", "in": 1.0, "out": 5.0, **over}


def doc(**over) -> dict:
    return {"clips": [clip()], **over}


def errors_of(raw) -> list[str]:
    with pytest.raises(ProjectInvalid) as e:
        validate_doc(raw, assets=ASSETS, settings=S)
    return [item["field"] for item in e.value.errors]


def test_minimal_document_gets_defaults():
    out = validate_doc(doc(), assets=ASSETS, settings=S)
    assert out["output"] == {"aspect": "16:9", "fit": "pad", "fps": 30}
    assert out["music"] is None and out["subtitles"] is None
    c = out["clips"][0]
    assert c["id"] == "c1" and c["snap_to_pauses"] is False
    assert c["in_verified"] is False and c["out_verified"] is False
    assert c["in"] == 1.0 and c["out"] == 5.0


def test_times_are_rounded_to_milliseconds():
    out = validate_doc(doc(clips=[clip(**{"in": 1.00049, "out": 5.6667})]), assets=ASSETS, settings=S)
    assert out["clips"][0]["in"] == 1.0 and out["clips"][0]["out"] == 5.667


def test_client_cannot_set_verification_flags():
    """Флаги подтверждения выставляет только сервер (раздел 4 спеки)."""
    raw = doc(clips=[clip(in_verified=True, out_verified=True)])
    out = validate_doc(raw, assets=ASSETS, settings=S)
    assert out["clips"][0]["in_verified"] is False and out["clips"][0]["out_verified"] is False


def test_ids_are_kept_and_generated():
    raw = doc(clips=[clip(id="left"), clip(**{"in": 10, "out": 12})])
    out = validate_doc(raw, assets=ASSETS, settings=S)
    assert [c["id"] for c in out["clips"]] == ["left", "c2"]


def test_duplicate_ids_are_rejected():
    assert errors_of(doc(clips=[clip(id="x"), clip(id="x", **{"in": 9, "out": 10})])) == ["clips[1].id"]


def test_clip_count_bounds():
    assert errors_of({"clips": []}) == ["clips"]
    many = [clip(**{"in": 0, "out": 0.5}) for _ in range(S.max_clips + 1)]
    assert errors_of({"clips": many}) == ["clips"]


def test_clip_time_rules():
    assert errors_of(doc(clips=[clip(**{"in": 5, "out": 5})])) == ["clips[0].out"]
    assert errors_of(doc(clips=[clip(**{"in": 6, "out": 5})])) == ["clips[0].out"]
    assert errors_of(doc(clips=[clip(**{"in": -1, "out": 5})])) == ["clips[0].in"]
    assert errors_of(doc(clips=[clip(**{"in": 1, "out": 500})])) == ["clips[0].out"]
    assert errors_of(doc(clips=[clip(**{"in": 1.0, "out": 1.05})])) == ["clips[0].out"]


def test_clip_asset_rules():
    assert errors_of(doc(clips=[clip(asset_id="ast_00000000dead")])) == ["clips[0].asset_id"]
    assert errors_of(doc(clips=[clip(asset_id="ast_000000000003")])) == ["clips[0].asset_id"]  # звук
    assert errors_of(doc(clips=[clip(asset_id="ast_000000000005")])) == ["clips[0].asset_id"]  # не готов


def test_total_duration_limit():
    small = Settings(_env_file=None, max_total_duration_sec=10)
    raw = doc(clips=[clip(**{"in": 0, "out": 6}), clip(**{"in": 0, "out": 6})])
    with pytest.raises(ProjectInvalid) as e:
        validate_doc(raw, assets=ASSETS, settings=small)
    assert e.value.errors[0]["field"] == "clips"


def test_output_rules():
    out = validate_doc(doc(output={"aspect": "9:16", "fit": "crop", "fps": 50}), assets=ASSETS, settings=S)
    assert out["output"] == {"aspect": "9:16", "fit": "crop", "fps": 50}
    assert errors_of(doc(output={"aspect": "4:3"})) == ["output.aspect"]
    assert errors_of(doc(output={"fit": "stretch"})) == ["output.fit"]
    assert errors_of(doc(output={"fps": 24})) == ["output.fps"]


def test_music_rules():
    out = validate_doc(
        doc(music={"asset_id": "ast_000000000003", "volume": 0.25, "fade_in": 1, "fade_out": 2}),
        assets=ASSETS, settings=S,
    )
    assert out["music"] == {
        "asset_id": "ast_000000000003", "volume": 0.25, "fade_in": 1.0, "fade_out": 2.0, "loop": True,
    }
    assert errors_of(doc(music={"asset_id": "ast_000000000004"})) == ["music.asset_id"]
    assert errors_of(doc(music={"asset_id": "ast_000000000003", "volume": 2})) == ["music.volume"]
    assert errors_of(doc(music={"asset_id": "ast_000000000003", "fade_in": -1})) == ["music.fade_in"]


def test_subtitles_rules():
    out = validate_doc(
        doc(subtitles={"source": "file", "asset_id": "ast_000000000004", "mode": "soft"}),
        assets=ASSETS, settings=S,
    )
    assert out["subtitles"] == {
        "source": "file", "asset_id": "ast_000000000004", "mode": "soft", "style": "default",
    }
    assert errors_of(doc(subtitles={"source": "file", "asset_id": "ast_000000000001"})) == ["subtitles.asset_id"]
    assert errors_of(doc(subtitles={"source": "transcript", "asset_id": "ast_000000000004"})) == [
        "subtitles.asset_id"
    ]
    assert errors_of(doc(subtitles={"source": "guess", "asset_id": "ast_000000000004"})) == ["subtitles.source"]
    assert errors_of(
        doc(subtitles={"source": "file", "asset_id": "ast_000000000004", "mode": "glow"})
    ) == ["subtitles.mode"]


def test_wrong_shapes_do_not_crash():
    assert errors_of([]) == ["doc"]
    assert errors_of({"clips": "нет"}) == ["clips"]
    assert errors_of({"clips": ["строка"]}) == ["clips[0]"]
    assert errors_of(doc(output="широкий")) == ["output"]
    assert errors_of(doc(music=5)) == ["music"]
    assert errors_of(doc(clips=[clip(**{"in": "рано"})])) == ["clips[0].in"]


def test_all_errors_are_collected_not_just_the_first():
    raw = {"clips": [clip(**{"in": -1, "out": 500}), clip(asset_id="ast_00000000dead")], "output": {"fps": 24}}
    fields = errors_of(raw)
    assert "clips[0].in" in fields and "clips[0].out" in fields
    assert "clips[1].asset_id" in fields and "output.fps" in fields


def test_unknown_keys_are_dropped_not_echoed():
    out = validate_doc(doc(clips=[clip(evil="<script>")], extra=1), assets=ASSETS, settings=S)
    assert "extra" not in out and "evil" not in out["clips"][0]
    assert set(out) == {"output", "clips", "music", "subtitles"}
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_project_doc.py`
Expected: FAIL, нет модуля.

- [ ] **Step 3: Реализация**

Создать пустой `server/app/projects/__init__.py` и `server/app/projects/doc.py`:

```python
"""Проверка и нормализация документа проекта (раздел 4 спеки).

Чистые функции: ни базы, ни диска. О состоянии ассетов знают только через словарь AssetInfo,
который собирает вызывающий. Ошибки копятся списком, чтобы клиент увидел сразу все проблемы,
а не исправлял их по одной.
"""
from __future__ import annotations

from dataclasses import dataclass

from server.app.config import Settings

ASPECTS = ("16:9", "9:16", "1:1")
FITS = ("pad", "crop")
FPS_VALUES = (25, 30, 50, 60)
SUB_SOURCES = ("file", "transcript")
SUB_MODES = ("burn", "soft")
SUB_STYLES = ("default",)
CLIP_READY_STATUSES = ("ready", "proxy_ready")
TIME_DIGITS = 3


@dataclass(frozen=True)
class AssetInfo:
    kind: str
    status: str
    duration: float | None


class ProjectInvalid(Exception):
    def __init__(self, errors: list[dict]) -> None:
        super().__init__("документ проекта не прошёл проверку")
        self.errors = errors


class _Errors:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, field: str, message: str) -> None:
        self.items.append({"field": field, "message": message})

    def __bool__(self) -> bool:
        return bool(self.items)


def _number(value: object) -> float | None:
    """Числом считаем int и float, но не bool и не строку: «1» в поле времени — ошибка клиента."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _round(value: float) -> float:
    return round(value, TIME_DIGITS)


def _validate_output(raw: object, errors: _Errors) -> dict:
    out = {"aspect": "16:9", "fit": "pad", "fps": 30}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        errors.add("output", "output должен быть объектом")
        return out
    aspect = raw.get("aspect", out["aspect"])
    if aspect not in ASPECTS:
        errors.add("output.aspect", f"aspect: {', '.join(ASPECTS)}")
    else:
        out["aspect"] = aspect
    fit = raw.get("fit", out["fit"])
    if fit not in FITS:
        errors.add("output.fit", f"fit: {', '.join(FITS)}")
    else:
        out["fit"] = fit
    fps = raw.get("fps", out["fps"])
    if fps not in FPS_VALUES:
        errors.add("output.fps", f"fps: {', '.join(str(v) for v in FPS_VALUES)}")
    else:
        out["fps"] = int(fps)
    return out


def _validate_clip(
    raw: object, index: int, seen_ids: set[str], assets: dict[str, AssetInfo], settings: Settings,
    errors: _Errors,
) -> dict | None:
    where = f"clips[{index}]"
    if not isinstance(raw, dict):
        errors.add(where, "клип должен быть объектом")
        return None
    clip_id = raw.get("id")
    if clip_id is None:
        clip_id = f"c{index + 1}"
    elif not isinstance(clip_id, str) or not clip_id.strip():
        errors.add(f"{where}.id", "id клипа должен быть непустой строкой")
        return None
    if clip_id in seen_ids:
        errors.add(f"{where}.id", "id клипа повторяется")
        return None
    seen_ids.add(clip_id)

    asset_id = raw.get("asset_id")
    asset = assets.get(asset_id) if isinstance(asset_id, str) else None
    if asset is None:
        errors.add(f"{where}.asset_id", "нет такого ассета")
    elif asset.kind != "video":
        errors.add(f"{where}.asset_id", "в клип идёт только видеоассет")
    elif asset.status not in CLIP_READY_STATUSES:
        errors.add(f"{where}.asset_id", "ассет ещё не готов")
        asset = None
    elif asset.duration is None:
        errors.add(f"{where}.asset_id", "у ассета неизвестна длительность")
        asset = None

    start = _number(raw.get("in"))
    end = _number(raw.get("out"))
    if start is None:
        errors.add(f"{where}.in", "in должен быть числом секунд")
    elif start < 0:
        errors.add(f"{where}.in", "in не может быть отрицательным")
        start = None
    if end is None:
        errors.add(f"{where}.out", "out должен быть числом секунд")
    if start is not None and end is not None:
        if end - start < settings.min_clip_sec:
            errors.add(f"{where}.out", f"клип короче {settings.min_clip_sec} с")
            end = None
        elif asset is not None and asset.duration is not None and end > asset.duration + 1e-6:
            errors.add(f"{where}.out", "out за пределами длительности ассета")
            end = None
    if start is None or end is None or asset is None:
        return None
    return {
        "id": clip_id,
        "asset_id": asset_id,
        "in": _round(start),
        "out": _round(end),
        "snap_to_pauses": bool(raw.get("snap_to_pauses", False)),
        # Флаги подтверждения выставляет только сервер: присланные значения игнорируются.
        "in_verified": False,
        "out_verified": False,
    }


def _validate_music(raw: object, assets: dict[str, AssetInfo], errors: _Errors) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.add("music", "music должен быть объектом")
        return None
    asset_id = raw.get("asset_id")
    asset = assets.get(asset_id) if isinstance(asset_id, str) else None
    if asset is None or asset.kind not in ("audio", "video"):
        errors.add("music.asset_id", "музыкой может быть звуковой или видеоассет владельца")
        return None
    volume = _number(raw.get("volume", 0.25))
    if volume is None or not 0.0 <= volume <= 1.0:
        errors.add("music.volume", "volume от 0 до 1")
        return None
    fades = {}
    for key in ("fade_in", "fade_out"):
        value = _number(raw.get(key, 0.0))
        if value is None or value < 0:
            errors.add(f"music.{key}", f"{key} не может быть отрицательным")
            return None
        fades[key] = _round(value)
    return {
        "asset_id": asset_id,
        "volume": round(volume, 3),
        "fade_in": fades["fade_in"],
        "fade_out": fades["fade_out"],
        "loop": bool(raw.get("loop", True)),
    }


def _validate_subtitles(raw: object, assets: dict[str, AssetInfo], errors: _Errors) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        errors.add("subtitles", "subtitles должен быть объектом")
        return None
    source = raw.get("source")
    if source not in SUB_SOURCES:
        errors.add("subtitles.source", f"source: {', '.join(SUB_SOURCES)}")
        return None
    asset_id = raw.get("asset_id")
    asset = assets.get(asset_id) if isinstance(asset_id, str) else None
    want_kind = "subtitle" if source == "file" else "video"
    if asset is None or asset.kind != want_kind:
        errors.add("subtitles.asset_id", f"для source={source} нужен ассет вида {want_kind}")
        return None
    mode = raw.get("mode", "burn")
    if mode not in SUB_MODES:
        errors.add("subtitles.mode", f"mode: {', '.join(SUB_MODES)}")
        return None
    style = raw.get("style", "default")
    if style not in SUB_STYLES:
        errors.add("subtitles.style", f"style: {', '.join(SUB_STYLES)}")
        return None
    return {"source": source, "asset_id": asset_id, "mode": mode, "style": style}


def validate_doc(raw: object, *, assets: dict[str, AssetInfo], settings: Settings) -> dict:
    """Нормализованный документ или ProjectInvalid со списком ошибок.

    Возвращает ровно четыре ключа: неизвестные поля отбрасываются, чтобы клиент не мог протащить
    что-то в хранимый документ и получить обратно при чтении.
    """
    errors = _Errors()
    if not isinstance(raw, dict):
        raise ProjectInvalid([{"field": "doc", "message": "документ должен быть объектом"}])

    output = _validate_output(raw.get("output"), errors)
    raw_clips = raw.get("clips")
    clips: list[dict] = []
    if not isinstance(raw_clips, list):
        errors.add("clips", "clips должен быть списком")
    elif not raw_clips:
        errors.add("clips", "в проекте должен быть хотя бы один клип")
    elif len(raw_clips) > settings.max_clips:
        errors.add("clips", f"клипов больше {settings.max_clips}")
    else:
        seen: set[str] = set()
        for index, item in enumerate(raw_clips):
            clip = _validate_clip(item, index, seen, assets, settings, errors)
            if clip is not None:
                clips.append(clip)
        total = sum(c["out"] - c["in"] for c in clips)
        if total > settings.max_total_duration_sec:
            errors.add("clips", f"ролик длиннее {settings.max_total_duration_sec} с")

    music = _validate_music(raw.get("music"), assets, errors)
    subtitles = _validate_subtitles(raw.get("subtitles"), assets, errors)
    if errors:
        raise ProjectInvalid(errors.items)
    return {"output": output, "clips": clips, "music": music, "subtitles": subtitles}
```

- [ ] **Step 4: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_project_doc.py && uv run ruff check .`
Expected: PASS. Если ruff требует заменить `isinstance(value, (int, float))` на `isinstance(value, int | float)`, сделать так.

- [ ] **Step 5: Commit**

```bash
git add server/app/projects tests/test_project_doc.py
git commit -m "feat(projects): document validation and normalization"
```

---
### Task 3: Подтяжка резов к измеренным паузам

**Files:**
- Create: `server/app/projects/snap.py`
- Test: `tests/test_project_snap.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_project_snap.py`:

```python
import json

import pytest

from server.app.config import Settings
from server.app.projects.snap import load_silences, snap_clips, snap_in, snap_out
from server.app.storage import asset_dir

S = Settings(_env_file=None)
# Речь до 10.0, пауза 10.0–11.0, речь 11.0–20.0, пауза 20.0–20.4 (короткая), речь дальше.
PAUSES = [{"start": 10.0, "end": 11.0}, {"start": 20.0, "end": 20.4}]


def test_snap_in_moves_to_the_start_of_speech_with_a_buffer():
    """in подтягивается к концу паузы и отступает буфером внутрь паузы (раздел 10.6)."""
    assert snap_in(10.9, PAUSES, window=0.35, buffer=0.3) == 10.7
    assert snap_in(11.2, PAUSES, window=0.35, buffer=0.3) == 10.7


def test_snap_in_never_goes_past_the_middle_of_a_short_pause():
    assert snap_out(20.2, PAUSES, window=0.35, buffer=0.3) == 20.2  # середина паузы 20.0–20.4
    assert snap_in(20.3, PAUSES, window=0.35, buffer=0.3) == 20.2


def test_snap_out_moves_to_the_end_of_speech_with_a_buffer():
    assert snap_out(9.9, PAUSES, window=0.35, buffer=0.3) == 10.3
    assert snap_out(10.2, PAUSES, window=0.35, buffer=0.3) == 10.3


def test_no_pause_in_the_window_leaves_the_value_alone():
    assert snap_in(5.0, PAUSES, window=0.35, buffer=0.3) is None
    assert snap_out(15.0, PAUSES, window=0.35, buffer=0.3) is None
    assert snap_in(1.0, [], window=0.35, buffer=0.3) is None


def test_the_nearest_edge_wins():
    pauses = [{"start": 4.0, "end": 5.0}, {"start": 5.2, "end": 6.0}]
    assert snap_in(5.15, pauses, window=0.35, buffer=0.3) == 4.7  # конец 5.0 ближе, чем 6.0


def test_snap_clips_sets_flags_and_leaves_others_alone(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    folder = asset_dir(settings, "usr_00000000000a", "ast_000000000001")
    folder.mkdir(parents=True)
    (folder / "analysis.json").write_text(
        json.dumps({"silences": [], "silences_dense": PAUSES}), encoding="utf-8"
    )
    clips = [
        {"id": "c1", "asset_id": "ast_000000000001", "in": 10.9, "out": 20.2,
         "snap_to_pauses": True, "in_verified": False, "out_verified": False},
        {"id": "c2", "asset_id": "ast_000000000001", "in": 10.9, "out": 20.2,
         "snap_to_pauses": False, "in_verified": False, "out_verified": False},
    ]
    snap_clips(clips, settings=settings, user_id="usr_00000000000a")
    assert clips[0]["in"] == 10.7 and clips[0]["in_verified"] is True
    assert clips[0]["out"] == 20.2 and clips[0]["out_verified"] is True
    assert clips[1]["in"] == 10.9 and clips[1]["in_verified"] is False


def test_snap_is_rolled_back_when_it_would_break_the_clip():
    """Подтяжка не имеет права сделать клип нулевым или перевёрнутым."""
    pauses = [{"start": 0.0, "end": 5.0}]
    clips = [{"id": "c1", "asset_id": "a", "in": 4.9, "out": 5.1,
              "snap_to_pauses": True, "in_verified": False, "out_verified": False}]
    snap_clips(clips, silences_by_asset={"a": pauses}, settings=S)
    assert clips[0]["in"] == 4.9 and clips[0]["out"] == 5.1
    assert clips[0]["in_verified"] is False and clips[0]["out_verified"] is False


def test_missing_analysis_file_is_not_an_error(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    clips = [{"id": "c1", "asset_id": "ast_000000000009", "in": 1.0, "out": 2.0,
              "snap_to_pauses": True, "in_verified": False, "out_verified": False}]
    snap_clips(clips, settings=settings, user_id="usr_00000000000a")
    assert clips[0]["in"] == 1.0 and clips[0]["in_verified"] is False


def test_broken_analysis_file_is_not_an_error(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    folder = asset_dir(settings, "usr_00000000000a", "ast_000000000001")
    folder.mkdir(parents=True)
    (folder / "analysis.json").write_text("{не json", encoding="utf-8")
    assert load_silences(settings, "usr_00000000000a", "ast_000000000001") == []


def test_load_silences_prefers_the_dense_map(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    folder = asset_dir(settings, "usr_00000000000a", "ast_000000000001")
    folder.mkdir(parents=True)
    (folder / "analysis.json").write_text(
        json.dumps({"silences": [{"start": 1, "end": 2}], "silences_dense": [{"start": 3, "end": 4}]}),
        encoding="utf-8",
    )
    assert load_silences(settings, "usr_00000000000a", "ast_000000000001") == [{"start": 3.0, "end": 4.0}]


def test_zero_window_disables_snapping():
    assert snap_in(11.0, PAUSES, window=0.0, buffer=0.3) == 10.7
    assert snap_in(11.01, PAUSES, window=0.0, buffer=0.3) is None
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_project_snap.py`
Expected: FAIL, нет модуля.

- [ ] **Step 3: Реализация**

Создать `server/app/projects/snap.py`:

```python
"""Подтяжка точек реза к измеренным паузам (раздел 10.6 спеки).

Правило: рез ставится не там, где попросил клиент, а на краю ближайшей измеренной паузы, отступив
внутрь неё на буфер. Речь при этом не обрезается. Если подходящей паузы рядом нет, значение остаётся
как есть, а флаг подтверждения — false: «проверить нечем» честнее, чем двигать вслепую.
"""
from __future__ import annotations

import json
import logging

from server.app.config import Settings
from server.app.storage import asset_dir

log = logging.getLogger("video.projects")

ANALYSIS_NAME = "analysis.json"


def load_silences(settings: Settings, user_id: str, asset_id: str) -> list[dict]:
    """Плотная карта пауз ассета. Файла нет или он битый — пустой список, это не ошибка."""
    path = asset_dir(settings, user_id, asset_id) / ANALYSIS_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    raw = data.get("silences_dense") or data.get("silences") or []
    out: list[dict] = []
    for item in raw:
        try:
            start, end = float(item["start"]), float(item["end"])
        except (TypeError, KeyError, ValueError):
            continue
        if end > start:
            out.append({"start": start, "end": end})
    return out


def _edge_within(value: float, edges: list[tuple[float, float]], window: float) -> tuple[float, float] | None:
    """Ближайшая пауза, чей нужный край не дальше окна. edges: (край, другой край паузы)."""
    best: tuple[float, float] | None = None
    best_distance = window
    for edge, other in edges:
        distance = abs(edge - value)
        if distance <= best_distance:
            best_distance = distance
            best = (edge, other)
    return best


def snap_in(value: float, silences: list[dict], *, window: float, buffer: float) -> float | None:
    """Точка входа встаёт перед началом речи: конец паузы минус буфер, но не дальше её середины."""
    edges = [(p["end"], p["start"]) for p in silences]
    found = _edge_within(value, edges, window)
    if found is None:
        return None
    end, start = found
    middle = (start + end) / 2
    return round(max(middle, end - buffer), 3)


def snap_out(value: float, silences: list[dict], *, window: float, buffer: float) -> float | None:
    """Точка выхода встаёт после конца речи: начало паузы плюс буфер, но не дальше её середины."""
    edges = [(p["start"], p["end"]) for p in silences]
    found = _edge_within(value, edges, window)
    if found is None:
        return None
    start, end = found
    middle = (start + end) / 2
    return round(min(middle, start + buffer), 3)


def snap_clips(
    clips: list[dict],
    *,
    settings: Settings,
    user_id: str | None = None,
    silences_by_asset: dict[str, list[dict]] | None = None,
) -> None:
    """Правит клипы на месте: время и флаги подтверждения. Карты пауз читаются по одному разу на ассет.

    silences_by_asset задают тесты; в бою карта читается с диска по user_id.
    """
    cache: dict[str, list[dict]] = dict(silences_by_asset or {})
    for clip in clips:
        if not clip.get("snap_to_pauses"):
            continue
        asset_id = clip["asset_id"]
        if asset_id not in cache:
            cache[asset_id] = load_silences(settings, user_id, asset_id) if user_id else []
        silences = cache[asset_id]
        if not silences:
            continue
        new_in = snap_in(clip["in"], silences, window=settings.snap_window_sec, buffer=settings.snap_buffer_sec)
        new_out = snap_out(clip["out"], silences, window=settings.snap_window_sec, buffer=settings.snap_buffer_sec)
        candidate_in = clip["in"] if new_in is None else new_in
        candidate_out = clip["out"] if new_out is None else new_out
        if candidate_out - candidate_in < settings.min_clip_sec or candidate_in < 0:
            # Подтяжка сломала бы клип: откатываемся целиком, границы остаются неподтверждёнными.
            log.debug("снэп откачен для клипа %s", clip.get("id"))
            continue
        clip["in"], clip["out"] = candidate_in, candidate_out
        clip["in_verified"] = new_in is not None
        clip["out_verified"] = new_out is not None
```

- [ ] **Step 4: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_project_snap.py && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/app/projects/snap.py tests/test_project_snap.py
git commit -m "feat(projects): snap cut points to measured pauses"
```

---

### Task 4: Хранилище проектов

**Files:**
- Create: `server/app/projects/store.py`
- Test: `tests/test_project_store.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_project_store.py`:

```python
import json

import pytest

from server.app.config import Settings
from server.app.projects.doc import ProjectInvalid
from server.app.projects.store import (
    ProjectConflict,
    ProjectLimit,
    assets_of,
    create_project,
    delete_project,
    finish_project,
    get_project,
    list_projects,
    projects_using_asset,
    save_project,
)
from server.app.storage import asset_dir
from server.app.util import now_iso
from server.db.core import connect
from server.db.migrate import migrate

USER = "usr_00000000000a"
OTHER = "usr_00000000000b"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data")


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True)
    c = connect(settings.db_path)
    migrate(c)
    for uid in (USER, OTHER):
        c.execute(
            "INSERT INTO users (id, email, name, created_at) VALUES (?, ?, 'U', ?)",
            (uid, f"{uid}@ya.ru", now_iso()),
        )
    for asset_id, kind, duration in (
        ("ast_000000000001", "video", 120.0),
        ("ast_000000000002", "video", 60.0),
        ("ast_000000000003", "audio", 200.0),
    ):
        c.execute(
            "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
            "created_at, last_access_at) VALUES (?, ?, ?, 'a', 'mp4', 1, 'ready', ?, ?, ?)",
            (asset_id, USER, kind, duration, now_iso(), now_iso()),
        )
        folder = asset_dir(settings, USER, asset_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "source.mp4").write_bytes(b"x")
    yield c
    c.close()


def doc(asset="ast_000000000001", **over) -> dict:
    return {"clips": [{"asset_id": asset, "in": 1.0, "out": 5.0}], **over}


def test_create_returns_a_normalized_project(conn, settings):
    p = create_project(conn, settings, USER, name="Подкаст", raw_doc=doc())
    assert p["id"].startswith("prj_") and p["version"] == 1 and p["status"] == "draft"
    assert p["name"] == "Подкаст"
    assert p["doc"]["clips"][0]["id"] == "c1"
    assert p["doc"]["output"]["aspect"] == "16:9"
    assert p["created_at"] == p["updated_at"] and p["finished_at"] is None


def test_create_without_a_document_starts_empty(conn, settings):
    p = create_project(conn, settings, USER, name="Пустой", raw_doc=None)
    assert p["doc"]["clips"] == [] and p["version"] == 1


def test_name_is_trimmed_and_required(conn, settings):
    p = create_project(conn, settings, USER, name="  Ролик  ", raw_doc=None)
    assert p["name"] == "Ролик"
    with pytest.raises(ProjectInvalid) as e:
        create_project(conn, settings, USER, name="   ", raw_doc=None)
    assert e.value.errors[0]["field"] == "name"


def test_project_count_limit(conn, settings):
    small = Settings(_env_file=None, data_dir=settings.data_dir, max_projects_per_user=2)
    create_project(conn, small, USER, name="1", raw_doc=None)
    create_project(conn, small, USER, name="2", raw_doc=None)
    with pytest.raises(ProjectLimit):
        create_project(conn, small, USER, name="3", raw_doc=None)


def test_get_and_list_are_scoped_to_the_owner(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    assert get_project(conn, USER, p["id"])["name"] == "Мой"
    assert get_project(conn, OTHER, p["id"]) is None
    assert [x["id"] for x in list_projects(conn, USER)] == [p["id"]]
    assert list_projects(conn, OTHER) == []


def test_list_does_not_carry_the_whole_document(conn, settings):
    create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    row = list_projects(conn, USER)[0]
    assert "doc" not in row and row["clips_count"] == 1
    assert row["duration"] == 4.0


def test_save_bumps_the_version_and_normalizes(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    saved = save_project(conn, settings, USER, p["id"], name="Другое", raw_doc=doc(**{"output": {"fps": 50}}), version=1)
    assert saved["version"] == 2 and saved["name"] == "Другое"
    assert saved["doc"]["output"]["fps"] == 50
    assert saved["updated_at"] >= p["updated_at"]


def test_stale_version_conflicts_and_returns_the_current_project(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=doc(), version=1)
    with pytest.raises(ProjectConflict) as e:
        save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=doc(), version=1)
    assert e.value.project["version"] == 2


def test_save_applies_snapping(conn, settings):
    folder = asset_dir(settings, USER, "ast_000000000001")
    (folder / "analysis.json").write_text(
        json.dumps({"silences_dense": [{"start": 4.0, "end": 5.0}]}), encoding="utf-8"
    )
    p = create_project(conn, settings, USER, name="Мой", raw_doc=None)
    raw = {"clips": [{"asset_id": "ast_000000000001", "in": 1.0, "out": 4.1, "snap_to_pauses": True}]}
    saved = save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=raw, version=1)
    clip = saved["doc"]["clips"][0]
    assert clip["out"] == 4.3 and clip["out_verified"] is True
    assert clip["in"] == 1.0 and clip["in_verified"] is False


def test_save_rejects_a_foreign_asset(conn, settings):
    conn.execute(
        "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
        "created_at, last_access_at) VALUES ('ast_00000000000f', ?, 'video', 'a', 'mp4', 1, 'ready', 9, ?, ?)",
        (OTHER, now_iso(), now_iso()),
    )
    p = create_project(conn, settings, USER, name="Мой", raw_doc=None)
    with pytest.raises(ProjectInvalid) as e:
        save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=doc("ast_00000000000f"), version=1)
    assert e.value.errors[0]["field"] == "clips[0].asset_id"


def test_save_touches_the_assets_it_uses(conn, settings):
    old = "2020-01-01T00:00:00.000Z"
    conn.execute("UPDATE assets SET last_access_at = ? WHERE id = 'ast_000000000001'", (old,))
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    assert conn.execute(
        "SELECT last_access_at FROM assets WHERE id = 'ast_000000000001'"
    ).fetchone()[0] > old
    assert p["id"]


def test_delete_is_scoped_and_returns_whether_it_deleted(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    assert delete_project(conn, OTHER, p["id"]) is False
    assert delete_project(conn, USER, p["id"]) is True
    assert get_project(conn, USER, p["id"]) is None


def test_assets_of_lists_every_referenced_asset(conn, settings):
    raw = {
        "clips": [{"asset_id": "ast_000000000001", "in": 0, "out": 2},
                  {"asset_id": "ast_000000000002", "in": 0, "out": 2}],
        "music": {"asset_id": "ast_000000000003"},
    }
    p = create_project(conn, settings, USER, name="Мой", raw_doc=raw)
    assert assets_of(p["doc"]) == {"ast_000000000001", "ast_000000000002", "ast_000000000003"}


def test_projects_using_asset_ignores_finished_ones(conn, settings):
    a = create_project(conn, settings, USER, name="Живой", raw_doc=doc())
    b = create_project(conn, settings, USER, name="Готовый", raw_doc=doc())
    finish_project(conn, settings, USER, b["id"])
    using = projects_using_asset(conn, USER, "ast_000000000001")
    assert [x["id"] for x in using] == [a["id"]]


def test_finish_deletes_assets_that_nobody_else_needs(conn, settings):
    shared = create_project(conn, settings, USER, name="Общий", raw_doc=doc("ast_000000000001"))
    done = create_project(conn, settings, USER, name="Готовый", raw_doc={
        "clips": [{"asset_id": "ast_000000000001", "in": 0, "out": 2},
                  {"asset_id": "ast_000000000002", "in": 0, "out": 2}],
    })
    result = finish_project(conn, settings, USER, done["id"])
    assert result["status"] == "finished" and result["finished_at"]
    # ast_000000000001 остаётся: он нужен другому незавершённому проекту
    assert conn.execute("SELECT count(*) FROM assets WHERE id = 'ast_000000000001'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM assets WHERE id = 'ast_000000000002'").fetchone()[0] == 0
    assert not asset_dir(settings, USER, "ast_000000000002").exists()
    assert get_project(conn, USER, shared["id"])["doc"]["clips"]  # чужой проект цел


def test_finishing_twice_is_harmless(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    finish_project(conn, settings, USER, p["id"])
    again = finish_project(conn, settings, USER, p["id"])
    assert again["status"] == "finished"


def test_finished_project_cannot_be_saved(conn, settings):
    p = create_project(conn, settings, USER, name="Мой", raw_doc=doc())
    finish_project(conn, settings, USER, p["id"])
    with pytest.raises(ProjectInvalid) as e:
        save_project(conn, settings, USER, p["id"], name="Мой", raw_doc=doc(), version=p["version"])
    assert e.value.errors[0]["field"] == "status"
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_project_store.py`
Expected: FAIL, нет модуля.

- [ ] **Step 3: Реализация**

Создать `server/app/projects/store.py`:

```python
"""Проекты в базе: создание, чтение, сохранение целиком с версией, удаление, завершение.

Документ хранится одной строкой JSON: он всегда читается и пишется целиком, точечных операций
«добавь клип» нет по решению из раздела 2 спеки.
"""
from __future__ import annotations

import json
import shutil
import sqlite3

from server.app.config import Settings
from server.app.projects.doc import AssetInfo, ProjectInvalid, validate_doc
from server.app.projects.snap import snap_clips
from server.app.storage import asset_dir
from server.app.util import new_id, now_iso
from server.db.core import transaction

MAX_NAME = 200
EMPTY_DOC = {"output": {"aspect": "16:9", "fit": "pad", "fps": 30}, "clips": [], "music": None, "subtitles": None}


class ProjectConflict(Exception):
    """Сохранение поверх чужой правки: у клиента устаревшая версия."""

    def __init__(self, project: dict) -> None:
        super().__init__("версия проекта устарела")
        self.project = project


class ProjectLimit(Exception):
    pass


def _row_to_project(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
        "doc": json.loads(row["doc"]),
    }


def _clean_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned or len(cleaned) > MAX_NAME:
        raise ProjectInvalid([{"field": "name", "message": f"имя проекта от 1 до {MAX_NAME} знаков"}])
    return cleaned


def _assets_index(conn: sqlite3.Connection, user_id: str) -> dict[str, AssetInfo]:
    rows = conn.execute("SELECT id, kind, status, duration FROM assets WHERE user_id = ?", (user_id,))
    return {r["id"]: AssetInfo(kind=r["kind"], status=r["status"], duration=r["duration"]) for r in rows}


def assets_of(doc: dict) -> set[str]:
    """Все ассеты, на которые ссылается документ: клипы, музыка, субтитры."""
    used = {c["asset_id"] for c in doc.get("clips") or []}
    for key in ("music", "subtitles"):
        block = doc.get(key)
        if isinstance(block, dict) and block.get("asset_id"):
            used.add(block["asset_id"])
    return used


def _prepare(conn: sqlite3.Connection, settings: Settings, user_id: str, raw_doc: object) -> dict:
    """Проверка документа плюс подтяжка резов к паузам."""
    if raw_doc is None:
        return json.loads(json.dumps(EMPTY_DOC))
    doc = validate_doc(raw_doc, assets=_assets_index(conn, user_id), settings=settings)
    snap_clips(doc["clips"], settings=settings, user_id=user_id)
    return doc


def _touch_assets(conn: sqlite3.Connection, doc: dict) -> None:
    """Проект держит ассеты живыми: janitor чистит по последнему обращению (раздел 3 спеки)."""
    used = assets_of(doc)
    if used:
        marks = ",".join("?" * len(used))
        conn.execute(
            f"UPDATE assets SET last_access_at = ? WHERE id IN ({marks})",  # noqa: S608 — маркеры, не данные
            (now_iso(), *used),
        )


def create_project(
    conn: sqlite3.Connection, settings: Settings, user_id: str, *, name: str, raw_doc: object
) -> dict:
    name = _clean_name(name)
    doc = _prepare(conn, settings, user_id, raw_doc)
    now = now_iso()
    project_id = new_id("prj")
    with transaction(conn):
        count = conn.execute(
            "SELECT count(*) FROM projects WHERE user_id = ? AND status = 'draft'", (user_id,)
        ).fetchone()[0]
        if count >= settings.max_projects_per_user:
            raise ProjectLimit(f"больше {settings.max_projects_per_user} проектов в работе")
        conn.execute(
            "INSERT INTO projects (id, user_id, name, version, doc, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, 'draft', ?, ?)",
            (project_id, user_id, name, json.dumps(doc, ensure_ascii=False), now, now),
        )
        _touch_assets(conn, doc)
    return {
        "id": project_id, "name": name, "version": 1, "status": "draft",
        "created_at": now, "updated_at": now, "finished_at": None, "doc": doc,
    }


def get_project(conn: sqlite3.Connection, user_id: str, project_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id)
    ).fetchone()
    return _row_to_project(row) if row else None


def list_projects(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """Карточки без документа: список проектов не должен тащить сотни клипов."""
    rows = conn.execute(
        "SELECT id, name, version, status, created_at, updated_at, finished_at, doc FROM projects "
        "WHERE user_id = ? ORDER BY updated_at DESC, id",
        (user_id,),
    )
    out = []
    for row in rows:
        doc = json.loads(row["doc"])
        clips = doc.get("clips") or []
        out.append({
            "id": row["id"], "name": row["name"], "version": row["version"], "status": row["status"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "finished_at": row["finished_at"],
            "clips_count": len(clips),
            "duration": round(sum(c["out"] - c["in"] for c in clips), 3),
        })
    return out


def save_project(
    conn: sqlite3.Connection, settings: Settings, user_id: str, project_id: str, *,
    name: str, raw_doc: object, version: int,
) -> dict:
    current = get_project(conn, user_id, project_id)
    if current is None:
        raise KeyError(project_id)
    if current["status"] != "draft":
        raise ProjectInvalid([{"field": "status", "message": "завершённый проект не редактируется"}])
    if current["version"] != version:
        raise ProjectConflict(current)
    name = _clean_name(name)
    doc = _prepare(conn, settings, user_id, raw_doc)
    now = now_iso()
    with transaction(conn):
        cur = conn.execute(
            "UPDATE projects SET name = ?, doc = ?, version = version + 1, updated_at = ? "
            "WHERE id = ? AND user_id = ? AND version = ? AND status = 'draft'",
            (name, json.dumps(doc, ensure_ascii=False), now, project_id, user_id, version),
        )
        if cur.rowcount == 0:
            # Кто-то сохранил проект между нашей проверкой и записью.
            raise ProjectConflict(get_project(conn, user_id, project_id) or current)
        _touch_assets(conn, doc)
    return {
        "id": project_id, "name": name, "version": version + 1, "status": "draft",
        "created_at": current["created_at"], "updated_at": now, "finished_at": None, "doc": doc,
    }


def delete_project(conn: sqlite3.Connection, user_id: str, project_id: str) -> bool:
    with transaction(conn):
        cur = conn.execute(
            "DELETE FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id)
        )
    return cur.rowcount > 0


def projects_using_asset(conn: sqlite3.Connection, user_id: str, asset_id: str) -> list[dict]:
    """Незавершённые проекты владельца, где встречается ассет. Документов мало, ищем перебором."""
    rows = conn.execute(
        "SELECT id, name, doc FROM projects WHERE user_id = ? AND status = 'draft'", (user_id,)
    )
    return [
        {"id": r["id"], "name": r["name"]}
        for r in rows
        if asset_id in assets_of(json.loads(r["doc"]))
    ]


def finish_project(conn: sqlite3.Connection, settings: Settings, user_id: str, project_id: str) -> dict:
    """Завершение: проект остаётся историей, а его ассеты удаляются, если больше нигде не нужны.

    Рендеры появятся в M3 и будут удаляться здесь же.
    """
    project = get_project(conn, user_id, project_id)
    if project is None:
        raise KeyError(project_id)
    now = now_iso()
    if project["status"] == "draft":
        with transaction(conn):
            conn.execute(
                "UPDATE projects SET status = 'finished', finished_at = ?, updated_at = ? WHERE id = ?",
                (now, now, project_id),
            )
        project = {**project, "status": "finished", "finished_at": now, "updated_at": now}
    for asset_id in sorted(assets_of(project["doc"])):
        if projects_using_asset(conn, user_id, asset_id):
            continue
        with transaction(conn):
            conn.execute("DELETE FROM assets WHERE id = ? AND user_id = ?", (asset_id, user_id))
        shutil.rmtree(asset_dir(settings, user_id, asset_id), ignore_errors=True)
    return project
```

- [ ] **Step 4: Прогнать тесты и линтер**

Run: `uv run python -m pytest tests/test_project_store.py && uv run ruff check .`
Expected: PASS. Если `# noqa: S608` окажется лишним (правило не включено), убрать.

- [ ] **Step 5: Commit**

```bash
git add server/app/projects/store.py tests/test_project_store.py
git commit -m "feat(projects): store with versions, snapping and finishing"
```

---
### Task 5: Маршруты `/api/v1/projects` и запрет удаления занятого ассета

**Files:**
- Create: `server/app/projects/routes.py`
- Modify: `server/app/main.py`, `server/app/assets/routes.py`
- Test: `tests/test_projects_api.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_projects_api.py`:

```python
import sqlite3

from server.app.util import now_iso

VIDEO = "ast_000000000001"
AUDIO = "ast_000000000003"


def seed_assets(client, settings, user_id):
    """Готовые ассеты прямо в базе: путь загрузки и обработки уже проверен другими тестами."""
    conn = sqlite3.connect(str(settings.db_path))
    for asset_id, kind, duration in ((VIDEO, "video", 120.0), (AUDIO, "audio", 200.0)):
        conn.execute(
            "INSERT INTO assets (id, user_id, kind, original_name, ext, size, status, duration, "
            "created_at, last_access_at) VALUES (?, ?, ?, 'a', 'mp4', 1, 'ready', ?, ?, ?)",
            (asset_id, user_id, kind, duration, now_iso(), now_iso()),
        )
    conn.commit()
    conn.close()


def doc(**over):
    return {"clips": [{"asset_id": VIDEO, "in": 1.0, "out": 5.0}], **over}


def test_create_read_list_and_save(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])

    r = client.post("/api/v1/projects", json={"name": "Подкаст", "doc": doc()})
    assert r.status_code == 201, r.text
    project = r.json()
    assert project["version"] == 1 and project["status"] == "draft"
    assert project["doc"]["clips"][0]["id"] == "c1"
    assert project["doc"]["clips"][0]["in_verified"] is False

    listing = client.get("/api/v1/projects").json()["projects"]
    assert len(listing) == 1 and listing[0]["clips_count"] == 1 and "doc" not in listing[0]

    got = client.get(f"/api/v1/projects/{project['id']}").json()
    assert got["doc"] == project["doc"]

    r = client.put(
        f"/api/v1/projects/{project['id']}",
        json={"name": "Подкаст 2", "version": 1, "doc": doc(output={"aspect": "9:16"})},
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and r.json()["doc"]["output"]["aspect"] == "9:16"


def test_create_without_a_document(client, login_as, settings):
    login_as()
    r = client.post("/api/v1/projects", json={"name": "Пустой"})
    assert r.status_code == 201 and r.json()["doc"]["clips"] == []


def test_validation_errors_list_every_bad_field(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    r = client.post("/api/v1/projects", json={"name": "Плохой", "doc": {
        "clips": [{"asset_id": VIDEO, "in": -1, "out": 5}], "output": {"fps": 24},
    }})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "invalid_project"
    fields = {e["field"] for e in body["details"]["errors"]}
    assert fields == {"clips[0].in", "output.fps"}


def test_stale_version_returns_409_with_the_current_project(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    client.put(f"/api/v1/projects/{p['id']}", json={"name": "Мой", "version": 1, "doc": doc()})
    r = client.put(f"/api/v1/projects/{p['id']}", json={"name": "Мой", "version": 1, "doc": doc()})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "version_conflict"
    assert err["details"]["project"]["version"] == 2
    assert err["details"]["project"]["doc"]["clips"]


def test_foreign_project_is_404(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get(f"/api/v1/projects/{p['id']}").status_code == 404
    assert client.put(f"/api/v1/projects/{p['id']}", json={"name": "x", "version": 1, "doc": doc()}).status_code == 404
    assert client.delete(f"/api/v1/projects/{p['id']}").status_code == 404
    assert client.post(f"/api/v1/projects/{p['id']}/finish").status_code == 404
    assert client.get("/api/v1/projects").json()["projects"] == []


def test_delete_project(client, login_as, settings):
    login_as()
    p = client.post("/api/v1/projects", json={"name": "Мой"}).json()
    assert client.delete(f"/api/v1/projects/{p['id']}").status_code == 204
    assert client.get(f"/api/v1/projects/{p['id']}").status_code == 404
    assert client.delete(f"/api/v1/projects/{p['id']}").status_code == 404


def test_finish_marks_the_project_and_frees_assets(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    r = client.post(f"/api/v1/projects/{p['id']}/finish")
    assert r.status_code == 200 and r.json()["status"] == "finished"
    assert client.get(f"/api/v1/assets/{VIDEO}").status_code == 404
    r = client.put(f"/api/v1/projects/{p['id']}", json={"name": "Мой", "version": 2, "doc": doc()})
    assert r.status_code == 422 and r.json()["error"]["details"]["errors"][0]["field"] == "status"


def test_asset_in_use_cannot_be_deleted(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_assets(client, settings, me["id"])
    p = client.post("/api/v1/projects", json={"name": "Мой", "doc": doc()}).json()
    r = client.delete(f"/api/v1/assets/{VIDEO}")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "asset_in_use"
    assert err["details"]["projects"] == [{"id": p["id"], "name": "Мой"}]
    assert client.delete(f"/api/v1/assets/{AUDIO}").status_code == 204  # музыка нигде не занята
    assert client.delete(f"/api/v1/projects/{p['id']}").status_code == 204
    assert client.delete(f"/api/v1/assets/{VIDEO}").status_code == 204  # проект удалён — ассет свободен


def test_agent_can_drive_projects_with_a_token(bearer_client, settings):
    me = bearer_client.get("/api/v1/me").json()
    seed_assets(bearer_client, settings, me["id"])
    p = bearer_client.post("/api/v1/projects", json={"name": "Агентский", "doc": doc()}).json()
    saved = bearer_client.put(
        f"/api/v1/projects/{p['id']}",
        json={"name": "Агентский", "version": 1, "doc": doc(music={"asset_id": AUDIO, "volume": 0.2})},
    )
    assert saved.status_code == 200 and saved.json()["doc"]["music"]["volume"] == 0.2


def test_projects_require_auth(client):
    assert client.get("/api/v1/projects").status_code == 401
    assert client.post("/api/v1/projects", json={"name": "x"}).status_code == 401
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_projects_api.py`
Expected: FAIL, маршрутов нет.

- [ ] **Step 3: Маршруты**

Создать `server/app/projects/routes.py`:

```python
"""Проекты: /api/v1/projects. Документ приходит и уходит целиком, версия защищает от гонки правок."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from server.app.auth.deps import CurrentUser, current_user
from server.app.errors import ApiError
from server.app.projects.doc import ProjectInvalid
from server.app.projects.store import (
    ProjectConflict,
    ProjectLimit,
    create_project,
    delete_project,
    finish_project,
    get_project,
    list_projects,
    save_project,
)
from server.db.core import get_db

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    doc: dict | None = None


class ProjectSave(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    doc: dict | None = None


class ProjectView(BaseModel):
    id: str
    name: str
    version: int
    status: str
    created_at: str
    updated_at: str
    finished_at: str | None
    doc: dict[str, Any]


class ProjectCard(BaseModel):
    id: str
    name: str
    version: int
    status: str
    created_at: str
    updated_at: str
    finished_at: str | None
    clips_count: int
    duration: float


class ProjectList(BaseModel):
    projects: list[ProjectCard]


def invalid(exc: ProjectInvalid) -> ApiError:
    return ApiError(422, "invalid_project", "Документ проекта не прошёл проверку", {"errors": exc.errors})


def conflict(exc: ProjectConflict) -> ApiError:
    return ApiError(
        409, "version_conflict", "Проект изменился, перечитайте его", {"project": exc.project}
    )


def _owned(conn: sqlite3.Connection, user: CurrentUser, project_id: str) -> dict:
    project = get_project(conn, user.id, project_id)
    if project is None:
        raise ApiError(404, "not_found", "Проект не найден")
    return project


@router.get("", response_model=ProjectList)
def list_(
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectList:
    return ProjectList(projects=[ProjectCard(**p) for p in list_projects(conn, user.id)])


@router.post("", status_code=201, response_model=ProjectView)
def create(
    body: ProjectCreate,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    try:
        project = create_project(
            conn, request.app.state.settings, user.id, name=body.name, raw_doc=body.doc
        )
    except ProjectInvalid as exc:
        raise invalid(exc) from exc
    except ProjectLimit as exc:
        raise ApiError(409, "too_many_projects", str(exc)) from exc
    return ProjectView(**project)


@router.get("/{project_id}", response_model=ProjectView)
def get_(
    project_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    return ProjectView(**_owned(conn, user, project_id))


@router.put("/{project_id}", response_model=ProjectView)
def save(
    project_id: str,
    body: ProjectSave,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    _owned(conn, user, project_id)
    try:
        project = save_project(
            conn, request.app.state.settings, user.id, project_id,
            name=body.name, raw_doc=body.doc, version=body.version,
        )
    except ProjectInvalid as exc:
        raise invalid(exc) from exc
    except ProjectConflict as exc:
        raise conflict(exc) from exc
    return ProjectView(**project)


@router.delete("/{project_id}", status_code=204)
def delete(
    project_id: str,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    if not delete_project(conn, user.id, project_id):
        raise ApiError(404, "not_found", "Проект не найден")
    return Response(status_code=204)


@router.post("/{project_id}/finish", response_model=ProjectView)
def finish(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ProjectView:
    _owned(conn, user, project_id)
    return ProjectView(**finish_project(conn, request.app.state.settings, user.id, project_id))
```

- [ ] **Step 4: Подключить и запретить удаление занятого ассета**

В `server/app/main.py` добавить импорт `from server.app.projects.routes import router as projects_router` и `app.include_router(projects_router)` рядом с остальными.

В `server/app/assets/routes.py` в обработчике `delete` перед удалением записи добавить проверку:

```python
    used_by = projects_using_asset(conn, user.id, asset_id)
    if used_by:
        raise ApiError(
            409, "asset_in_use", "Файл стоит в проекте", {"projects": used_by}
        )
```

с импортом `from server.app.projects.store import projects_using_asset`. Проверку делать после того, как убедились, что ассет существует и принадлежит владельцу (иначе чужой ассет выдаст 409 вместо 404).

- [ ] **Step 5: Прогнать всё**

Run: `uv run python -m pytest && uv run ruff check .`
Expected: PASS. Тест `test_delete_cancels_open_jobs` из M1a должен остаться зелёным: ассет там ни в одном проекте не стоит.

- [ ] **Step 6: Commit**

```bash
git add server/app/projects/routes.py server/app/main.py server/app/assets/routes.py tests/test_projects_api.py
git commit -m "feat(projects): API with versions, finishing and asset-in-use guard"
```

---

### Task 6: Субтитры в VTT при загрузке

**Files:**
- Create: `server/media/subtitles.py`
- Modify: `server/app/uploads/store.py`, `server/app/assets/views.py`, `server/app/storage.py`
- Test: `tests/test_media_subtitles.py`, `tests/test_assets_api.py`

- [ ] **Step 1: Тесты**

Создать `tests/test_media_subtitles.py`:

```python
import pytest

from server.media.subtitles import SubtitleInvalid, to_vtt

SRT = """1
00:00:01,000 --> 00:00:03,500
Привет, мир

2
00:00:04,000 --> 00:00:06,000
Вторая реплика
и её вторая строка
"""

VTT = """WEBVTT

00:00:01.000 --> 00:00:03.500
Привет
"""


def test_srt_becomes_vtt():
    out = to_vtt(SRT, ext="srt")
    assert out.startswith("WEBVTT\n\n")
    assert "00:00:01.000 --> 00:00:03.500" in out
    assert "00:00:04.000 --> 00:00:06.000" in out
    assert "Привет, мир" in out and "и её вторая строка" in out
    assert "-->" in out and "," not in out.split("\n")[2]


def test_srt_numbering_is_dropped():
    out = to_vtt(SRT, ext="srt")
    assert not any(line.strip() == "1" for line in out.splitlines())


def test_vtt_passes_through():
    assert to_vtt(VTT, ext="vtt") == VTT


def test_vtt_without_a_header_is_rejected():
    with pytest.raises(SubtitleInvalid):
        to_vtt("00:00:01.000 --> 00:00:02.000\nтекст\n", ext="vtt")


def test_hours_and_short_forms():
    out = to_vtt("1\n01:02:03,004 --> 01:02:04,000\nтекст\n", ext="srt")
    assert "01:02:03.004 --> 01:02:04.000" in out


def test_empty_or_broken_srt_is_rejected():
    with pytest.raises(SubtitleInvalid):
        to_vtt("", ext="srt")
    with pytest.raises(SubtitleInvalid):
        to_vtt("совсем не субтитры", ext="srt")


def test_byte_order_mark_and_crlf_are_handled():
    out = to_vtt("﻿1\r\n00:00:01,000 --> 00:00:02,000\r\nтекст\r\n", ext="srt")
    assert out.startswith("WEBVTT")
    assert "\r" not in out
    assert "текст" in out


def test_unknown_extension_is_rejected():
    with pytest.raises(SubtitleInvalid):
        to_vtt(SRT, ext="txt")
```

Добавить в `tests/test_assets_api.py`:

```python
def test_subtitle_upload_produces_a_vtt_link(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    r = _upload_small(client)
    assert r.status_code == 201
    asset = r.json()
    assert asset["files"]["vtt"] == f"/files/{me['id']}/assets/{asset['id']}/subs.vtt"
    body = client.get(asset["files"]["vtt"])
    assert body.status_code == 200 and body.text.startswith("WEBVTT")


def test_broken_subtitle_file_is_rejected_on_upload(client, login_as):
    login_as()
    r = _upload_small(client, name="bad.srt", data=b"\xd0\xbd\xd0\xb5 \xd1\x81\xd1\x83\xd0\xb1\xd1\x82\xd1\x8b")
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_subtitles"
    assert client.get("/api/v1/assets").json()["assets"] == []
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `uv run python -m pytest tests/test_media_subtitles.py tests/test_assets_api.py`
Expected: FAIL.

- [ ] **Step 3: Конвертер**

Создать `server/media/subtitles.py`:

```python
"""SRT → WebVTT. Плеер в браузере понимает только VTT, а команда чаще приносит SRT.

Разбор нарочно простой: нас интересуют строки таймингов, остальное переносится как есть.
Стили и позиционирование SRT не поддерживаются и не нужны: в M3 субтитры вжигает ffmpeg из исходного файла.
"""
from __future__ import annotations

import re

TIME_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2})[,.](\d{1,3})"
)
INDEX_RE = re.compile(r"^\d+$")
VTT_HEADER = "WEBVTT"


class SubtitleInvalid(Exception):
    pass


def _normalize(text: str) -> str:
    return text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")


def _fix_time_line(line: str) -> str | None:
    match = TIME_RE.search(line)
    if match is None:
        return None
    start_h, start_ms, end_h, end_ms = match.groups()
    return f"{start_h}.{start_ms.ljust(3, '0')} --> {end_h}.{end_ms.ljust(3, '0')}"


def to_vtt(text: str, *, ext: str) -> str:
    """Готовый VTT или SubtitleInvalid. VTT пропускается как есть, но обязан иметь заголовок."""
    text = _normalize(text)
    if ext == "vtt":
        if not text.lstrip().startswith(VTT_HEADER):
            raise SubtitleInvalid("в файле VTT нет заголовка WEBVTT")
        return text
    if ext != "srt":
        raise SubtitleInvalid("субтитры принимаются в формате SRT или VTT")

    out: list[str] = []
    seen_times = False
    previous_blank = True
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        fixed = _fix_time_line(line)
        if fixed is not None:
            out.append(fixed)
            seen_times = True
            previous_blank = False
            continue
        # Номер реплики в SRT идёт отдельной строкой перед таймингом; в VTT он не нужен.
        if previous_blank and INDEX_RE.match(line.strip()):
            continue
        out.append(line)
        previous_blank = not line
    if not seen_times:
        raise SubtitleInvalid("в файле нет ни одной строки таймингов")
    body = "\n".join(out).strip("\n")
    return f"{VTT_HEADER}\n\n{body}\n"
```

- [ ] **Step 4: Конвертация при загрузке**

В `server/app/storage.py` добавить `"subs.vtt"` в кортеж `PUBLIC_FILES`.

В `server/app/uploads/store.py` в `finalize_file` после успешной вставки в базу (внутри той же транзакции нельзя: файл пишем после) добавить для субтитров конвертацию. Порядок такой: сначала перенос исходника и запись в базу, затем создание `subs.vtt`; при ошибке разбора — удалить ассет и файлы и бросить `UploadError(422, "bad_subtitles", …)`. Реализация: после `with transaction(conn):` и до `return row` добавить

```python
    if kind == "subtitle":
        try:
            _write_vtt(target_dir, dst, ext)
        except SubtitleInvalid as exc:
            with transaction(conn):
                conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
            shutil.rmtree(target_dir, ignore_errors=True)
            raise UploadError(422, "bad_subtitles", str(exc)) from exc
```

и вспомогательную функцию рядом:

```python
def _write_vtt(folder: Path, source: Path, ext: str) -> None:
    """Рядом с исходником кладём subs.vtt: браузерный плеер понимает только его."""
    text = source.read_text(encoding="utf-8-sig", errors="replace")
    (folder / "subs.vtt").write_text(to_vtt(text, ext=ext), encoding="utf-8")
```

с импортами `from server.media.subtitles import SubtitleInvalid, to_vtt`.

В `server/app/assets/views.py` добавить в `AssetFiles` поле `vtt: str | None = None` и в `asset_files` для вида `subtitle` выставлять `files.vtt = file_url(user_id, asset_id, "subs.vtt")`, а ранний `return` для субтитров убрать. Поправить тест `test_view_links_follow_status`: у субтитров теперь ожидается ссылка `vtt`, а у остальных видов `vtt` равен `None`.

- [ ] **Step 5: Прогнать всё**

Run: `uv run python -m pytest && uv run ruff check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/media/subtitles.py server/app/uploads/store.py server/app/assets/views.py server/app/storage.py tests/test_media_subtitles.py tests/test_assets_api.py
git commit -m "feat(subtitles): convert uploaded SRT to VTT for the browser player"
```

---

### Task 7: Документация и выкатка

**Files:**
- Modify: `README.md`
- Живая проверка на VM (координатор, не субагент)

- [ ] **Step 1: README**

Добавить после раздела «Обработка (M1b)»:

```markdown
### Проекты (M2a)

- `POST /api/v1/projects` `{name, doc?}` → карточка проекта; `GET /api/v1/projects` — список без документов; `GET /api/v1/projects/{id}` — проект целиком.
- `PUT /api/v1/projects/{id}` `{name, version, doc}` сохраняет документ целиком. Версия должна совпадать с текущей, иначе `409 version_conflict` и актуальный проект в `details.project`.
- `DELETE /api/v1/projects/{id}` удаляет проект. `POST /api/v1/projects/{id}/finish` завершает его: проект остаётся историей, а его ассеты удаляются, если не заняты другими незавершёнными проектами.
- Документ: `output` (`aspect` 16:9 / 9:16 / 1:1, `fit` pad / crop, `fps` 25 / 30 / 50 / 60), `clips` (до 100, каждый — ассет плюс `in` и `out`), `music`, `subtitles`. Ошибки проверки приходят списком: `422 invalid_project`, в `details.errors` пары «поле, сообщение».
- Клип с `snap_to_pauses: true` сервер подтягивает к измеренной паузе: окно ±0.35 с, отступ 0.3 с внутрь паузы, не дальше её середины. Получилось — флаг `in_verified` / `out_verified` становится `true`; не нашлось паузы — значение остаётся, флаг `false`. Рез внутри непрерывной речи молча не делается.
- Ассет, занятый в незавершённом проекте, не удаляется: `409 asset_in_use` со списком проектов.
- Загруженные субтитры конвертируются в WebVTT рядом с исходником (`subs.vtt`), ссылка приходит в карточке ассета.
```

- [ ] **Step 2: Прогон и коммит**

Run: `uv run python -m pytest && uv run ruff check . && cd web && npm test && npm run build`

```bash
git add README.md
git commit -m "docs: projects API"
```

- [ ] **Step 3: Слияние и выкатка** (координатор)

`git checkout main && git merge --ff-only m2a-projects && git push origin main m2a-projects`, затем на VM `sudo bash /opt/editing-site/deploy/deploy.sh`.

- [ ] **Step 4: Живая проверка** (координатор)

1. Миграция 5 применилась, таблица `projects` на месте.
2. Загрузить ролик, дождаться `proxy_ready`, создать проект с двумя клипами, сохранить, убедиться, что версия растёт.
3. Сохранить с устаревшей версией — получить 409 и актуальный проект.
4. Сохранить клип с `snap_to_pauses` рядом с настоящей паузой из `analysis.json` и проверить, что время сдвинулось, а флаг стал `true`.
5. Попробовать удалить занятый ассет — получить 409 со списком проектов.
6. Завершить проект и убедиться, что ассет удалён с диска, а проект остался со статусом `finished`.
7. Загрузить SRT и открыть `subs.vtt` через `/files/...`.

---

## Поправки по ходу выполнения

- **Task 2** (`b411cbc`): в образце кода плана при отрицательном `in` проверка `out` пропускалась, поэтому тест на сбор всех ошибок не прошёл бы. Проверка времени вынесена в отдельную функцию с независимыми флагами.
- **Task 3** (`efc0ccb`): числа в тесте отката подтяжки не проверяли заявленное — клип оставался валидным, и откат не срабатывал. Подобрана пауза короче удвоенного буфера, где подтяжка схлопывает клип в ноль.
- **Ревью задач 1–3** (`ddd2d02`): `NaN` и бесконечность во временах проходили все сравнения границ (любое сравнение с ними ложно) и оседали в хранимом документе токеном, который не разбирает ни один строгий разборщик JSON, включая браузерный. Теперь отвергаются. Длина идентификатора клипа ограничена 64 знаками: без предела клиент раздувал бы документ сотней клипов.
- **Ревью задач 4–5** (`6cf1661`): проверка «файл занят в проекте» переехала внутрь той же транзакции, что и удаление, иначе между проверкой и удалением кто-то успевал сослаться на файл. Пропавший между проверкой и записью проект давал 500 вместо 404. Сохранение завершённого проекта отвечало «версия устарела» вместо понятной причины.
- **Финальное ревью ветки** (`53280b7`): два блокера. Первый: `PUT` без документа молча стирал весь монтаж, потому что отсутствие поля трактовалось как «пустой проект»; поле стало обязательным. Второй: уборщик удалял файлы, стоящие в незавершённом проекте, если к ним сутки не обращались, и документ оставался со ссылками на несуществующие файлы; теперь черновик держит свои файлы независимо от срока обращений.
- **Живая проверка на VM 2026-09-05** (`53280b7`): миграция 5 применилась, все сервисы активны. Ролик 720p длиной минуту обработан за 16 с, пауза найдена как 20.016–24.009. Через домен проверено: создание проекта, подтяжка реза (`out` 19.9 стал 20.316, то есть начало паузы плюс буфер 0.3, флаг подтверждения `true`), устаревшая версия даёт 409 с актуальным проектом, сохранение без документа 422, удаление занятого файла 409 со списком проектов, завершение проекта удалило файл с диска, загруженный SRT отдался как `text/vtt` через Caddy. Тело запроса с кириллицей надо слать файлом: из консоли Git Bash оно приходит битым.

## Что остаётся на M2b

Интерфейс: шкала с блоками клипов, волна из `peaks.json`, кадры из спрайта `thumbs.jpg`, перетаскивание и подрезка ручками, разрез клипа, плеер склейки двумя элементами `video` с предзагрузкой следующего клипа, музыка отдельным аудиоэлементом, автосохранение через 500 мс тишины, обработка 409 перечитыванием, значок неподтверждённой границы.
