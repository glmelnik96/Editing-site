# M2d: таймкоды, разметка выделения, индикация переноса, отмена действия

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** закрыть четыре замечания с показа. Выделенный в исходнике кусок должен быть виден полосой, а не только подписью, и задаваться таймкодом с клавиатуры. По шкале редактора нужно уметь прыгать на введённый таймкод. При переносе клипа должно быть видно, куда он встанет. Любое действие должно откатываться кнопкой, с памятью на пять шагов.

**Architecture:** вся арифметика — в чистых модулях с тестами (`timecode.ts`, `history.ts`, плюс `dropTarget` в модели шкалы). DOM-обвязка тонкая: полоса выделения в панели исходника, призрак места вставки в шкале, поля ввода и кнопка отмены в редакторе. Отмена держится в памяти страницы и не путается с точками сохранения: точки — ручные и на сервере, отмена — автоматическая и локальная.

**Спека:** `docs/superpowers/specs/2026-09-03-video-editor-design.md`, раздел 8. **Предыдущие планы:** `2026-09-05-m2b-timeline.md`, `2026-09-05-m2c-versions-playhead.md`.

---

## Решения M2d

| Вопрос | Решение | Почему |
|---|---|---|
| Формат таймкода | `83.5`, `1:23.5`, `1:02:03.4`; запятая как точка | Русская раскладка ставит запятую, отвергать её — злить пользователя |
| Неразобранный ввод | Поле подсвечивается, значение не меняется | Молча подставить ноль хуже, чем ничего не сделать |
| Выделение в исходнике | Полоса во всю длину файла: залитый кусок между началом и концом, две ручки, риска текущего кадра | Подпись «0:02 — 0:08» не показывает, какая это часть файла |
| Ручки выделения | Тянутся указателем, снизу подписаны таймкодом | Точное значение вводится полем, грубое — рукой |
| Куда встанет клип | Призрак блока на будущем месте плюс подпись «позиция N из M» | Просьба пользователя: сейчас непонятно, куда переносится |
| Как считается призрак | Через ту же `moveClip`, что и сама правка | Иначе показ и результат разъедутся |
| Отмена | Пять последних состояний документа в памяти страницы, кнопка и Ctrl+Z | Просьба пользователя. На сервер не тащим: там уже есть ручные точки |
| Что попадает в отмену | Любая правка документа: добавление, удаление, перенос, подрезка, разрез, смена пропорции, возврат к точке | Всё, что меняет монтаж |
| Отмена после чужой правки | История чистится при конфликте версий | База под нами сменилась, откатывать на старое нельзя |
| Отмена и перезагрузка | История не переживает перезагрузку | Это отмена действия, а не история проекта; для долгого хранения есть точки |

## Структура файлов

| Файл | Обязанность |
|---|---|
| `web/src/timecode.ts` | Разбор и показ таймкода, чистые функции |
| `web/src/history.ts` | Стопка последних состояний с ограничением |
| `web/src/timeline/model.ts` | + `dropTarget`: куда встанет переносимый клип |
| `web/src/source.ts` | Полоса выделения, ручки, поля таймкодов |
| `web/src/timeline/view.ts` | Призрак места вставки при переносе |
| `web/src/editor.ts` | Поле перехода по таймкоду, кнопка отмены, Ctrl+Z |
| `web/src/style.css` | Стили полосы, призрака, полей |

Команды: `cd web && npm test`, `cd web && npm run build`. Серверные `uv run python -m pytest` и `uv run ruff check .` должны остаться зелёными (сервер не трогаем). Ветка: `m2d-timecodes-undo` от `main`.

---

### Task 1: Чистые модули — таймкод, история, место вставки

**Files:**
- Create: `web/src/timecode.ts`, `web/src/timecode.test.ts`, `web/src/history.ts`, `web/src/history.test.ts`
- Modify: `web/src/timeline/model.ts`, `web/src/timeline/model.test.ts`

- [ ] **Step 1: Тесты таймкода**

Создать `web/src/timecode.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { formatTimecode, parseTimecode } from './timecode'

describe('разбор таймкода', () => {
  it('принимает голые секунды', () => {
    expect(parseTimecode('12')).toBe(12)
    expect(parseTimecode('12.5')).toBe(12.5)
    expect(parseTimecode('120.25')).toBe(120.25)
    expect(parseTimecode('0')).toBe(0)
  })

  it('принимает минуты и часы', () => {
    expect(parseTimecode('1:23')).toBe(83)
    expect(parseTimecode('1:23.5')).toBe(83.5)
    expect(parseTimecode('01:02:03')).toBe(3723)
    expect(parseTimecode('1:02:03.4')).toBe(3723.4)
  })

  it('принимает запятую как разделитель дробной части', () => {
    expect(parseTimecode('1:23,5')).toBe(83.5)
    expect(parseTimecode('12,25')).toBe(12.25)
  })

  it('терпит пробелы по краям', () => {
    expect(parseTimecode('  1:23  ')).toBe(83)
  })

  it('отвергает мусор, а не подставляет ноль', () => {
    for (const bad of ['', '   ', 'нет', '1:2:3:4', '1:60', '1:23:60', '-5', '1..2', '::', '1:', 'e5']) {
      expect(parseTimecode(bad)).toBeNull()
    }
  })

  it('округляет до миллисекунды', () => {
    expect(parseTimecode('1.23456')).toBe(1.235)
  })
})

describe('показ таймкода', () => {
  it('показывает минуты и секунды с десятыми', () => {
    expect(formatTimecode(0)).toBe('0:00.0')
    expect(formatTimecode(83.5)).toBe('1:23.5')
    expect(formatTimecode(9.04)).toBe('0:09.0')
  })

  it('добавляет часы, когда они есть', () => {
    expect(formatTimecode(3723.45)).toBe('1:02:03.5')
    expect(formatTimecode(3600)).toBe('1:00:00.0')
  })

  it('не порождает шестидесятую секунду при округлении', () => {
    expect(formatTimecode(59.98)).toBe('1:00.0')
    expect(formatTimecode(3599.99)).toBe('1:00:00.0')
  })

  it('отрицательное и нечисло показывает нулём', () => {
    expect(formatTimecode(-5)).toBe('0:00.0')
    expect(formatTimecode(Number.NaN)).toBe('0:00.0')
  })

  it('разбор и показ сходятся друг с другом', () => {
    for (const value of [0, 1.5, 83.5, 3723.4]) {
      expect(parseTimecode(formatTimecode(value))).toBe(value)
    }
  })
})
```

- [ ] **Step 2: Тесты истории**

Создать `web/src/history.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { createHistory } from './history'

describe('стопка отмены', () => {
  it('пустая ничего не отдаёт', () => {
    const h = createHistory<string>(5)
    expect(h.canUndo()).toBe(false)
    expect(h.undo()).toBeNull()
  })

  it('отдаёт состояния в обратном порядке', () => {
    const h = createHistory<string>(5)
    h.push('первое')
    h.push('второе')
    expect(h.undo()).toBe('второе')
    expect(h.undo()).toBe('первое')
    expect(h.undo()).toBeNull()
  })

  it('держит только последние пять', () => {
    const h = createHistory<number>(5)
    for (let i = 1; i <= 8; i++) h.push(i)
    expect(h.size()).toBe(5)
    expect(h.undo()).toBe(8)
    expect([h.undo(), h.undo(), h.undo(), h.undo()]).toEqual([7, 6, 5, 4])
    expect(h.undo()).toBeNull()
  })

  it('чистится целиком', () => {
    const h = createHistory<string>(5)
    h.push('а')
    h.clear()
    expect(h.canUndo()).toBe(false)
  })

  it('знает, сколько шагов доступно', () => {
    const h = createHistory<string>(3)
    expect(h.size()).toBe(0)
    h.push('а')
    h.push('б')
    expect(h.size()).toBe(2)
    h.undo()
    expect(h.size()).toBe(1)
  })
})
```

- [ ] **Step 3: Тест места вставки**

Добавить в `web/src/timeline/model.test.ts` (в блок «правки списка»):

```ts
  it('считает, куда встанет переносимый клип', () => {
    // three: c1 [0,4), c2 [4,6), c3 [6,9.5)
    expect(dropTarget(three, 0, 5)).toEqual({ to: 1, start: 2 })
    expect(dropTarget(three, 2, 1)).toEqual({ to: 0, start: 0 })
    expect(dropTarget(three, 0, 100)).toEqual({ to: 2, start: 5.5 })
    expect(dropTarget(three, 1, 4.5)).toEqual({ to: 1, start: 4 })
  })

  it('место вставки совпадает с тем, что сделает перенос', () => {
    const preview = dropTarget(three, 0, 7)!
    const moved = moveClip(three, 0, preview.to)
    const index = moved.findIndex(c => c.id === 'c1')
    expect(timelineStart(moved, index)).toBe(preview.start)
  })

  it('чужой номер клипа не роняет расчёт', () => {
    expect(dropTarget(three, 9, 1)).toBeNull()
    expect(dropTarget([], 0, 1)).toBeNull()
  })
```

Импорт `dropTarget` добавить в список из `./model`.

- [ ] **Step 4: Запустить, убедиться, что падает**

Run: `cd web && npm test`
Expected: FAIL — нет модулей `./timecode`, `./history` и функции `dropTarget`.

- [ ] **Step 5: Таймкод**

Создать `web/src/timecode.ts`:

```ts
/**
 * Таймкод: разбор введённого человеком и показ на экране.
 *
 * Принимаем то, что человек реально печатает: голые секунды, минуты с секундами, часы с минутами,
 * запятую вместо точки (на русской раскладке она под рукой). Мусор возвращает null: подставить
 * вместо непонятного ввода ноль — значит молча увести курсор в начало.
 */

const SHAPE = /^\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?$/

/** Секунды из строки или null, если разобрать не вышло. */
export function parseTimecode(text: string): number | null {
  const cleaned = (text ?? '').trim().replace(',', '.')
  if (!SHAPE.test(cleaned)) return null
  const parts = cleaned.split(':')
  const numbers = parts.map(Number)
  if (numbers.some(n => !Number.isFinite(n))) return null
  // Минуты и секунды в составном таймкоде обязаны быть меньше шестидесяти: «1:60» — опечатка.
  if (parts.length > 1 && numbers.slice(1).some(n => n >= 60)) return null
  const seconds = numbers.reduce((total, part) => total * 60 + part, 0)
  return Math.round(seconds * 1000) / 1000
}

/** Секунды в «1:23.5» или «1:02:03.5». Отрицательное и нечисло показываем нулём. */
export function formatTimecode(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0
  // Округляем до десятых заранее: иначе 59.98 превратилось бы в «0:60.0».
  const tenths = Math.round(safe * 10)
  const whole = Math.floor(tenths / 10)
  const rest = tenths % 10
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor((whole % 3600) / 60)
  const secs = whole % 60
  const tail = `${String(secs).padStart(2, '0')}.${rest}`
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${tail}` : `${minutes}:${tail}`
}
```

- [ ] **Step 6: История**

Создать `web/src/history.ts`:

```ts
/**
 * Стопка последних состояний для отмены действия.
 *
 * Живёт в памяти страницы и не переживает перезагрузку: это отмена действия, а не история проекта.
 * Для долгого хранения есть точки сохранения на сервере.
 */

export function createHistory<T>(limit = 5) {
  let items: T[] = []
  return {
    /** Запомнить состояние ДО правки. Самое старое вытесняется. */
    push(state: T): void {
      items.push(state)
      if (items.length > limit) items = items.slice(items.length - limit)
    },
    /** Последнее запомненное состояние или null, если откатывать нечего. */
    undo(): T | null {
      return items.pop() ?? null
    },
    canUndo(): boolean {
      return items.length > 0
    },
    size(): number {
      return items.length
    },
    clear(): void {
      items = []
    },
  }
}
```

- [ ] **Step 7: Место вставки**

Добавить в `web/src/timeline/model.ts` в конец:

```ts
/**
 * Куда встанет переносимый клип, если отпустить указатель на этом времени шкалы.
 *
 * Считается через ту же moveClip, что выполняет саму правку: иначе показ и результат разъехались бы.
 * Возвращает номер позиции и время начала клипа на новом месте.
 */
export function dropTarget(clips: Clip[], from: number, time: number): { to: number; start: number } | null {
  if (from < 0 || from >= clips.length) return null
  const found = clipAt(clips, time)
  const to = found ? found.index : clips.length - 1
  const moved = moveClip(clips, from, to)
  const index = moved.findIndex(c => c.id === clips[from].id)
  return { to, start: timelineStart(moved, index) }
}
```

- [ ] **Step 8: Прогон и коммит**

Run: `cd web && npm test && npm run build`
Expected: зелено, тестов стало 70 + около 20 новых.

```bash
git add web/src/timecode.ts web/src/timecode.test.ts web/src/history.ts web/src/history.test.ts web/src/timeline/model.ts web/src/timeline/model.test.ts
git commit -m "feat(web): timecode parsing, undo history and drop target"
```

---

### Task 2: Полоса выделения и таймкоды в панели исходника

**Files:**
- Modify: `web/src/source.ts`, `web/src/style.css`

Модуль работает с DOM, тестами не покрывается: арифметика уже в `timecode.ts`.

- [ ] **Step 1: Разметка**

В `web/src/source.ts` заменить содержимое `el.innerHTML` на:

```ts
  el.innerHTML = `
    <main class="card">
      <h3>Исходник</h3>
      <select id="src-pick"><option value="">— выберите файл —</option></select>
      <div id="src-player"></div>
      <div class="src-strip" id="src-strip" title="Клик — перемотка, ручки — границы куска">
        <div class="src-sel" id="src-sel"></div>
        <b class="src-handle src-handle-in" id="src-h-in"></b>
        <b class="src-handle src-handle-out" id="src-h-out"></b>
        <i class="src-cursor" id="src-cursor"></i>
      </div>
      <div class="row">
        <button id="src-mark-in" type="button" title="Взять начало с плеера">Начало</button>
        <input id="src-in" class="tc" inputmode="decimal" placeholder="0:00.0" />
        <button id="src-mark-out" type="button" title="Взять конец с плеера">Конец</button>
        <input id="src-out" class="tc" inputmode="decimal" placeholder="0:00.0" />
      </div>
      <div class="row">
        <span id="src-range" class="muted">весь файл</span>
        <button id="src-add" type="button" disabled>Добавить в шкалу</button>
      </div>
      <p class="muted" id="src-note"></p>
    </main>`
```

и получить узлы: `strip`, `sel`, `handleIn`, `handleOut`, `cursor`, `inputIn`, `inputOut`.

- [ ] **Step 2: Показ выделения**

Заменить `refreshRange` на:

```ts
  /** Полоса, поля и подпись показывают одно и то же состояние: from, to и длительность файла. */
  function refreshRange(): void {
    const total = current?.duration ?? 0
    const pct = (value: number) => (total > 0 ? `${(value / total) * 100}%` : '0%')
    sel.style.left = pct(from)
    sel.style.width = total > 0 ? `${((to - from) / total) * 100}%` : '0%'
    handleIn.style.left = pct(from)
    handleOut.style.left = pct(to)
    strip.classList.toggle('empty', !current)
    if (document.activeElement !== inputIn) inputIn.value = formatTimecode(from)
    if (document.activeElement !== inputOut) inputOut.value = formatTimecode(to)
    inputIn.classList.remove('bad')
    inputOut.classList.remove('bad')
    rangeLabel.textContent = current
      ? `кусок ${formatTimecode(to - from)} из ${formatTimecode(total)}`
      : 'весь файл'
    addButton.disabled = !current || to - from < MIN_PIECE
  }
```

с константой `const MIN_PIECE = 0.1` и импортом `import { formatTimecode, parseTimecode } from './timecode'`.

- [ ] **Step 3: Границы из полей и с полосы**

Добавить общий приёмник значений и обработчики:

```ts
  /** Ставит границу, не давая ей вывернуться наизнанку или выйти за длительность файла. */
  function setEdge(edge: 'in' | 'out', value: number): void {
    const total = current?.duration ?? 0
    if (edge === 'in') from = Math.max(0, Math.min(value, to - MIN_PIECE))
    else to = Math.min(total, Math.max(value, from + MIN_PIECE))
    refreshRange()
  }

  function readInput(input: HTMLInputElement, edge: 'in' | 'out'): void {
    if (!current) return
    const parsed = parseTimecode(input.value)
    if (parsed === null) {
      // Непонятный ввод не двигает границу: подсвечиваем поле и оставляем прежнее значение.
      input.classList.add('bad')
      return
    }
    setEdge(edge, parsed)
  }

  for (const [input, edge] of [[inputIn, 'in'], [inputOut, 'out']] as const) {
    input.addEventListener('change', () => readInput(input, edge))
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault()
        readInput(input, edge)
      }
    })
  }
```

Перемотка и перетаскивание ручек по полосе:

```ts
  const timeAtStrip = (clientX: number): number => {
    const rect = strip.getBoundingClientRect()
    const total = current?.duration ?? 0
    if (rect.width <= 0 || total <= 0) return 0
    return Math.max(0, Math.min(total, ((clientX - rect.left) / rect.width) * total))
  }

  let dragEdge: 'in' | 'out' | null = null
  strip.addEventListener('pointerdown', event => {
    if (!current) return
    const target = event.target as HTMLElement
    strip.setPointerCapture(event.pointerId)
    if (target === handleIn || target === handleOut) {
      dragEdge = target === handleIn ? 'in' : 'out'
      return
    }
    const player = video()
    if (player) player.currentTime = timeAtStrip(event.clientX)
  })
  strip.addEventListener('pointermove', event => {
    if (dragEdge) setEdge(dragEdge, timeAtStrip(event.clientX))
  })
  const endStripDrag = () => {
    dragEdge = null
  }
  strip.addEventListener('pointerup', endStripDrag)
  strip.addEventListener('pointercancel', endStripDrag)
```

Риска текущего кадра: в `choose` после вставки плеера повесить

```ts
    const player = video()
    if (player) {
      player.addEventListener('timeupdate', () => {
        const total = current?.duration ?? 0
        cursor.style.left = total > 0 ? `${(player.currentTime / total) * 100}%` : '0%'
      })
    }
```

Кнопки «Начало» и «Конец» перевести на `setEdge`:

```ts
  el.querySelector('#src-mark-in')!.addEventListener('click', () => {
    const player = video()
    if (player && current) setEdge('in', player.currentTime)
  })
  el.querySelector('#src-mark-out')!.addEventListener('click', () => {
    const player = video()
    if (player && current) setEdge('out', player.currentTime)
  })
```

- [ ] **Step 4: Стили**

Добавить в конец `web/src/style.css`:

```css
/* Полоса исходника: весь файл, залитый кусок между границами, ручки и риска текущего кадра. */
.src-strip { position: relative; height: 26px; margin: 8px 0; border-radius: 4px;
  background: #8882; cursor: pointer; touch-action: none; }
.src-strip.empty { opacity: .4; cursor: default; }
.src-sel { position: absolute; top: 0; bottom: 0; background: #3a7d5c88; border-left: 2px solid #7fd1ab;
  border-right: 2px solid #7fd1ab; }
.src-handle { position: absolute; top: -2px; bottom: -2px; width: 10px; margin-left: -5px;
  background: #7fd1ab; border-radius: 3px; cursor: ew-resize; }
.src-cursor { position: absolute; top: -2px; bottom: -2px; width: 2px; background: #ff6b6b; pointer-events: none; }
.tc { width: 92px; flex: 0 0 auto; font-variant-numeric: tabular-nums; }
.tc.bad { outline: 2px solid #b3261e; }
```

- [ ] **Step 5: Прогон и коммит**

Run: `cd web && npm test && npm run build`

```bash
git add web/src/source.ts web/src/style.css
git commit -m "feat(web): source selection strip with handles and timecode fields"
```

---

### Task 3: Индикация переноса клипа

**Files:**
- Modify: `web/src/timeline/view.ts`, `web/src/style.css`

- [ ] **Step 1: Призрак места вставки**

В `web/src/timeline/view.ts`:

1. В разметку дорожки добавить призрак:

```ts
      <div class="track" id="tl-track"><div class="drop-ghost" id="tl-drop" hidden></div><div class="playhead" id="tl-playhead"></div></div>
```

и получить `const ghost = el.querySelector('#tl-drop') as HTMLElement`.

2. Импортировать `dropTarget` из `./model`.

3. В `pointermove` при переносе показывать призрак и человеческую подпись:

```ts
    if (drag.kind === 'move') {
      const preview = drag.moved ? dropTarget(current.clips, drag.index, timeAt(event.clientX)) : null
      if (preview) {
        const width = clipDuration(drag.clips[drag.index]) * current.pxPerSec
        ghost.hidden = false
        ghost.style.left = `${preview.start * current.pxPerSec}px`
        ghost.style.width = `${Math.max(MIN_BLOCK_PX, width)}px`
        hint.textContent = `«${clip.id}» встанет на ${preview.to + 1}-е место из ${current.clips.length}`
      }
    } else {
      ...как было...
    }
```

(`clipDuration` и `MIN_BLOCK_PX` импортировать из `./model`.)

4. Переносимый блок пометить, чтобы было видно, что тянут именно его: в `pointerdown` после `render()` добавить

```ts
    track.querySelector(`.block[data-id="${CSS.escape(id)}"]`)?.classList.add('dragging')
```

5. Прятать призрак в `finishDrag` (в обеих ветках) и в конце: `ghost.hidden = true`.

- [ ] **Step 2: Стили**

Добавить в конец `web/src/style.css`:

```css
/* Призрак показывает, куда встанет клип, если отпустить указатель. */
.drop-ghost { position: absolute; top: 2px; height: 72px; z-index: 2; pointer-events: none;
  border: 2px dashed #7fd1ab; border-radius: 5px; background: #7fd1ab22; }
.block.dragging { opacity: .55; }
```

- [ ] **Step 3: Прогон и коммит**

Run: `cd web && npm test && npm run build`

```bash
git add web/src/timeline/view.ts web/src/style.css
git commit -m "feat(web): ghost preview of where a dragged clip lands"
```

---

### Task 4: Переход по таймкоду и отмена действия

**Files:**
- Modify: `web/src/editor.ts`, `web/src/style.css`

- [ ] **Step 1: Разметка**

В `web/src/editor.ts` в строку кнопок добавить отмену и поле перехода, заменив показ времени:

```html
          <button id="ed-undo" type="button" disabled title="Отменить последнее действие (Ctrl+Z)">Отменить</button>
          ...
          <input id="ed-goto" class="tc" inputmode="decimal" title="Перейти к таймкоду" />
          <span class="muted" id="ed-total"></span>
```

Подсказку в шапке дополнить: `пробел — играть, стрелки — шаг, Shift — точнее, Ctrl+Z — отменить`.

- [ ] **Step 2: История**

Завести историю и правило записи:

```ts
import { createHistory } from './history'
import { formatTimecode, parseTimecode } from './timecode'

  const history = createHistory<ProjectDoc>(5)
  const undoButton = el.querySelector('#ed-undo') as HTMLButtonElement
  const gotoInput = el.querySelector('#ed-goto') as HTMLInputElement

  /** Запомнить состояние ДО правки: именно к нему вернёт кнопка «Отменить». */
  function remember(): void {
    if (project) history.push(project.doc)
    undoButton.disabled = !history.canUndo()
  }
```

(`ProjectDoc` импортировать из `./project`.)

В `applyClips` первой строкой после проверки добавить `remember()`. То же самое в обработчике смены пропорции. В колбэке возврата к точке сохранения — тоже `remember()` перед заменой документа, чтобы возврат можно было отменить.

- [ ] **Step 3: Отмена**

```ts
  function undo(): void {
    if (!project) return
    const previous = history.undo()
    undoButton.disabled = !history.canUndo()
    if (!previous) return
    project = { ...project, doc: previous }
    render()
    saver.schedule(project)
    if (playing) seek(Math.min(timelineTime, totalDuration(previous.clips)))
    notice('Действие отменено')
  }
  undoButton.addEventListener('click', undo)
```

В обработчике конфликта версий (`onConflict`) добавить `history.clear()` и `undoButton.disabled = true`: база под нами сменилась, откатывать на своё старое нельзя.

- [ ] **Step 4: Переход по таймкоду**

```ts
  function applyGoto(): void {
    if (!project) return
    const parsed = parseTimecode(gotoInput.value)
    if (parsed === null) {
      gotoInput.classList.add('bad')
      return
    }
    gotoInput.classList.remove('bad')
    seek(Math.max(0, Math.min(parsed, totalDuration(project.doc.clips))))
  }
  gotoInput.addEventListener('change', applyGoto)
  gotoInput.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault()
      applyGoto()
    }
  })
```

`showTime` переписать так, чтобы он не затирал то, что человек печатает:

```ts
  function showTime(): void {
    if (document.activeElement !== gotoInput) {
      gotoInput.value = formatTimecode(timelineTime)
      gotoInput.classList.remove('bad')
    }
    totalBox.textContent = project ? `из ${formatTimecode(totalDuration(project.doc.clips))}` : ''
  }
```

Строку с `totalBox.textContent` в `render` убрать: теперь общее время показывает `showTime`.

- [ ] **Step 5: Ctrl+Z**

В `onKey` первым условием (до проверки пробела) добавить:

```ts
    if ((event.ctrlKey || event.metaKey) && event.code === 'KeyZ') {
      event.preventDefault()
      undo()
      return
    }
```

Проверка «фокус в поле ввода» должна стоять раньше: в поле таймкода Ctrl+Z обязан отменять ввод текста, а не правку монтажа.

- [ ] **Step 6: Прогон и коммит**

Run: `cd web && npm test && npm run build`

```bash
git add web/src/editor.ts web/src/style.css
git commit -m "feat(web): undo with a five-step history and timecode jump"
```

---

### Task 5: Документация и выкатка

**Files:**
- Modify: `README.md`
- Живая проверка в браузере (координатор)

- [ ] **Step 1: README**

Дополнить раздел про редактор:

```markdown
### Таймкоды, индикация и отмена (M2d)

- В панели исходника под плеером идёт полоса всего файла: выделенный кусок залит, границы тянутся ручками, риска показывает текущий кадр. Начало и конец задаются и полем таймкода: `83.5`, `1:23.5`, `1:02:03.4`, запятая вместо точки тоже принимается. Непонятный ввод подсвечивается и границу не двигает.
- В редакторе текущее время — поле: введите таймкод и нажмите Enter, курсор прыгнет туда.
- При переносе клипа пунктирный призрак показывает, куда он встанет, а подпись говорит номер будущей позиции. Призрак считается той же функцией, что выполняет перенос, поэтому показ не расходится с результатом.
- Кнопка «Отменить» и Ctrl+Z откатывают последнее действие; в памяти пять последних состояний. История живёт в странице и не переживает перезагрузку — для долгого хранения есть точки сохранения. При конфликте версий история чистится: база сменилась под нами.
```

- [ ] **Step 2: Прогон и коммит**

Run: `cd web && npm test && npm run build && cd .. && uv run python -m pytest && uv run ruff check .`

```bash
git add README.md
git commit -m "docs: timecodes, drag preview and undo"
```

- [ ] **Step 3: Слияние и выкатка** (координатор)

- [ ] **Step 4: Живая проверка** (координатор)

1. Выбрать исходник: полоса показывает весь файл, риска идёт за плеером.
2. Отметить начало и конец кнопками: залитая часть меняется, поля показывают таймкоды.
3. Ввести в поле `0:03.5` и Enter: граница встала точно, полоса перерисовалась. Ввести мусор: поле подсветилось, граница не сдвинулась.
4. Потянуть ручку выделения: кусок меняется, поле обновляется.
5. В редакторе ввести таймкод в поле времени: курсор прыгнул.
6. Потянуть блок на новое место: виден пунктирный призрак и подпись с номером позиции; отпустить — клип встал ровно туда, куда показывал призрак.
7. Нажать «Отменить»: перенос откатился. Ещё раз: откатилась предыдущая правка. После пяти отмен кнопка гаснет.
8. Ctrl+Z работает так же; в поле ввода Ctrl+Z отменяет текст, а не монтаж.

---

## Поправки по ходу выполнения

- **Task 1**: в плане регулярное выражение таймкода допускало не больше трёх знаков после точки, из-за чего тест на округление `1.23456` → `1.235` не проходил: ввод не проходил проверку формы. Ограничение снято, округление до миллисекунды делает сама функция.
- **Task 3**: `onSeek` не вызывает перерисовку дорожки, поэтому класс `dragging`, повешенный на блок при нажатии, не снимался в ветке клика без переноса — блок остался бы затемнён навсегда. Класс снимается явно.
- **Стили** добавлены координатором отдельным коммитом до запуска исполнителей: три задачи писали бы в один `style.css` одновременно и затёрли бы друг друга.
- **Живая проверка в браузере 2026-09-05** (локальный стенд, домен с ПК не резолвится). Полоса исходника: при куске 5.5–30 из 30 секунд заливка встала на 18.33 % слева шириной 81.67 % — арифметика сходится; риска кадра на 40 % при 12 секундах из 30. Ввод `0:20,25` с запятой принят, `абракадабра` подсветила поле и границу не сдвинула. Переход по таймкоду: `0:14.5` дал курсор на 580 пикселях при 40 пикселях на секунду — точно. Призрак переноса показал 324 пикселя и подпись «встанет на 2-е место из 2», клип встал ровно туда. Отмена: семь разрезов подряд дали 9 блоков, отмена сработала ровно пять раз (глубина пула) и вернула к 4 блокам, после чего кнопка погасла. Ctrl+Z отменяет правку, но в поле ввода монтаж не трогает.
