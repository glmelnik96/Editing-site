# Путь: записи, конвертер, новый проект — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ ПОД-НАВЫК: выполнять этот план задача за задачей через
> superpowers:subagent-driven-development (рекомендуется) или executing-plans. Шаги помечены чекбоксами (`- [ ]`).

**Цель:** три работы — три адреса: записи только склад, конвертер свой экран с расширенными форматами, новый проект создаёт пустую шкалу; на двери всегда «Войти» с читаемым hover.

**Спека:** `docs/superpowers/specs/2026-09-08-path-convert-new-project-design.md`.

**Архитектура:** белый список ffmpeg расширяется в `server/media/convert.py`; общий `GET /api/v1/conversions` кормит новый экран `web/src/convert.ts`. `#/files` теряет конвертер и «В проект». `#/new` шлёт только имя. Подсказка пустой шкалы — в `timeline/view.ts`.

**Стек:** FastAPI / SQLite, vanilla TS, pytest, vitest. Команды: `uv run python -m pytest` (не `uv run pytest`), `uv run ruff check .`, `cd web && npm test`.

---

## Порядок

Сначала дверь и маршруты (не зависят от ffmpeg). Затем сервер форматов и список конверсий — без них экран конвертера нечем кормить. Потом клиент конвертера, чистка записей, новый проект, пустая шкала. README в конце.

Шапка без новых ссылок. Gif не добавляем. `GET /api/v1/assets/{id}/conversions` не удаляем.

Не коммитить `NUL` и `web/layout-preview.html`.

---

## Файлы

| Файл | Зачем |
|---|---|
| `web/src/door.ts` | подпись «Войти» |
| `web/src/style.css` | `.btn-key:hover` — `color: var(--brand-ink)` |
| `web/src/door.test.ts` | подпись кнопки |
| `web/src/style-btn-key.test.ts` | hover не красит текст в мяту |
| `web/src/router.ts`, `router.test.ts` | `#/convert` |
| `web/src/home.ts`, `home.test.ts` | шаг 2.1 → `#/convert` |
| `web/src/main.ts` | монтировать экран конвертера |
| `server/media/convert.py` | форматы, таблица ffmpeg, зонд webm |
| `server/app/storage.py` | `CONVERT_EXTS` |
| `server/app/conversions/store.py` | JOIN имени исходника, список человека |
| `server/app/conversions/routes.py` | `GET /conversions`, AUDIO/VIDEO, 503 webm |
| `server/worker/handlers.py` | передать `webm_encoder` в сборку команды |
| `tests/test_media_convert.py`, `tests/test_conversions_api.py`, `tests/test_storage.py` | форматы, 422/503, общий список |
| `web/src/convert.ts`, `convert.test.ts` | экран и чистые функции |
| `web/src/files.ts` | убрать конвертер и «В проект» |
| `web/src/files.test.ts` | удалить (тесты уезжают в `convert.test.ts`) |
| `web/src/assets.ts` | убрать `rememberPick` / `takePick` |
| `web/src/newproject.ts`, `newproject.test.ts` | имя обязательно, пустой проект |
| `web/src/timeline/view.ts` | подсказка пустой шкалы |
| `web/src/source.ts` | ссылка загрузить, если нет видео |
| `README.md` | `#/convert`, дверь «Войти», форматы |

---

### Task 1: Дверь «Войти» и читаемый hover

**Files:**
- Modify: `web/src/door.ts`
- Modify: `web/src/style.css`
- Create: `web/src/door.test.ts`
- Create: `web/src/style-btn-key.test.ts`

- [ ] **Step 1: Падающие тесты**

`web/src/door.test.ts`:

```typescript
import { expect, test } from 'vitest'
import { LOGIN_LABEL } from './door'

test('на двери всегда короткое Войти, без Яндекса', () => {
  expect(LOGIN_LABEL).toBe('Войти')
  expect(LOGIN_LABEL).not.toContain('Яндекс')
})
```

`web/src/style-btn-key.test.ts`:

```typescript
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'

test('hover ключевой кнопки оставляет тёмный текст', () => {
  const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'style.css'), 'utf8')
  const hover = css.match(/\.btn-key:hover:not\(:disabled\)\s*\{[^}]+\}/)
  expect(hover?.[0]).toContain('color: var(--brand-ink)')
})
```

- [ ] **Step 2: Прогнать — должны упасть**

Run: `cd web && npm test -- src/door.test.ts src/style-btn-key.test.ts`

Expected: FAIL (`LOGIN_LABEL` нет; в блоке hover нет `color`).

- [ ] **Step 3: Подпись и CSS**

В `web/src/door.ts` рядом с константами экрана:

```typescript
export const LOGIN_LABEL = 'Войти'
```

В разметке кнопки заменить `Войти через Яндекс` на `${LOGIN_LABEL}`.

В `web/src/style.css` блок `.btn-key:hover:not(:disabled)`:

```css
.btn-key:hover:not(:disabled) {
  background: var(--brand-hover);
  color: var(--brand-ink);
}
```

`color` обязателен: общее `a:hover` красит ссылку в `--brand-hover` — тот же зелёный, что фон.

- [ ] **Step 4: Прогнать тесты**

Run: `cd web && npm test -- src/door.test.ts src/style-btn-key.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/door.ts web/src/door.test.ts web/src/style.css web/src/style-btn-key.test.ts
git commit -m "fix: кнопка входа «Войти» читается на hover"
```

---

### Task 2: Маршрут `#/convert`

**Files:**
- Modify: `web/src/router.ts`
- Modify: `web/src/router.test.ts`

- [ ] **Step 1: Падающий тест**

В `web/src/router.test.ts` в тест «у каждого экрана свой адрес» добавить:

```typescript
  expect(parseRoute('#/convert')).toEqual({ name: 'convert' })
```

В тест мусора добавить:

```typescript
  expect(parseRoute('#/convert/')).toEqual({ name: 'home' })
```

- [ ] **Step 2: Прогнать — должен упасть**

Run: `cd web && npm test -- src/router.test.ts`

Expected: FAIL (`convert` уходит на `home`).

- [ ] **Step 3: Разбор адреса**

В `web/src/router.ts` в union `Route` добавить `{ name: 'convert' }`. В `switch`:

```typescript
    case '/convert':
      return { name: 'convert' }
```

- [ ] **Step 4: Прогнать тесты**

Run: `cd web && npm test -- src/router.test.ts`

Expected: PASS. `main.ts` пока не трогаем: TypeScript на `switch` без `convert` ругнётся на `npm run build` в Task 6, vitest `router` не импортирует `main`.

- [ ] **Step 5: Commit**

```bash
git add web/src/router.ts web/src/router.test.ts
git commit -m "feat: маршрут экрана конвертера"
```

---

### Task 3: Главная 2.1 ведёт на конвертер

**Files:**
- Modify: `web/src/home.ts`
- Modify: `web/src/home.test.ts`

- [ ] **Step 1: Падающий тест**

В `web/src/home.test.ts` в существующий тест добавить:

```typescript
    expect(html).toContain('href="#/convert"')
    expect(html).not.toMatch(/step-side[^>]*href="#\/files"/)
```

Проще и надёжнее — отдельный тест:

```typescript
  it('карточка конвертера ведёт на свой экран, не на записи', () => {
    const html = homeStepsHtml()
    const convertAt = html.indexOf('Конвертировать')
    const slice = html.slice(convertAt - 400, convertAt)
    expect(slice).toContain('href="#/convert"')
    expect(slice).not.toContain('href="#/files"')
  })
```

- [ ] **Step 2: Прогнать — должен упасть**

Run: `cd web && npm test -- src/home.test.ts`

Expected: FAIL (сейчас `CONVERT.href` = `#/files`).

- [ ] **Step 3: Ссылка и комментарий**

В `web/src/home.ts`:

```typescript
const CONVERT: Step = {
  href: '#/convert',
  step: 'Шаг 2.1',
  title: 'Конвертировать',
  lead: 'Извлечь звук или другой файл, не собирая нарезку',
  key: false,
  compact: true,
}
```

Комментарий над `UPLOAD`: конвертер — боковая ветка **своего** экрана, не записей. Между 1 и 2 его по-прежнему не вставляем.

- [ ] **Step 4: Прогнать тесты**

Run: `cd web && npm test -- src/home.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/home.ts web/src/home.test.ts
git commit -m "feat: карточка конвертера на главной ведёт на #/convert"
```

---

### Task 4: Белый список форматов на сервере

**Files:**
- Modify: `server/media/convert.py`
- Modify: `server/app/storage.py`
- Modify: `server/app/conversions/routes.py`
- Modify: `server/worker/handlers.py`
- Modify: `tests/test_media_convert.py`
- Modify: `tests/test_conversions_api.py`
- Modify: `tests/test_storage.py`

Ветка `else` в `build_convert_command` сейчас — это mp4. Новый аудиоформат, попавший в `FORMATS`, но не в свою ветку, уедет в libx264. Поэтому aac/flac/ogg/webm — отдельные `elif`, mp4 остаётся последним `else` только для `mp4`.

- [ ] **Step 1: Падающие тесты ffmpeg-таблицы**

В `tests/test_media_convert.py`:

1. `test_unknown_format_is_invalid` — вместо `webm` вызывать `fmt="gif"` (webm станет легальным).
2. Расширить `test_convert_ext_matches_whitelist`: `aac`, `flac`, `ogg`, `webm` проходят, `gif` — нет.
3. Добавить:

```python
def test_aac_is_adts_without_video():
    args = build_convert_command(s(), "/x/a.mp4", "/x/out.part", fmt="aac")
    assert "-vn" in args
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-f") + 1] == "adts"
    assert "libx264" not in args


def test_flac_is_flac_without_video():
    args = build_convert_command(s(), "/x/a.mp4", "/x/out.part", fmt="flac")
    assert "-vn" in args
    assert args[args.index("-c:a") + 1] == "flac"
    assert args[args.index("-f") + 1] == "flac"


def test_ogg_is_vorbis_without_video():
    args = build_convert_command(s(), "/x/a.mp4", "/x/out.part", fmt="ogg")
    assert "-vn" in args
    assert args[args.index("-c:a") + 1] == "libvorbis"
    assert args[args.index("-f") + 1] == "ogg"
    assert "libx264" not in args


def test_webm_is_vp9_opus_same_scale_as_mp4():
    args = build_convert_command(s(), "/x/a.mp4", "/x/out.part", fmt="webm")
    assert args[args.index("-c:v") + 1] == "libvpx-vp9"
    assert args[args.index("-c:a") + 1] == "libopus"
    assert args[args.index("-f") + 1] == "webm"
    assert "libx264" not in args
    scale = args[args.index("-vf") + 1]
    assert "1080" in scale


def test_webm_without_audio_drops_the_sound_track():
    args = build_convert_command(s(), "/x/a.mp4", "/x/out.part", fmt="webm", has_audio=False)
    assert "-an" in args
    assert "-c:a" not in args


def test_missing_webm_encoder_is_unavailable_not_raw_stderr():
    with pytest.raises(ConvertUnavailable) as exc:
        build_convert_command(s(), "/x/a.mp4", "/x/out.part", fmt="webm", webm_encoder=False)
    assert exc.value.code == "encoder_unavailable"
    assert "stderr" not in exc.value.message.lower()
    assert "mp4" in exc.value.message.lower()
```

В `tests/test_conversions_api.py`:

- `test_unknown_format_is_422` — `{"format": "gif"}` (не webm).
- Добавить постановку `aac` (202), `webm` с видео (202), `webm` с `kind="audio"` → `422 not_video`.
- Добавить 503 webm по образцу mp3:

```python
def test_missing_webm_encoder_is_503(client, login_as, settings, monkeypatch):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    from server.app.conversions import routes as conv_routes

    monkeypatch.setattr(conv_routes, "has_webm_encoder", lambda _s: False)
    r = client.post(f"/api/v1/assets/{ASSET}/convert", json={"format": "webm"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "encoder_unavailable"
```

В `tests/test_storage.py` в `test_parse_file_url_understands_conversions` добавить разбор `cnv_….webm` и `cnv_….aac` — сейчас `CONVERT_EXTS` их отрежет.

- [ ] **Step 2: Прогнать — должны упасть**

Run: `uv run python -m pytest tests/test_media_convert.py tests/test_conversions_api.py tests/test_storage.py -q`

Expected: FAIL (`webm` ещё invalid; новых веток нет).

- [ ] **Step 3: Таблица ffmpeg, storage, 503, воркер**

`server/media/convert.py`:

```python
FORMATS = ("mp3", "m4a", "aac", "wav", "flac", "ogg", "mp4", "webm")
WEBM_VIDEO = "libvpx-vp9"
WEBM_AUDIO = "libopus"
```

`CONVERT_BYTES_PER_SEC`: `aac` и `ogg` как `m4a`/`mp3`; `flac` как `wav`; `webm` как `mp4`.

```python
def has_webm_encoder(settings: Settings) -> bool:
    try:
        listing = run_tool([settings.ffmpeg_path, "-v", "error", "-encoders"], timeout=60)
        return WEBM_VIDEO in listing and WEBM_AUDIO in listing
    except MediaError:
        return False
```

`build_convert_command(..., webm_encoder: bool = True)`:

- после проверки `FORMATS`: если `fmt == "webm"` и не `webm_encoder` — `ConvertUnavailable("encoder_unavailable", "В этой сборке ffmpeg нет VP9 или Opus, выберите mp4")`;
- `aac`: `-vn -c:a aac -b:a 192k -ac 2 -f adts`;
- `flac`: `-vn -c:a flac -f flac`;
- `ogg`: `-vn -c:a libvorbis -q:a 5 -ac 2 -f ogg`;
- `webm`: тот же `-vf` что у mp4, `-c:v libvpx-vp9 -crf 32 -b:v 0 -deadline realtime -cpu-used 8`, звук `libopus` 96k или `-an`, `-f webm`;
- `mp4`: нынешний блок (больше не через «всё остальное»).

`server/app/storage.py`:

```python
CONVERT_EXTS = {"mp3", "m4a", "aac", "wav", "flac", "ogg", "mp4", "webm"}
```

`server/app/conversions/routes.py`:

```python
from server.media.convert import FORMATS, has_mp3_encoder, has_webm_encoder

AUDIO_FORMATS = {"mp3", "m4a", "aac", "wav", "flac", "ogg"}
VIDEO_FORMATS = {"mp4", "webm"}
```

После проверки mp3-кодека:

```python
    if fmt == "webm" and not has_webm_encoder(settings):
        raise ApiError(
            503, "encoder_unavailable", "В этой сборке ffmpeg нет VP9 или Opus, выберите mp4"
        )
```

Условие «не видео»: `if fmt in VIDEO_FORMATS and asset["kind"] != "video"`.

`server/worker/handlers.py` в `handle_convert` при сборке команды:

```python
        args = build_convert_command(
            settings, str(_source(settings, asset)), str(tmp),
            fmt=fmt, has_audio=bool(asset["has_audio"]),
            mp3_encoder=mp3_encoder_available(settings) if fmt == "mp3" else True,
            webm_encoder=has_webm_encoder(settings) if fmt == "webm" else True,
        )
```

Импортировать `has_webm_encoder` из `server.media.convert` (рядом с уже импортированными `build_convert_command` / `convert_ext`).

- [ ] **Step 4: Прогнать тесты**

Run: `uv run python -m pytest tests/test_media_convert.py tests/test_conversions_api.py tests/test_storage.py -q && uv run ruff check server/media/convert.py server/app/conversions/routes.py server/worker/handlers.py server/app/storage.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/media/convert.py server/app/storage.py server/app/conversions/routes.py server/worker/handlers.py tests/test_media_convert.py tests/test_conversions_api.py tests/test_storage.py
git commit -m "feat: конвертер умеет aac, flac, ogg и webm"
```

---

### Task 5: `GET /api/v1/conversions` со именем исходника

**Files:**
- Modify: `server/app/conversions/store.py`
- Modify: `server/app/conversions/routes.py`
- Modify: `tests/test_conversions_api.py`

- [ ] **Step 1: Падающие тесты API**

В `tests/test_conversions_api.py`:

```python
def test_list_all_conversions_is_mine_newest_first(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    a1 = seed_asset(settings, me["id"], asset_id="ast_000000000001", name="встреча.mp4")
    a2 = seed_asset(settings, me["id"], asset_id="ast_000000000002", name="нарезка.mp4")
    seed_conversion(settings, me["id"], "cnv_00000000000a", created_at="2026-01-01T00:00:00.000Z", asset_id=a1)
    seed_conversion(settings, me["id"], "cnv_00000000000b", created_at="2026-02-01T00:00:00.000Z", asset_id=a2)
    listing = client.get("/api/v1/conversions").json()["conversions"]
    assert [c["id"] for c in listing] == ["cnv_00000000000b", "cnv_00000000000a"]
    assert listing[0]["original_name"] == "нарезка.mp4"
    assert listing[1]["original_name"] == "встреча.mp4"


def test_list_all_conversions_hides_foreign(client, login_as, settings):
    login_as()
    me = client.get("/api/v1/me").json()
    seed_asset(settings, me["id"])
    seed_conversion(settings, me["id"], "cnv_000000000001")
    assert client.post("/api/v1/admin/whitelist", json={"email": "other@ya.ru"}).status_code == 201
    login_as("other@ya.ru", "Other")
    assert client.get("/api/v1/conversions").json()["conversions"] == []
```

В `test_list_and_card_and_delete` после листинга по ассету: `assert listing["conversions"][0]["original_name"] == "встреча.mp4"`.

В `test_conversions_require_auth`: `assert client.get("/api/v1/conversions").status_code == 401`.

`seed_asset` уже принимает `asset_id` и `name`. Второй ассет — другой id, иначе UNIQUE.

- [ ] **Step 2: Прогнать — должны упасть**

Run: `uv run python -m pytest tests/test_conversions_api.py -q`

Expected: FAIL (нет `GET /conversions` или нет `original_name`).

- [ ] **Step 3: JOIN и ручка**

В `server/app/conversions/store.py` `_row` добавить `"original_name": row["original_name"]`.

Выборка с именем:

```python
_SELECT = (
    "SELECT conversions.*, assets.original_name FROM conversions "
    "JOIN assets ON assets.id = conversions.asset_id "
)
```

`list_conversions`: `_SELECT + "WHERE conversions.asset_id = ? AND conversions.user_id = ? ORDER BY conversions.created_at DESC, conversions.id"`.

`get_conversion`: `_SELECT + "WHERE conversions.id = ? AND conversions.user_id = ?"`.

```python
def list_conversions_for_user(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = conn.execute(
        _SELECT + "WHERE conversions.user_id = ? ORDER BY conversions.created_at DESC, conversions.id",
        (user_id,),
    )
    return [_row(r) for r in rows]
```

`ConversionView` в `routes.py`: поле `original_name: str`.

Новая ручка **перед** `GET /conversions/{conversion_id}`:

```python
@router.get("/conversions", response_model=ConversionList)
def list_mine(
    user: CurrentUser = Depends(current_user),  # noqa: B008
    conn: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ConversionList:
    return ConversionList(
        conversions=[ConversionView(**c) for c in list_conversions_for_user(conn, user.id)]
    )
```

- [ ] **Step 4: Прогнать тесты**

Run: `uv run python -m pytest tests/test_conversions_api.py -q && uv run ruff check server/app/conversions`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/app/conversions/store.py server/app/conversions/routes.py tests/test_conversions_api.py
git commit -m "feat: список всех конверсий человека с именем исходника"
```

---

### Task 6: Экран `#/convert`

**Files:**
- Create: `web/src/convert.ts`
- Create: `web/src/convert.test.ts`
- Modify: `web/src/main.ts`
- Delete after move: логика конвертера из `web/src/files.ts` остаётся до Task 7; тесты панели переезжают сюда сразу.

Чистые функции покрывают меню форматов и разметку без jsdom. `mountConvert` — по образцу `mountFiles`: опрос `POLL_MS`, `stop()` гасит таймер.

- [ ] **Step 1: Падающие vitest**

`web/src/convert.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import {
  convertFormatsFor,
  convertHint,
  convertJobText,
  conversionsListHtml,
  emptyConvertHtml,
  formatChipsHtml,
} from './convert'

describe('convert screen helpers', () => {
  it('даёт mp4 и webm только видео', () => {
    expect(convertFormatsFor('video')).toEqual([
      'mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg', 'mp4', 'webm',
    ])
    expect(convertFormatsFor('audio')).toEqual(['mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg'])
  })

  it('пустой склад — ссылка в записи, без чипов', () => {
    const html = emptyConvertHtml()
    expect(html).toContain('#/files')
    expect(html).toContain('Сначала загрузите запись')
    expect(html).not.toContain('data-format')
  })

  it('чипы видео содержат webm, у звука — нет', () => {
    expect(formatChipsHtml('video', 'mp3', false)).toContain('data-format="webm"')
    expect(formatChipsHtml('audio', 'mp3', false)).not.toContain('data-format="webm"')
    expect(formatChipsHtml('audio', 'mp3', false)).not.toContain('data-format="mp4"')
  })

  it('во время задания чипы неактивны', () => {
    expect(formatChipsHtml('video', 'mp3', true)).toContain('disabled')
  })

  it('список готовых показывает имя исходника и срок', () => {
    const html = conversionsListHtml([
      {
        id: 'cnv_1',
        original_name: 'Нарезка.mp4',
        format: 'mp3',
        size: 1024,
        duration: 60,
        expires_at: '2099-01-02T03:04:00.000Z',
        download: '/files/u/assets/ast_1/conversions/cnv_1.mp3',
      },
    ])
    expect(html).toContain('Нарезка.mp4')
    expect(html).toContain('Скачать')
    expect(html).toContain('до ')
    expect(html).toContain('data-drop-conversion="cnv_1"')
  })

  it('поясняет звук и черновик видео', () => {
    expect(convertHint()).toMatch(/звук/)
    expect(convertHint()).toMatch(/mp4/)
    expect(convertJobText('running', 0.4)).toContain('%')
  })
})
```

- [ ] **Step 2: Прогнать — должен упасть**

Run: `cd web && npm test -- src/convert.test.ts`

Expected: FAIL (модуля нет).

- [ ] **Step 3: Модуль и монтаж**

`web/src/convert.ts` — вынести из нынешнего `files.ts` типы `ConversionCard` (добавить `original_name: string`), `CONVERT_RUNNING`, `convertJobText`, `until` (переименовать в локальную функцию), `startConvert`, `deleteConversion`.

Новое:

```typescript
export function convertFormatsFor(kind: string): string[] {
  const audio = ['mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg']
  return kind === 'audio' ? audio : [...audio, 'mp4', 'webm']
}

export function convertHint(): string {
  return 'извлечь звук займёт секунды; mp4 и webm — примерно как черновик сборки этой длительности'
}

export function emptyConvertHtml(): string {
  return `<p class="lead" style="margin:0">Сначала загрузите запись</p>
    <a class="btn btn-key" href="#/files">К записям</a>`
}

export function formatChipsHtml(kind: string, selected: string, locked: boolean): string {
  return convertFormatsFor(kind)
    .map(
      fmt =>
        `<button type="button" class="btn ${fmt === selected ? 'btn-key' : 'btn-ghost'}" data-format="${fmt}"${
          locked ? ' disabled' : ''
        }>${fmt}</button>`,
    )
    .join('')
}

export function conversionsListHtml(items: ConversionCard[]): string {
  if (!items.length) return ''
  return `<h2 class="display-m" style="margin:0">Готовые файлы</h2>
    <ul class="versions">${items
      .map(
        c => `<li>
      <span>${escapeHtml(c.original_name)} · ${escapeHtml(c.format)} · ${fmtDuration(c.duration)} · ${fmtSize(c.size)} · до ${until(c.expires_at)}</span>
      <span class="render-actions">
        <a href="${escapeHtml(c.download)}" download>Скачать</a>
        <button type="button" data-drop-conversion="${escapeHtml(c.id)}">Удалить</button>
      </span></li>`,
      )
      .join('')}</ul>`
}

export function listMyConversions(): Promise<{ conversions: ConversionCard[] }> {
  return api<{ conversions: ConversionCard[] }>('/api/v1/conversions')
}
```

`mountConvert(el, work?)`:

Разметка: заголовок «Конвертер», `#cv-body`, `#cv-error`.

`refresh()`:

1. `listAssets()`, готовые = `ready` / `proxy_ready`.
2. Нет готовых → `emptyConvertHtml()`, таймер только если `needsPolling` (чтобы появился файл после анализа). Чипов нет.
3. Есть готовые: `<select id="cv-file">` опции `имя · длительность`. Под селектом всегда `<p class="meta"><a href="#/files">Нет файла? Загрузить в записях</a></p>`.
4. Выбранный ассет: чипы `formatChipsHtml`, выбранный формат по умолчанию первый из списка (если прежний формат есть у нового kind — сохранить). Кнопка «Конвертировать» (`id="cv-go"`), `disabled` если задание этого файла `queued`/`running`.
5. Если job не `done` — фраза `convertJobText`, полоса, «Отменить» (`data-cancel-convert`).
6. `listMyConversions()` → `conversionsListHtml`.
7. `convertHint()` серым под чипами.
8. Опрос: `needsPolling(assets)` или живое convert-задание выбранного файла.

Старт: `startConvert(assetId, selectedFormat)` как в `files.ts`. Отмена — `cancelJob`. Удаление строки — `deleteConversion` с confirm «Удалить готовый файл?…».

`work` в `mountConvert` не обязателен: загрузки на этом экране нет.

В `web/src/main.ts`:

```typescript
import { mountConvert } from './convert'
```

В `show`:

```typescript
    case 'convert':
      current = mountConvert(shell.screen)
      return
```

- [ ] **Step 4: Прогнать тесты и tsc**

Run: `cd web && npm test -- src/convert.test.ts src/router.test.ts && npx tsc --noEmit`

Expected: PASS, `tsc` зелёный (`Route` исчерпан).

- [ ] **Step 5: Commit**

```bash
git add web/src/convert.ts web/src/convert.test.ts web/src/main.ts
git commit -m "feat: отдельный экран конвертера"
```

---

### Task 7: Записи — только склад

**Files:**
- Modify: `web/src/files.ts`
- Delete: `web/src/files.test.ts` (весь файл — тесты панели конвертера; они уже в `convert.test.ts`)
- Modify: `web/src/assets.ts`

- [ ] **Step 1: Убедиться, что старые тесты панели больше не нужны в files**

`web/src/files.test.ts` импортирует `convertFormatsFor` из `./files`. После выноса это сломает `npm test`. Сначала перенести нечего — удалить файл после чистки `files.ts`. Падающий прогон сейчас: `cd web && npm test -- src/files.test.ts` ещё зелёный. Это ожидаемо. Шаг реализации — удалить панель, затем удалить `files.test.ts`, прогнать весь `npm test`.

- [ ] **Step 2: Снять конвертер и «В проект»**

В `web/src/files.ts`:

- импорты: убрать `rememberPick`, `cancelJob`, `loadJob`, типы/функции конвертера (`ConversionCard`, `startConvert`, `listConversions`, `deleteConversion`, `convertPanelHtml`, …);
- в `card`: убрать кнопку «В проект» и вызов `convertPanelHtml`; оставить кадр, имя, пилюлю, «Удалить»;
- убрать `pending` convert-заданий, `jobs`, `conversions`, `loadReadyConversions`, `pollJobs`;
- `refresh` больше не грузит конверсии; таймер только `needsPolling(assets)`;
- в `wire` убрать `data-pick`, `data-convert`, `data-cancel-convert`, `data-drop-conversion`.

В `web/src/assets.ts` удалить блок «Память о выбранной записи»: `PICK_KEY`, `rememberPick`, `takePick` и комментарий про `#/new`.

Удалить `web/src/files.test.ts`.

- [ ] **Step 3: Прогнать фронт**

Run: `cd web && npm test && npx tsc --noEmit`

Expected: PASS. Нет импортов `rememberPick` / `convertFormatsFor` из `files`.

- [ ] **Step 4: Commit**

```bash
git add web/src/files.ts web/src/assets.ts
git rm web/src/files.test.ts
git commit -m "refactor: записи без конвертера и без входа в проект"
```

---

### Task 8: Новый проект — имя и пустая шкала

**Files:**
- Modify: `web/src/newproject.ts`
- Create: `web/src/newproject.test.ts`

- [ ] **Step 1: Падающий тест имени**

`web/src/newproject.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { nameReady } from './newproject'

describe('имя нового проекта', () => {
  it('пустое и одни пробелы — нельзя создать', () => {
    expect(nameReady('')).toBe(false)
    expect(nameReady('   ')).toBe(false)
  })
  it('после обрезки хотя бы один знак — можно', () => {
    expect(nameReady('Ролик')).toBe(true)
    expect(nameReady('  а  ')).toBe(true)
  })
})
```

- [ ] **Step 2: Прогнать — должен упасть**

Run: `cd web && npm test -- src/newproject.test.ts`

Expected: FAIL.

- [ ] **Step 3: Экран без плиток**

```typescript
/**
 * Экран нового проекта: имя и вход в монтаж с пустой шкалой.
 *
 * Файл на шкалу кладут в исходниках редактора, здесь его не выбираем.
 */
import { ApiError } from './api'
import { createProject } from './project'

export function nameReady(value: string): boolean {
  return value.trim().length > 0
}

export function mountNewProject(el: HTMLElement) {
  el.innerHTML = `
    <div class="screen stack">
      <h1 class="display-l" style="margin:0">Новый проект</h1>
      <input id="np-name" class="field" maxlength="200" placeholder="Как назовём ролик" />
      <div class="row">
        <button id="np-go" class="btn btn-key" disabled>Создать</button>
      </div>
      <pre id="np-error" hidden></pre>
    </div>`

  const nameField = el.querySelector('#np-name') as HTMLInputElement
  const go = el.querySelector('#np-go') as HTMLButtonElement
  const errorBox = el.querySelector('#np-error') as HTMLPreElement

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  const sync = () => {
    go.disabled = !nameReady(nameField.value)
  }
  nameField.addEventListener('input', sync)

  go.addEventListener('click', async () => {
    const name = nameField.value.trim()
    if (!nameReady(name)) return
    go.disabled = true
    try {
      const project = await createProject(name)
      location.hash = `#/p/${project.id}`
    } catch (e) {
      go.disabled = false
      showError(e)
    }
  })

  return { stop(): void {} }
}
```

Плиток, `listAssets`, `saveRequest`, `takePick` нет. Пустой склад не блокирует экран. Кнопка не шлёт пустое имя.

- [ ] **Step 4: Прогнать тесты**

Run: `cd web && npm test -- src/newproject.test.ts && npx tsc --noEmit`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/newproject.ts web/src/newproject.test.ts
git commit -m "feat: новый проект создаётся с именем и пустой шкалой"
```

---

### Task 9: Подсказка пустой шкалы и ссылка в исходниках

**Files:**
- Modify: `web/src/timeline/view.ts`
- Modify: `web/src/source.ts`
- Create: `web/src/timeline/hint.test.ts`
- Create: `web/src/source.test.ts`

Чтобы не тащить DOM шкалы в vitest, экспортнуть чистые строки.

- [ ] **Step 1: Падающие тесты**

`web/src/timeline/hint.test.ts`:

```typescript
import { expect, test } from 'vitest'
import { emptyTrackHint } from './view'

test('пустая шкала просит кусок из исходников', () => {
  expect(emptyTrackHint(0)).toBe('Добавьте кусок из исходников')
  expect(emptyTrackHint(1)).toBe('')
})
```

`web/src/source.test.ts`:

```typescript
import { expect, test } from 'vitest'
import { sourcePoolNote } from './source'

test('без готового видео — ссылка загрузить', () => {
  expect(sourcePoolNote(0)).toContain('#/files')
  expect(sourcePoolNote(0)).toContain('Загрузите запись')
  expect(sourcePoolNote(1)).toBe('')
})
```

- [ ] **Step 2: Прогнать — должны упасть**

Run: `cd web && npm test -- src/timeline/hint.test.ts src/source.test.ts`

Expected: FAIL.

- [ ] **Step 3: Вставить подсказки**

В `web/src/timeline/view.ts`:

```typescript
export function emptyTrackHint(clipCount: number): string {
  return clipCount === 0 ? 'Добавьте кусок из исходников' : ''
}
```

В конце `render()`, когда `!drag`:

```typescript
    if (!drag) hint.textContent = emptyTrackHint(current.clips.length)
```

Не ставить это до раннего `return` при `drag`: иначе сотрётся подсказка переноса. После `finishDrag` / `abortDrag` вызывается `flushPending` → `render` → пустая фраза вернётся, если клипов нет.

`finishDrag` сейчас делает `hint.textContent = ''` до `flushPending` — это нормально, render сразу восстановит текст.

В `web/src/source.ts`:

```typescript
export function sourcePoolNote(readyCount: number): string {
  return readyCount === 0
    ? 'Нет готового видео. <a href="#/files">Загрузите запись</a>'
    : ''
}
```

В `setAssets` после заполнения `<select>`:

```typescript
      if (!assets.length) note.innerHTML = sourcePoolNote(0)
      else if (!current || current.files.proxy) note.textContent = ''
```

`choose()` сейчас затирает `note` текстом про прокси. Если пул пуст, `choose(null)` не должен стереть ссылку:

в `choose` в конце, где задаётся `note.textContent` про прокси:

```typescript
    if (!assets.length) {
      note.innerHTML = sourcePoolNote(0)
      return
    }
    note.textContent = asset && !asset.files.proxy ? 'Прокси ещё готовится: выделять можно будет после обработки.' : ''
```

- [ ] **Step 4: Прогнать тесты**

Run: `cd web && npm test -- src/timeline/hint.test.ts src/source.test.ts && npx tsc --noEmit`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/timeline/view.ts web/src/timeline/hint.test.ts web/src/source.ts web/src/source.test.ts
git commit -m "feat: пустая шкала и исходники без файла не тупик"
```

---

### Task 10: README и полный прогон

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Текст**

Раздел «Экраны и разбор субтитров (M5)»:

- дверь: кнопка **«Войти»** (не «Войти через Яндекс»);
- вошедший: три работы с главной — исходники `#/files`, редактор `#/new`, конвертер `#/convert`;
- строка адресов: добавить `#/convert` конвертер;
- `#/files` — склад (загрузка, статус, удалить), без конвертера;
- `#/new` — название и создание, шкала пустая, куски из исходников;
- конвертер: выбор готового файла, форматы `mp3 m4a aac wav flac ogg` и для видео `mp4 webm`, готовые файлы скачиваются здесь. Gif нет. Нет кодека mp3/webm — 503.

- [ ] **Step 2: Полный прогон**

Run: `uv run python -m pytest && uv run ruff check . && cd web && npm test && npm run build`

Expected: всё зелёное.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: три адреса работ и форматы конвертера"
```

- [ ] **Step 4: Живая проверка после выкладки (не в этом коммите)**

На проде: hover «Войти»; с главной три адреса; создать проект без файла — подсказка на шкале; конвертировать в новый формат и скачать со экрана конвертера, не с карточки записи.

---

## Покрытие спеки

| Спека | Задача |
|---|---|
| Дверь «Войти», ink на hover | Task 1 |
| `#/convert` в роутере | Task 2 |
| Главная 2.1 | Task 3 |
| Форматы, 422 gif, 503 webm, CONVERT_EXTS | Task 4 |
| GET всех конверсий + original_name | Task 5 |
| Экран-инструмент, чипы, история | Task 6 |
| Записи без конвертера и «В проект», без rememberPick | Task 7 |
| Новый проект, обязательное имя, пустой POST | Task 8 |
| Подсказка шкалы, ссылка в исходниках | Task 9 |
| Шапка без новых ссылок | ничего не делаем |
| README | Task 10 |
