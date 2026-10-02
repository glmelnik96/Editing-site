# WebM из браузера и подсказка конвертера — план

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** WebM, записанный в браузере без длительности, проходит анализ; конвертер подсказывает время, предел кадра и срок хранения.

**Architecture:** Отказ probe «нет длительности» несёт контейнер; для Matroska обработчик анализа переупаковывает исходник `ffmpeg -c copy` и спрашивает probe снова. Подсказка конвертера — чистая функция от формата, длительности и срока с сервера (`/limits.render_ttl_hours`).

**Tech Stack:** FastAPI + SQLite, воркер на ffmpeg; клиент vanilla TS + vitest без DOM.

Спека: `docs/superpowers/specs/2026-10-02-browser-webm-and-convert-hint-design.md`.

Команды: `PYTHONUTF8=1 uv run python -m pytest -p no:cacheprovider` (без второго `-q`), `uv run ruff check server tests tools`, `cd web && npx vitest run && npx tsc --noEmit && npm run build`.

---

### Task 1: `NoDuration` несёт контейнер

**Files:** Modify `server/media/probe.py`; Test `tests/test_media_probe.py`

- [ ] **Step 1: Падающий тест**

```python
def test_missing_duration_names_the_container():
    """WebM из браузера приходит без длительности; по контейнеру видно, поможет ли переупаковка."""
    with pytest.raises(NoDuration) as e:
        parse_probe({"format": {"format_name": "matroska,webm"}, "streams": [
            {"codec_type": "video", "codec_name": "vp8", "width": 2, "height": 2},
        ]})
    assert e.value.reason == "no_duration" and e.value.container == "matroska,webm"
    assert e.value.message == "Не удалось определить длительность файла"
```

- [ ] **Step 2:** `uv run python -m pytest tests/test_media_probe.py` → падает (нет `NoDuration`).
- [ ] **Step 3: Реализация**

```python
class NoDuration(MediaError):
    """В файле не записана длительность. container — format_name ffprobe: по нему видно, чем
    лечить. WebM, записанный в браузере, приходит без длительности и без индекса — его
    переупаковывают без перекодирования (media/remux.py)."""

    def __init__(self, container: str) -> None:
        super().__init__("no_duration", "Не удалось определить длительность файла")
        self.container = container
```

`_duration` в конце: `raise NoDuration(str((data.get("format") or {}).get("format_name") or ""))`.

- [ ] **Step 4:** тест зелёный.

### Task 2: `remux.py`

**Files:** Create `server/media/remux.py`, `tests/test_media_remux.py`

- [ ] **Step 1: Падающие тесты**

```python
def test_only_the_matroska_family_is_repacked():
    assert can_remux("matroska,webm")
    assert not can_remux("mov,mp4,m4a,3gp,3g2,mj2")
    assert not can_remux("")


def test_remux_copies_every_track_into_matroska():
    args = remux_args(Settings(_env_file=None), "/x/source.webm", "/x/source.webm.part")
    assert args[0] == "ffmpeg" and args[-1] == "/x/source.webm.part"
    assert args[args.index("-i") + 1] == "/x/source.webm"
    assert args[args.index("-map") + 1] == "0" and args[args.index("-c") + 1] == "copy"
    assert args[args.index("-f") + 1] == "matroska"
```

- [ ] **Step 2:** падают (нет модуля).
- [ ] **Step 3: Реализация**

```python
MATROSKA = "matroska"


def can_remux(container: str) -> bool:
    return MATROSKA in {name.strip() for name in container.split(",")}


def remux_args(settings: Settings, src: str, dst: str) -> list[str]:
    return [
        settings.ffmpeg_path, "-v", "error", "-y", "-i", src,
        "-map", "0", "-c", "copy", "-f", "matroska", dst,
    ]
```

- [ ] **Step 4:** зелёные.

### Task 3: анализ переупаковывает Matroska без длительности

**Files:** Modify `server/worker/handlers.py`; Test `tests/test_worker_handlers.py`

- [ ] **Step 1: Падающие тесты** — четыре случая:
  - `test_analyze_repacks_a_browser_webm_without_duration`: probe сперва бросает `NoDuration("matroska,webm")`, потом отдаёт `VIDEO`; подменённый `run_tool` пишет 25 байт в `args[-1]` → статус `ready`, `size == 25`, исходник заменён, `.part` нет, в аргументах `-c copy`.
  - `test_analyze_does_not_repack_other_containers`: `NoDuration("mov,mp4,…")`, `run_tool` не зовётся, статус `failed`, текст «Не удалось определить длительность файла».
  - `test_analyze_refuses_to_repack_on_a_full_disk`: `disk_free_bytes` → 0, статус `failed`, текст про место.
  - `test_a_failed_repack_keeps_the_readable_reason`: `run_tool` пишет «half» в `.part` и бросает `MediaError("tool_failed", …)` → статус `failed`, текст прежний, исходник не тронут, `.part` убран.
- [ ] **Step 2:** падают.
- [ ] **Step 3: Реализация** — `_probe_or_repack(conn, settings, asset)`; `handle_analyze` зовёт её вместо `probe_file`.

```python
def _probe_or_repack(conn: sqlite3.Connection, settings: Settings, asset: sqlite3.Row) -> MediaInfo:
    src = _source(settings, asset)
    try:
        return probe_file(settings, str(src))
    except NoDuration as exc:
        if not can_remux(exc.container):
            raise
        missing = exc
    if disk_free_bytes(settings.data_dir) < src.stat().st_size * SIZE_SAFETY:
        raise MediaError("disk_low", "на диске мало места, чтобы подготовить запись, освободите его и повторите")
    tmp = src.with_name(src.name + ".part")
    try:
        run_tool(remux_args(settings, str(src), str(tmp)), timeout=settings.analyze_timeout_sec)
    except MediaError as exc:
        tmp.unlink(missing_ok=True)
        log.warning("analyze: %s не переупаковался: %s %s", asset["id"], exc.message, exc.stderr)
        raise missing from exc
    tmp.replace(src)
    conn.execute("UPDATE assets SET size = ? WHERE id = ?", (src.stat().st_size, asset["id"]))
    log.info("analyze: %s переупакован — в контейнере не было длительности", asset["id"])
    return probe_file(settings, str(src))
```

- [ ] **Step 4:** зелёные, весь `tests/test_worker_handlers.py` зелёный.

### Task 4: настоящий ffmpeg — WebM, записанный потоком

**Files:** Modify `tests/media_fixtures.py`, `tests/test_media_integration.py`

- [ ] **Step 1:** `make_browser_webm(path, seconds=4)` — VP8 + Opus, `-live 1 -f webm pipe:1` в файл; `have_webm_encoders()` — есть ли `libvpx` и `libopus`. Тест `test_browser_webm_without_duration_is_repacked_and_processed`: до обработки probe бросает `NoDuration`; после `drain` — `proxy_ready`, длительность ≈ 4 с, `size` равен весу файла на диске.
- [ ] **Step 2:** прогон без Task 3 — падает; с Task 3 — зелёный.

### Task 5: `/limits` отдаёт срок хранения

**Files:** Modify `server/app/auth/routes.py`; Test `tests/test_auth_login.py`

- [ ] **Step 1:** в `test_limits_tells_the_client_what_can_be_uploaded` — `assert body["render_ttl_hours"] == 24`.
- [ ] **Step 2:** падает.
- [ ] **Step 3:** поле `render_ttl_hours: int` в `LimitsView`, значение `settings.render_ttl_hours`.
- [ ] **Step 4:** зелёный.

### Task 6: подсказка конвертера

**Files:** Modify `web/src/assets.ts` (`Limits.render_ttl_hours`), `web/src/render.ts` (`export function plural`), `web/src/convert.ts`; Test `web/src/convert.test.ts`

- [ ] **Step 1: Падающие тесты** (вместо «поясняет звук и черновик видео»):

```ts
  it('подсказка видео: минуты по длительности, кадр до 1080p, срок с сервера', () => {
    const hint = convertHint('mp4', 600, 24)
    expect(hint).toContain('кодируется заново')
    expect(hint).toContain('около 10 мин')
    expect(hint).toContain('Кадр больше 1080p уменьшится до 1080p')
    expect(hint).toContain('хранится сутки')
  })

  it('webm кодируется вдвое дольше mp4', () => {
    expect(convertHint('webm', 600, 24)).toContain('около 20 мин')
  })

  it('подсказка звука — без кадра и минут', () => {
    const hint = convertHint('mp3', 600, 24)
    expect(hint).toContain('за секунды')
    expect(hint).not.toContain('1080')
    expect(hint).toContain('хранится сутки')
  })

  it('срок не доехал с сервера — о нём молчим', () => {
    expect(convertHint('mp4', 60, null)).not.toContain('хранится')
  })

  it('срок словами', () => {
    expect(keepText(24)).toBe('сутки')
    expect(keepText(72)).toBe('3 дня')
    expect(keepText(168)).toBe('7 дней')
    expect(keepText(12)).toBe('12 часов')
    expect(keepText(1)).toBe('1 час')
  })
```

- [ ] **Step 2:** `npx vitest run src/convert.test.ts` → падают.
- [ ] **Step 3: Реализация**

```ts
const VIDEO_FORMATS = new Set(['mp4', 'webm'])
// Предел конвертера по короткой стороне — SHORT_SIDE в server/media/convert.py.
const CONVERT_SHORT_SIDE = 1080

export function keepText(hours: number): string {
  if (hours === 24) return 'сутки'
  if (hours % 24 === 0) {
    const days = hours / 24
    return `${days} ${plural(days, 'день', 'дня', 'дней')}`
  }
  return `${hours} ${plural(hours, 'час', 'часа', 'часов')}`
}

export function convertHint(fmt: string, durationSec: number | null, ttlHours: number | null): string {
  const keep = ttlHours ? ` Готовый файл хранится ${keepText(ttlHours)}.` : ''
  if (!VIDEO_FORMATS.has(fmt)) return `Звук конвертируется за секунды.${keep}`
  const minutes = estimateRenderMinutes(durationSec ?? 0, 'medium', fmt === 'webm' ? 'webm' : 'mp4')
  return (
    `Видео кодируется заново: около ${minutes} мин, если очередь свободна. ` +
    `Кадр больше ${CONVERT_SHORT_SIDE}p уменьшится до ${CONVERT_SHORT_SIDE}p.${keep}`
  )
}
```

В `mountConvert`: `const limits = loadLimits().catch(() => null)` при монтировании; в `refresh` — `ttlHours = (await limits)?.render_ttl_hours ?? null`; в разметке — `convertHint(selectedFormat, asset.duration, ttlHours)`.

- [ ] **Step 4:** `npx vitest run && npx tsc --noEmit` → зелёное.

### Task 7: README

- [ ] «Обработка (M1b)»: WebM и MKV без длительности (так пишет запись в браузере) перед анализом переупаковываются без перекодирования; вес записи — у переупакованного файла.
- [ ] «Конвертер форматов (M7c)»: подсказка под форматами — минуты по длительности файла, кадр до 1080p, срок хранения из `/limits` (`render_ttl_hours`).

### Task 8: проверка

- [ ] pytest, ruff, vitest, tsc, build — зелёное.
- [ ] Стенд: WebM без длительности → `proxy_ready` → mp4; снимок экрана конвертера с подсказкой для mp4 и для mp3.
- [ ] Коммиты `docs:`, `feat:` (подсказка), `fix:` (WebM) и выкатка — только по отмашке владельца.
