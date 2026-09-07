# Одна вкладка субтитров и сохранения проекта — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНЫЙ ПОД-НАВЫК: выполнять этот план задача за задачей через
> superpowers:subagent-driven-development (или executing-plans). Шаги помечены чекбоксами (`- [ ]`).

**Цель:** человек собирает путь «расшифровать → реплики → в ролик / без них» в одной вкладке, а снимки проекта открывает из шапки, не из четвёртой вкладки.

**Спека:** `docs/superpowers/specs/2026-09-07-subtitles-tab-and-project-saves-design.md`.

**Архитектура:** признак `enabled` живёт в блоке `doc.subtitles`. Проверка документа его нормализует и сохраняет. Сборщик ffmpeg считает выключенный блок отсутствующим; генератор SRT и `GET /subtitles` на признак не смотрят. Интерфейс: три вкладки, галочка над шкалой, выпадающий список в шапке.

**Стек:** FastAPI / SQLite, `validate_doc` + `build_render_command`, vanilla TS в `web/src/`, pytest и vitest.

---

## Порядок

Сервер первым: без `enabled` в документе и без пропуска фильтра галочка бесполезна. Интерфейс вторым. Документы и живая проверка последними.

`transcript.ts` с экрана уходит, файл и тесты чистых функций (`flattenWords` и соседи) остаются.

---

## Файлы

| Файл | Зачем |
|---|---|
| `server/app/projects/doc.py` | нормализация `enabled` |
| `server/app/projects/store.py` | generate ставит `enabled: true` |
| `server/media/render.py` | выключенный блок не кладёт фильтр |
| `server/app/projects/routes.py` | docstring GET: файл не обязан совпадать со сборкой ролика |
| `tests/test_project_doc.py` | нормализация и отказ |
| `tests/test_project_subtitles_api.py` | generate, PUT, GET при `false` |
| `tests/test_media_render.py` | команда ffmpeg без фильтра |
| `web/src/project.ts` | поле в типе |
| `web/src/subtitles.ts` | helpers, панель без burn/soft |
| `web/src/subtitles.test.ts` | helpers и `sameSubtitleView` |
| `web/src/editor.ts` | вкладки, галочка, шапка |
| `web/src/versions.ts` | разметка выпадающего списка |
| `web/src/style.css` | шапка справа, список, галочка |
| `README.md`, `docs/superpowers/specs/2026-09-06-ux-redesign-and-subtitle-review-design.md` | догнать код |

Воркер не трогаем: он уже отдаёт документ в `build_render_command`.

---

### Task 1: `enabled` в документе

**Files:** `server/app/projects/doc.py`, `tests/test_project_doc.py`

- [ ] **Step 1: Тесты**

В `tests/test_project_doc.py` поправить существующее сравнение: после проверки ключ всегда есть.

```python
assert out["subtitles"] == {
    "source": "file", "asset_id": "ast_000000000004", "mode": "soft",
    "style": "default", "enabled": True,
}
```

В `test_cues_source_needs_no_asset` добавить `assert out["subtitles"]["enabled"] is True`.

Новые тесты рядом с `test_subtitles_rules`:

```python
def test_missing_enabled_means_on():
    """Старый проект без ключа не должен молча потерять субтитры в ролике."""
    out = validate_doc(
        doc(subtitles={"source": "file", "asset_id": "ast_000000000004", "mode": "soft"}),
        assets=ASSETS, settings=S,
    )
    assert out["subtitles"]["enabled"] is True


def test_enabled_false_is_kept():
    out = validate_doc(subs_doc(enabled=False), assets=ASSETS, settings=S)
    assert out["subtitles"]["enabled"] is False
    assert out["subtitles"]["cues"][0]["text"] == "Привет"


def test_enabled_must_be_bool():
    assert errors_of(subs_doc(enabled="false")) == ["subtitles.enabled"]
    assert errors_of(subs_doc(enabled=1)) == ["subtitles.enabled"]
```

- [ ] **Step 2: Прогнать — должны упасть**

```bash
uv run python -m pytest tests/test_project_doc.py::test_missing_enabled_means_on tests/test_project_doc.py::test_enabled_false_is_kept tests/test_project_doc.py::test_enabled_must_be_bool tests/test_project_doc.py::test_subtitles_rules -q
```

Ожидание: FAIL — ключа нет в объекте / тестов нет.

- [ ] **Step 3: Реализация**

В `_validate_subtitles` после проверки `style`, до сборки `out`:

```python
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.add("subtitles.enabled", "enabled должен быть true или false")
        return None
    out = {"source": source, "asset_id": asset_id, "mode": mode, "style": style, "enabled": enabled}
    if cues is not None:
        out["cues"] = cues
    return out
```

`isinstance(True, bool)` — да, `isinstance(1, bool)` — нет: единица не пролезает.

- [ ] **Step 4: Прогнать**

```bash
uv run python -m pytest tests/test_project_doc.py -q
```

Ожидание: PASS. Старые сравнения полного объекта без `enabled` больше не проходят — их уже поправили в шаге 1.

- [ ] **Step 5: Commit**

```bash
git add server/app/projects/doc.py tests/test_project_doc.py
git commit -m "feat(doc): признак enabled у блока субтитров"
```

---

### Task 2: Сборка реплик включает субтитры, GET отдаёт файл и при выключении

**Files:** `server/app/projects/store.py`, `server/app/projects/routes.py`, `tests/test_project_subtitles_api.py`

- [ ] **Step 1: Тесты**

В `test_cues_land_in_the_document` после чтения `subs`:

```python
assert subs["enabled"] is True
```

Новый тест в том же файле, рядом со сборкой реплик:

```python
def test_edited_cues_keep_enabled_false(client, login_as, settings):
    """Снятая галочка — правка документа: текст карточки её не включает обратно."""
    login_as()
    project = with_transcript(client, settings)
    saved = generate(client, project).json()
    doc = saved["doc"]
    doc["subtitles"]["enabled"] = False
    doc["subtitles"]["cues"][0]["text"] = "Я поправил"
    put = client.put(
        f"/api/v1/projects/{project['id']}",
        json={"name": saved["name"], "version": saved["version"], "doc": doc},
    )
    assert put.status_code == 200, put.text
    subs = put.json()["doc"]["subtitles"]
    assert subs["enabled"] is False
    assert subs["cues"][0]["text"] == "Я поправил"
    got = subtitles(client, put.json())
    assert got.status_code == 200
    assert "Я поправил" in got.text
```

- [ ] **Step 2: Прогнать — generate без ключа упадёт**

```bash
uv run python -m pytest tests/test_project_subtitles_api.py::test_cues_land_in_the_document tests/test_project_subtitles_api.py::test_edited_cues_keep_enabled_false -q
```

Ожидание: `test_cues_land_in_the_document` FAIL (`enabled` нет или False). PUT-тест может уже пройти: проверка документа из Task 1 сохранит `false`, а `build_project_subtitles` признак не читает.

- [ ] **Step 3: Реализация**

В `generate_project_cues` блок заменяется целиком и всегда включает субтитры:

```python
            "subtitles": {
                "source": "cues", "mode": mode, "style": "default",
                "enabled": True, "cues": cues,
            },
```

В `routes.py` у GET `/subtitles` поправить docstring: это файл из реплик (или расшифровки), а не «ровно то, что уйдёт в сборку». Сборка ролика смотрит на `enabled`, эта ручка — нет.

- [ ] **Step 4: Прогнать**

```bash
uv run python -m pytest tests/test_project_subtitles_api.py tests/test_project_subtitles.py -q
```

Ожидание: PASS. Генератор SRT по-прежнему пишет кэш при `enabled: false`.

- [ ] **Step 5: Commit**

```bash
git add server/app/projects/store.py server/app/projects/routes.py tests/test_project_subtitles_api.py
git commit -m "feat(projects): сборка реплик включает субтитры в ролик"
```

---

### Task 3: Выключенный блок не едет в ffmpeg

**Files:** `server/media/render.py`, `tests/test_media_render.py`

Сейчас `if subtitles:` кладёт фильтр при любом блоке. Пустой `subtitles_path` при `source: cues` — `RenderInvalid`, а не ролик без субтитров. Поэтому признак смотрит сборщик команды, не воркер.

- [ ] **Step 1: Тесты**

В `TestСубтитрыИзТранскрипта` (`tests/test_media_render.py`):

```python
    def test_выключенные_реплики_не_едут_в_команду(self):
        """Галочка снята: реплики в документе, в кадре их быть не должно."""
        args = self.build(doc(subtitles={
            "source": "cues", "asset_id": None, "mode": "burn", "style": "default",
            "enabled": False,
            "cues": [{"start": 0, "end": 1, "text": "х"}],
        }), path=None)
        assert "subtitles=" not in filter_of(args)
        assert "mov_text" not in args

    def test_нет_ключа_enabled_как_включено(self):
        chain = filter_of(self.build(doc(subtitles={
            "source": "cues", "asset_id": None, "mode": "burn", "style": "default",
            "cues": [{"start": 0, "end": 1, "text": "х"}],
        })))
        assert "subs/3.srt" in chain
```

В `TestСубтитры`:

```python
    def test_выключенный_файл_не_вжигается(self):
        args = build(self.subs_doc(enabled=False))
        assert "subtitles=" not in filter_of(args)
        assert SOURCES["ast_s"].path not in args
```

`self.build(..., path=None)` при выключенных репликах **не** должен бросать `RenderInvalid`.

- [ ] **Step 2: Прогнать — должны упасть**

```bash
uv run python -m pytest tests/test_media_render.py::TestСубтитрыИзТранскрипта::test_выключенные_реплики_не_едут_в_команду tests/test_media_render.py::TestСубтитры::test_выключенный_файл_не_вжигается -q
```

Ожидание: FAIL — либо `RenderInvalid` про `subtitles_path`, либо фильтр на месте.

- [ ] **Step 3: Реализация**

В `build_render_command` перед работой с субтитрами:

```python
    subtitles = doc.get("subtitles")
    if not isinstance(subtitles, dict) or subtitles.get("enabled") is False:
        subtitles = None
```

Только явный `False` выключает. Нет ключа — как включено: сырой JSON из базы ещё мог не пройти новый `validate_doc`.

Старые тесты без ключа не трогать: они остаются зелёными.

- [ ] **Step 4: Прогнать**

```bash
uv run python -m pytest tests/test_media_render.py tests/test_worker_render.py tests/test_render_integration.py -q
```

Ожидание: PASS. Интеграционный прогон без `enabled` по-прежнему вжигает.

- [ ] **Step 5: Commit**

```bash
git add server/media/render.py tests/test_media_render.py
git commit -m "feat(render): выключенные субтитры не попадают в ffmpeg"
```

---

### Task 4: Хелперы экрана и тип документа

**Files:** `web/src/project.ts`, `web/src/subtitles.ts`, `web/src/subtitles.test.ts`

Редактор не покрыт vitest. Логику галочки и сохранения `enabled` выносим в чистые функции — их и проверяем.

- [ ] **Step 1: Тесты**

Тип:

```ts
export type Subtitles = {
  source: 'file' | 'transcript' | 'cues'
  asset_id: string | null
  mode: 'burn' | 'soft'
  style: string
  enabled?: boolean
  cues?: Cue[]
}
```

В `subtitles.test.ts` расширить фабрику `view` необязательным `enabled` и добавить:

```ts
import { burnEnabled, cuesReady, patchCues, sameSubtitleView } from './subtitles'

it('не пересобирает карточки из-за галочки', () => {
  const on = view([cue(0, 2)])
  const off = {
    ...on,
    doc: { ...on.doc, subtitles: { ...on.doc.subtitles!, enabled: false } },
  } as Project
  expect(sameSubtitleView(on, off)).toBe(true)
})

describe('галочка над шкалой', () => {
  it('серая, пока реплик нет', () => {
    expect(cuesReady(null)).toBe(false)
    expect(burnEnabled(null)).toBe(false)
  })

  it('после сборки включена, даже если ключа ещё нет', () => {
    const subs = { source: 'cues' as const, asset_id: null, mode: 'burn' as const,
      style: 'default', cues: [cue(0, 2)] }
    expect(cuesReady(subs)).toBe(true)
    expect(burnEnabled(subs)).toBe(true)
  })

  it('снятая галочка не включает текст карточки обратно', () => {
    const previous = { source: 'cues' as const, asset_id: null, mode: 'burn' as const,
      style: 'default', enabled: false, cues: [cue(0, 2, 'старое')] }
    const next = patchCues(previous, [cue(0, 2, 'новое')])
    expect(next.enabled).toBe(false)
    expect(next.cues[0].text).toBe('новое')
  })
})
```

- [ ] **Step 2: Прогнать — должны упасть**

```bash
cd web && npm test -- src/subtitles.test.ts
```

Ожидание: FAIL — `cuesReady` / `patchCues` нет; `sameSubtitleView` ещё не обязан игнорировать `enabled`, но сейчас он его не сравнивает — этот кейс уже зелёный. Красные — новые имена.

- [ ] **Step 3: Реализация**

В `subtitles.ts`:

```ts
import type { Cue, Project, Subtitles } from './project'

export function cuesReady(subs: Subtitles | null | undefined): boolean {
  return Boolean(subs && subs.source === 'cues' && (subs.cues?.length ?? 0) > 0)
}

export function burnEnabled(subs: Subtitles | null | undefined): boolean {
  return cuesReady(subs) && subs.enabled !== false
}

export function patchCues(
  previous: Subtitles | null | undefined,
  cues: Cue[],
  mode?: 'burn' | 'soft',
): Subtitles {
  return {
    source: 'cues',
    asset_id: null,
    mode: mode ?? previous?.mode ?? 'burn',
    style: previous?.style ?? 'default',
    enabled: previous?.enabled !== false,
    cues,
  }
}
```

`sameSubtitleView` по-прежнему сравнивает `source`, `mode` и `cues`, не `enabled`.

- [ ] **Step 4: Прогнать**

```bash
cd web && npm test -- src/subtitles.test.ts
```

Ожидание: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/project.ts web/src/subtitles.ts web/src/subtitles.test.ts
git commit -m "feat(web): галочка субтитров не затирает правки карточек"
```

---

### Task 5: Панель «Субтитры» без режима и без «наложить»

**Files:** `web/src/subtitles.ts`, `web/src/editor.ts`

- [ ] **Step 1: Убрать с панели то, что врёт**

В `draw()` после появления карточек: нет `<select id="sub-mode">`, нет абзаца «Субтитры войдут в ролик» / «Пока не наложены». Остаются счётчик, «Собрать заново», карточки.

`build()` всегда шлёт `burn`:

```ts
handlers.onProject(await generateSubtitles(projectId, assetId, 'burn'))
```

`onChange` больше не передаёт режим. Тип хендлера: `onChange: (cues: Cue[]) => void`. Слушатель `#sub-mode` удалить.

- [ ] **Step 2: Правка карточек идёт через `patchCues`**

В `editor.ts`:

```ts
onChange: cues => applySubtitles(cues),

function applySubtitles(cues: Cue[]): void {
  if (!project) return
  remember()
  const было = project.doc.subtitles
  const subs = patchCues(было, cues)
  const previous = было?.cues ?? []
  const textOnly =
    cues.length === previous.length
    && cues.every((c, i) => {
      const prev = previous[i]
      return prev !== undefined && c.start === prev.start && c.end === prev.end
    })
  project = { ...project, doc: { ...project.doc, subtitles: subs } }
  if (textOnly) subtitles.adopt(project)
  else render()
  saver.schedule(project)
}
```

Сравнение `mode` в `textOnly` больше не нужно: панель режим не меняет.

- [ ] **Step 3: Прогнать фронт**

```bash
cd web && npm test
```

Ожидание: PASS.

- [ ] **Step 4: Commit**

```bash
git add web/src/subtitles.ts web/src/editor.ts
git commit -m "fix(web): панель субтитров больше не притворяется выключателем"
```

---

### Task 6: Три вкладки, новость на «Субтитры», галочка над шкалой

**Files:** `web/src/editor.ts`, `web/src/style.css`

- [ ] **Step 1: Разметка**

Вкладки: Исходник · Субтитры · Рендер. Нет `data-tab="transcript"`, нет `#ed-transcript`, нет `data-tab="versions"`, нет `#ed-versions`, нет `#ed-save`.

В ряду монтажа сразу после «Разрезать»:

```html
<label class="burn">
  <input id="ed-burn" type="checkbox" disabled />
  Субтитры
</label>
```

- [ ] **Step 2: Снять монтаж транскрибации**

Удалить `import { mountTranscript }`, вызов `mountTranscript`, `transcript.setAsset` / `setTime` / `stop`, слушатель `timeupdate` для слов. `mounted` начинается с `['source']`.

Новость о готовой расшифровке — только `markNews('subtitles')`. Строку `markNews('transcript')` убрать.

Комментарий про «пять панелей» поправить: три вкладки.

- [ ] **Step 3: Галочка**

После объявления `subtitles` / `applySubtitles`:

```ts
const burnBox = el.querySelector('#ed-burn') as HTMLInputElement

function syncBurn(): void {
  const subs = project?.doc.subtitles
  burnBox.disabled = !cuesReady(subs)
  burnBox.checked = burnEnabled(subs)
}

function applyEnabled(on: boolean): void {
  if (!project || !cuesReady(project.doc.subtitles)) return
  remember()
  project = {
    ...project,
    doc: { ...project.doc, subtitles: { ...project.doc.subtitles!, enabled: on } },
  }
  syncBurn()
  saver.schedule(project)
}

burnBox.addEventListener('change', () => applyEnabled(burnBox.checked))
```

`render()` вызывает `syncBurn()` и `subtitles.setProject`. Смена галочки **не** зовёт `render()`: карточки не пересобираются, `sameSubtitleView` это тоже страхует.

`onProject` после «Собрать субтитры» по-прежнему делает `render()` — галочка включится из `enabled: true` ответа.

- [ ] **Step 4: Стили**

Рядом с правилами редактора:

```css
.burn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 14px;
  cursor: pointer;
  user-select: none;
}
.burn:has(input:disabled) {
  opacity: 0.45;
  cursor: not-allowed;
}
.burn input { accent-color: var(--brand); }
```

Токены `--muted` и `--brand` уже есть в разделе 2 `style.css`.

- [ ] **Step 5: Сборка типов**

```bash
cd web && npx tsc --noEmit
```

Ожидание: PASS. Нет ссылок на `transcript.` в `editor.ts`.

- [ ] **Step 6: Commit**

```bash
git add web/src/editor.ts web/src/style.css
git commit -m "feat(web): одна вкладка субтитров и галочка над шкалой"
```

---

### Task 7: Сохранения проекта в шапке

**Files:** `web/src/editor.ts`, `web/src/versions.ts`, `web/src/style.css`

- [ ] **Step 1: Разметка шапки**

В `.project-bar` после `#ed-notice`:

```html
<div class="saves" id="ed-saves">
  <button type="button" class="btn btn-ghost" id="ed-saves-toggle" aria-expanded="false">
    Сохранения проекта
  </button>
  <div class="saves-panel" id="ed-saves-panel" hidden></div>
</div>
```

- [ ] **Step 2: `mountVersions` — содержимое списка, не страница**

```ts
  el.innerHTML = `
    <form id="ver-form" class="row">
      <input name="label" placeholder="Например: до перестановки" maxlength="200" />
      <button type="submit">Сохранить</button>
    </form>
    <ul id="ver-list" class="versions"><li class="muted">Пока нет</li></ul>
    <pre id="ver-error" hidden></pre>`
```

Заголовок «Точки сохранения» не дублировать: его несёт кнопка шапки. `flush` перед снимком и перед возвратом уже есть — не трогать.

- [ ] **Step 3: Открытие и закрытие**

В `editor.ts` вместо монтирования на вкладке `versions`:

```ts
const saves = el.querySelector('#ed-saves') as HTMLElement
const savesToggle = el.querySelector('#ed-saves-toggle') as HTMLButtonElement
const savesPanel = el.querySelector('#ed-saves-panel') as HTMLElement

function closeSaves(): void {
  savesPanel.hidden = true
  savesToggle.setAttribute('aria-expanded', 'false')
  document.removeEventListener('pointerdown', onPointerDown, true)
}

function onPointerDown(event: PointerEvent): void {
  if (!saves.contains(event.target as Node)) closeSaves()
}

savesToggle.addEventListener('click', () => {
  if (!savesPanel.hidden) {
    closeSaves()
    return
  }
  if (!versions) {
    versions = mountVersions(
      savesPanel,
      projectId,
      restored => {
        remember()
        project = restored
        timelineTime = 0
        render()
        if (playing) seek(0)
        notice('Вернулись к сохранённой точке')
        closeSaves()
      },
      async () => {
        if (project && saver.pending()) await saver.flush(project)
      },
    )
  } else {
    void versions.refresh()
  }
  savesPanel.hidden = false
  savesToggle.setAttribute('aria-expanded', 'true')
  document.addEventListener('pointerdown', onPointerDown, true)
})
```

В `stop()` редактора: `closeSaves()`, чтобы слушатель документа не остался после ухода с экрана.

`openPanel` больше не знает `'versions'`. Кнопки «Сохранить точку» над шкалой нет — обработчик `#ed-save` удалить, `createCheckpoint` из `editor.ts` не импортировать: снимок делает форма в списке.

- [ ] **Step 4: Стили**

```css
.project-bar .saves {
  margin-left: auto;
  position: relative;
}
.saves-panel {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  z-index: 5;
  min-width: 320px;
  padding: 12px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 12px;
}
```

- [ ] **Step 5: Типы и тесты**

```bash
cd web && npx tsc --noEmit && npm test
uv run python -m pytest -q
```

Ожидание: PASS с обеих сторон.

- [ ] **Step 6: Commit**

```bash
git add web/src/editor.ts web/src/versions.ts web/src/style.css
git commit -m "feat(web): сохранения проекта открываются из шапки"
```

---

### Task 8: Документы догоняют код

**Files:** `README.md`, `docs/superpowers/specs/2026-09-06-ux-redesign-and-subtitle-review-design.md`

Не переписывать старые планы. Поправить то, что человек прочитает сегодня.

- [ ] **Step 1: README**

§ «Точки сохранения и навигация (M2c)»: кнопка живёт в шапке, называется «Сохранения проекта», пул тот же.

§ M4b: абзац про «Взять кусок» и панель транскрипта убрать или заменить одной строкой — монтаж по словам с экрана ушёл, клипы кладутся из «Исходника».

§ M5: вкладки «Исходник · Субтитры · Рендер». После карточек — галочка «Субтитры» над шкалой (`enabled`). «Наложить» больше не шаг. В шапке — сохранения проекта.

- [ ] **Step 2: Спека UX 2026-09-06**

Вкладки в §4.5: `Исходник` · `Субтитры` · `Рендер`. Шапка справа — «Сохранения проекта».

§5.1 шаг 4: вместо «Наложить на видео» — галочка над шкалой. До сборки реплик она неактивна; после — включена; снятие не удаляет карточки.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/specs/2026-09-06-ux-redesign-and-subtitle-review-design.md
git commit -m "docs: вкладки субтитров и сохранения в шапке"
```

---

### Task 9: Живая проверка

Локально: API на `:8010`, Vite на `:5173`. Нужен вход через Яндекс.

- [ ] Расшифровать выбранную запись со вкладки «Субтитры» (вкладки «Транскрибация» нет).
- [ ] «Собрать субтитры» → карточки, галочка над шкалой включена, выбора burn/soft нет.
- [ ] Поправить текст карточки → черновик: правка в кадре.
- [ ] Снять галочку → черновик без субтитров, карточки на месте.
- [ ] Включить снова → в кадре тот же вычитанный текст.
- [ ] «Сохранения проекта» в шапке: снимок, возврат. Автосохранение монтажа снимок не создаёт.
- [ ] Клик снаружи закрывает список. Над шкалой кнопки «Сохранить точку» нет.

Если что-то из этого не так — чинить в этой же ветке, не выкатывать.

Полный прогон перед слиянием:

```bash
uv run python -m pytest
uv run ruff check .
cd web && npm test && npm run build
```

Выкатка — только по отдельной просьбе, на `main`, скриптом деплоя.
