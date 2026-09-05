# M2b: монтажная шкала, плеер склейки, автосохранение

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** человек открывает проект и монтирует: выделяет кусок в исходнике, кладёт его на шкалу, двигает и подрезает блоки, режет клип, смотрит склейку целиком. Правки сами уезжают на сервер, ответ сервера заменяет локальное состояние. Всё, что делает агент через API, теперь можно сделать руками.

**Architecture:** вся арифметика монтажа — чистые модули без DOM (`timeline/model.ts`, `strip.ts`, `playback.ts`), они покрыты тестами. Работа с DOM тонкая и проверяется живьём в браузере: так уже сделано с панелью файлов. Экраны разводит простой роутер по хешу: `#/` — файлы и настройки, `#/p/{id}` — редактор проекта. Автосохранение отдельным модулем `project.ts` с задержкой и одним запросом за раз.

**Tech Stack:** TypeScript, Vite 5, vitest, без фреймворков. Canvas для звуковой волны, CSS-фон для кадров из спрайта, два элемента `video` для склейки.

**Спека:** `docs/superpowers/specs/2026-09-03-video-editor-design.md`, разделы 4, 8, 10.6. **Предыдущий план:** `docs/superpowers/plans/2026-09-05-m2a-projects.md` (API проектов, версии, подтяжка резов).

---

## Решения M2b

| Вопрос | Решение | Почему |
|---|---|---|
| Что тестируем | Чистые модули: модель шкалы, клиент проекта, раскладка полоски и волны, состояние плеера | DOM-обвязка проверяется живьём; jsdom в зависимости не тянем |
| Роутер | Хеш: `#/` и `#/p/{id}` | Ссылку на проект можно переслать, история браузера работает, сервер не трогаем |
| Единица времени | Секунды с точностью до миллисекунды, как в документе | Никаких кадров и тиков: сервер считает так же |
| Состояние | Один объект `doc` плюс `version`; правка меняет `doc` и просит сохранить | Сервер отвечает нормализованным документом, и он заменяет локальный |
| Автосохранение | 500 мс тишины после последней правки, один запрос за раз, следующий ждёт | Раздел 8 спеки; очередь из одного места не даёт гонки версий |
| Конфликт версий | Перечитать проект, показать уведомление, локальные несохранённые правки отбросить | Молча склеивать две правки нельзя, а терять чужую хуже, чем свою последнюю |
| Ошибка проверки | Показать список полей и оставить документ как есть | Пользователь видит, что именно не так, и правит |
| Перетаскивание | Указатель (`pointerdown`/`move`/`up`), без библиотек | Работает и мышью, и пальцем |
| Подрезка | Ручки по краям блока, подсказка со временем, минимум 0.1 с | Раздел 8 спеки |
| Разрез | Кнопка режет выделенный клип по курсору шкалы на два | Один клип превращается в два с тем же ассетом |
| Кадры | Спрайт `thumbs.jpg` фоном блока, смещение из `thumbs.json` | Один запрос на ассет, дальше только CSS |
| Волна | `peaks.json` в canvas, столбик на пиксель | Тысячи столбиков в DOM тормозят, canvas рисуется мгновенно |
| Склейка | Два элемента `video`: активный играет, скрытый ждёт следующий клип на его точке входа | Раздел 8 спеки, шов без чёрного кадра |
| Музыка | Отдельный `audio`, громкость и затухания считаются на клиенте | Web Audio не нужен для простого затухания |
| Субтитры | `track` с `subs.vtt` для `source: "file"` | Готовый VTT уже кладёт сервер (M2a) |
| Кадр вывода | Контейнер в пропорции `output.aspect`, содержимое `contain` при `pad` и `cover` при `crop` | Пользователь видит будущий кадр, а не исходный |

## Структура файлов

| Файл | Обязанность |
|---|---|
| `web/src/timeline/model.ts` | Чистая модель монтажа: длительность, поиск клипа, вставка, удаление, перестановка, разрез, подрезка, раскладка в пиксели |
| `web/src/project.ts` | Загрузка и сохранение проекта, задержка, один запрос за раз, конфликт версий |
| `web/src/strip.ts` | Волна из пиков и раскладка кадров из спрайта |
| `web/src/timeline/view.ts` | Отрисовка шкалы, перетаскивание, подрезка, разрез, удаление, выделение |
| `web/src/source.ts` | Панель исходника: выбор файла, плеер, выделение диапазона, кнопка «в шкалу» |
| `web/src/playback.ts` | Чистое состояние воспроизведения склейки плюс тонкий драйвер двух элементов `video` |
| `web/src/editor.ts` | Сборка экрана редактора: шапка проекта, панели, статус сохранения |
| `web/src/projects.ts` | Список проектов на главной: создать, открыть, удалить, завершить |
| `web/src/main.ts` | Роутер по хешу |
| `web/src/style.css` | Стили шкалы и редактора |

Команды: `cd web && npm test`, `cd web && npm run build`, плюс серверные `uv run python -m pytest` и `uv run ruff check .` (они должны остаться зелёными). Ветка: `m2b-timeline` от `main`.

---

### Task 1: Модель монтажа

**Files:**
- Create: `web/src/timeline/model.ts`, `web/src/timeline/model.test.ts`

- [ ] **Step 1: Тесты**

Создать `web/src/timeline/model.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import type { Clip } from './model'
import {
  clipAt,
  insertClip,
  layout,
  moveClip,
  newClipId,
  removeClip,
  sourceTime,
  splitAt,
  timelineStart,
  totalDuration,
  trimClip,
} from './model'

function clip(id: string, inS: number, outS: number, asset = 'ast_1'): Clip {
  return { id, asset_id: asset, in: inS, out: outS, snap_to_pauses: false, in_verified: false, out_verified: false }
}

const three = [clip('c1', 0, 4), clip('c2', 10, 12), clip('c3', 1, 4.5)]

describe('время шкалы', () => {
  it('складывает длительности клипов', () => {
    expect(totalDuration(three)).toBe(9.5)
    expect(totalDuration([])).toBe(0)
  })

  it('знает, где начинается каждый клип', () => {
    expect(timelineStart(three, 0)).toBe(0)
    expect(timelineStart(three, 1)).toBe(4)
    expect(timelineStart(three, 2)).toBe(6)
    expect(timelineStart(three, 9)).toBe(9.5)
  })

  it('находит клип по времени шкалы', () => {
    expect(clipAt(three, 0)?.index).toBe(0)
    expect(clipAt(three, 3.999)?.index).toBe(0)
    expect(clipAt(three, 4)?.index).toBe(1)
    expect(clipAt(three, 6.5)?.index).toBe(2)
    expect(clipAt(three, 9.5)).toBeNull()
    expect(clipAt(three, -1)).toBeNull()
    expect(clipAt([], 0)).toBeNull()
  })

  it('переводит время шкалы во время исходника', () => {
    expect(sourceTime(three, 0)).toEqual({ index: 0, assetId: 'ast_1', time: 0 })
    expect(sourceTime(three, 4.5)).toEqual({ index: 1, assetId: 'ast_1', time: 10.5 })
    expect(sourceTime(three, 100)).toBeNull()
  })
})

describe('правки списка', () => {
  it('вставляет клип в конец и в середину', () => {
    const added = insertClip(three, clip('c4', 0, 1), 1)
    expect(added.map(c => c.id)).toEqual(['c1', 'c4', 'c2', 'c3'])
    expect(insertClip(three, clip('c4', 0, 1)).map(c => c.id)).toEqual(['c1', 'c2', 'c3', 'c4'])
    expect(three).toHaveLength(3) // исходный список не меняется
  })

  it('удаляет клип по id', () => {
    expect(removeClip(three, 'c2').map(c => c.id)).toEqual(['c1', 'c3'])
    expect(removeClip(three, 'нет такого')).toHaveLength(3)
  })

  it('переставляет клип', () => {
    expect(moveClip(three, 0, 2).map(c => c.id)).toEqual(['c2', 'c3', 'c1'])
    expect(moveClip(three, 2, 0).map(c => c.id)).toEqual(['c3', 'c1', 'c2'])
    expect(moveClip(three, 1, 1).map(c => c.id)).toEqual(['c1', 'c2', 'c3'])
    expect(moveClip(three, 0, 9).map(c => c.id)).toEqual(['c2', 'c3', 'c1'])
  })

  it('режет клип по времени шкалы', () => {
    const cut = splitAt(three, 2)
    expect(cut.map(c => [c.id, c.in, c.out])).toEqual([
      ['c1', 0, 2],
      [cut[1].id, 2, 4],
      ['c2', 10, 12],
      ['c3', 1, 4.5],
    ])
    expect(cut[1].id).not.toBe('c1')
    expect(cut[1].asset_id).toBe('ast_1')
  })

  it('не режет по краю клипа и слишком близко к краю', () => {
    expect(splitAt(three, 0)).toBe(three)
    expect(splitAt(three, 4)).toBe(three)
    expect(splitAt(three, 0.05)).toBe(three)
    expect(splitAt(three, 3.95)).toBe(three)
    expect(splitAt(three, 100)).toBe(three)
  })

  it('разрез сбрасывает подтверждение новой границы', () => {
    const verified = [{ ...clip('c1', 0, 4), snap_to_pauses: true, in_verified: true, out_verified: true }]
    const cut = splitAt(verified, 2)
    expect(cut[0].in_verified).toBe(true)
    expect(cut[0].out_verified).toBe(false) // новый рез ещё не подтверждён
    expect(cut[1].in_verified).toBe(false)
    expect(cut[1].out_verified).toBe(true)
    expect(cut[1].snap_to_pauses).toBe(true)
  })

  it('подрезает клип и держит минимальную длину', () => {
    const list = [clip('c1', 5, 10)]
    expect(trimClip(list, 'c1', { in: 6 })[0].in).toBe(6)
    expect(trimClip(list, 'c1', { out: 9 })[0].out).toBe(9)
    expect(trimClip(list, 'c1', { in: 9.95 })[0].in).toBe(9.9) // не ближе 0.1 с к out
    expect(trimClip(list, 'c1', { out: 5.05 })[0].out).toBe(5.1)
    expect(trimClip(list, 'c1', { in: -3 })[0].in).toBe(0)
    expect(trimClip(list, 'c1', { out: 99 }, { duration: 12 })[0].out).toBe(12)
  })

  it('подрезка сбрасывает подтверждение только тронутой границы', () => {
    const list = [{ ...clip('c1', 5, 10), in_verified: true, out_verified: true }]
    const trimmed = trimClip(list, 'c1', { in: 6 })
    expect(trimmed[0].in_verified).toBe(false)
    expect(trimmed[0].out_verified).toBe(true)
  })

  it('округляет времена до миллисекунды', () => {
    const list = [clip('c1', 0, 10)]
    expect(trimClip(list, 'c1', { in: 1.00049 })[0].in).toBe(1)
    expect(splitAt(list, 3.33333)[0].out).toBe(3.333)
  })

  it('выдаёт неповторяющиеся id', () => {
    const ids = new Set([newClipId(three), newClipId(three), newClipId(three)])
    expect(ids.size).toBe(3)
    expect(newClipId(three).startsWith('c')).toBe(true)
  })
})

describe('раскладка в пиксели', () => {
  it('считает левый край и ширину блоков', () => {
    expect(layout(three, 10)).toEqual([
      { id: 'c1', left: 0, width: 40, start: 0, duration: 4 },
      { id: 'c2', left: 40, width: 20, start: 4, duration: 2 },
      { id: 'c3', left: 60, width: 35, start: 6, duration: 3.5 },
    ])
  })

  it('не даёт блоку схлопнуться в невидимую полоску', () => {
    const tiny = layout([clip('c1', 0, 0.1)], 10)
    expect(tiny[0].width).toBeGreaterThanOrEqual(8)
  })
})
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `cd web && npm test`
Expected: FAIL, нет модуля `./model`.

- [ ] **Step 3: Реализация**

Создать `web/src/timeline/model.ts`:

```ts
/**
 * Арифметика монтажа: список клипов, время шкалы против времени исходника, правки списка.
 *
 * Здесь нет ни DOM, ни запросов: всё чистые функции над массивом клипов. Любая правка возвращает
 * новый массив, исходный не меняется — так проще откатывать и сравнивать состояния.
 */

export type Clip = {
  id: string
  asset_id: string
  in: number
  out: number
  snap_to_pauses: boolean
  in_verified: boolean
  out_verified: boolean
}

export const MIN_CLIP = 0.1 // минимальная длина клипа, как на сервере
export const MIN_BLOCK_PX = 8 // блок уже этого не поймать указателем

/** Округление до миллисекунды: сервер хранит времена именно так. */
export function ms(value: number): number {
  return Math.round(value * 1000) / 1000
}

export function clipDuration(clip: Clip): number {
  return clip.out - clip.in
}

export function totalDuration(clips: Clip[]): number {
  return ms(clips.reduce((sum, c) => sum + clipDuration(c), 0))
}

/** Время начала клипа с этим номером на шкале. Номер за пределами списка даёт конец шкалы. */
export function timelineStart(clips: Clip[], index: number): number {
  return ms(clips.slice(0, index).reduce((sum, c) => sum + clipDuration(c), 0))
}

/** Клип под курсором шкалы: номер, сам клип и смещение внутри него. */
export function clipAt(clips: Clip[], time: number): { index: number; clip: Clip; offset: number } | null {
  if (time < 0) return null
  let start = 0
  for (let index = 0; index < clips.length; index++) {
    const duration = clipDuration(clips[index])
    if (time < start + duration) return { index, clip: clips[index], offset: ms(time - start) }
    start += duration
  }
  return null
}

/** Время шкалы → ассет и время внутри исходника. */
export function sourceTime(clips: Clip[], time: number): { index: number; assetId: string; time: number } | null {
  const found = clipAt(clips, time)
  if (found === null) return null
  return { index: found.index, assetId: found.clip.asset_id, time: ms(found.clip.in + found.offset) }
}

/** Свободный id клипа: c1, c2, … плюс случайный хвост, чтобы два быстрых разреза не совпали. */
export function newClipId(clips: Clip[]): string {
  const used = new Set(clips.map(c => c.id))
  for (let n = clips.length + 1; ; n++) {
    const candidate = `c${n}`
    if (!used.has(candidate)) return candidate
  }
}

export function insertClip(clips: Clip[], clip: Clip, at?: number): Clip[] {
  const copy = clips.slice()
  copy.splice(at === undefined ? copy.length : Math.max(0, Math.min(at, copy.length)), 0, clip)
  return copy
}

export function removeClip(clips: Clip[], id: string): Clip[] {
  return clips.filter(c => c.id !== id)
}

export function moveClip(clips: Clip[], from: number, to: number): Clip[] {
  if (from < 0 || from >= clips.length) return clips
  const copy = clips.slice()
  const [moved] = copy.splice(from, 1)
  copy.splice(Math.max(0, Math.min(to, copy.length)), 0, moved)
  return copy
}

/**
 * Режет клип под курсором шкалы на два. Слишком близко к краю не режем: получился бы огрызок
 * короче минимума, который сервер всё равно отвергнет.
 */
export function splitAt(clips: Clip[], time: number): Clip[] {
  const found = clipAt(clips, time)
  if (found === null) return clips
  const cut = ms(found.clip.in + found.offset)
  if (cut - found.clip.in < MIN_CLIP || found.clip.out - cut < MIN_CLIP) return clips
  const left: Clip = { ...found.clip, out: cut, out_verified: false }
  const right: Clip = { ...found.clip, id: newClipId(clips), in: cut, in_verified: false }
  const copy = clips.slice()
  copy.splice(found.index, 1, left, right)
  return copy
}

/**
 * Двигает границу клипа. Границы держатся в пределах исходника и не сходятся ближе минимума;
 * тронутая граница теряет подтверждение — её снова проверит сервер при сохранении.
 */
export function trimClip(
  clips: Clip[],
  id: string,
  edges: { in?: number; out?: number },
  limits: { duration?: number } = {},
): Clip[] {
  return clips.map(clip => {
    if (clip.id !== id) return clip
    const next = { ...clip }
    if (edges.in !== undefined) {
      next.in = ms(Math.max(0, Math.min(edges.in, clip.out - MIN_CLIP)))
      next.in_verified = false
    }
    if (edges.out !== undefined) {
      const top = limits.duration ?? Number.POSITIVE_INFINITY
      next.out = ms(Math.min(top, Math.max(edges.out, next.in + MIN_CLIP)))
      next.out_verified = false
    }
    return next
  })
}

export type Block = { id: string; left: number; width: number; start: number; duration: number }

/** Раскладка блоков в пикселях при заданном масштабе. */
export function layout(clips: Clip[], pxPerSec: number): Block[] {
  let start = 0
  return clips.map(clip => {
    const duration = clipDuration(clip)
    const block: Block = {
      id: clip.id,
      left: ms(start * pxPerSec),
      width: Math.max(MIN_BLOCK_PX, ms(duration * pxPerSec)),
      start: ms(start),
      duration: ms(duration),
    }
    start += duration
    return block
  })
}
```

- [ ] **Step 4: Прогон**

Run: `cd web && npm test && npm run build`
Expected: зелено.

- [ ] **Step 5: Commit**

```bash
git add web/src/timeline
git commit -m "feat(web): pure timeline model"
```

---

### Task 2: Клиент проекта и автосохранение

**Files:**
- Create: `web/src/project.ts`, `web/src/project.test.ts`

- [ ] **Step 1: Тесты**

Создать `web/src/project.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { createSaver, type Project } from './project'

function project(version = 1, clips: unknown[] = []): Project {
  return {
    id: 'prj_1',
    name: 'Мой',
    version,
    status: 'draft',
    created_at: 'x',
    updated_at: 'x',
    finished_at: null,
    doc: { output: { aspect: '16:9', fit: 'pad', fps: 30 }, clips, music: null, subtitles: null } as never,
  }
}

const tick = () => new Promise(resolve => setTimeout(resolve, 0))

describe('автосохранение', () => {
  it('ждёт тишины и шлёт одно сохранение вместо трёх', async () => {
    vi.useFakeTimers()
    const request = vi.fn(async () => project(2))
    const saver = createSaver({ request, delay: 500 })
    saver.schedule(project(1, [{ id: 'a' }]))
    saver.schedule(project(1, [{ id: 'b' }]))
    saver.schedule(project(1, [{ id: 'c' }]))
    expect(request).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(500)
    expect(request).toHaveBeenCalledTimes(1)
    expect(request.mock.calls[0][0].doc.clips).toEqual([{ id: 'c' }])
    vi.useRealTimers()
  })

  it('сохраняет по требованию сразу, без ожидания', async () => {
    const request = vi.fn(async () => project(2))
    const saver = createSaver({ request, delay: 500 })
    await saver.flush(project(1))
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('отдаёт наверх нормализованный ответ сервера', async () => {
    const saved = project(7, [{ id: 'server' }])
    const onSaved = vi.fn()
    const saver = createSaver({ request: async () => saved, delay: 0, onSaved })
    await saver.flush(project(1))
    expect(onSaved).toHaveBeenCalledWith(saved)
  })

  it('не шлёт два запроса одновременно: следующая правка ждёт ответа', async () => {
    let release: (p: Project) => void = () => {}
    const inFlight = new Promise<Project>(resolve => (release = resolve))
    const request = vi.fn(() => inFlight)
    const saver = createSaver({ request, delay: 0 })
    const first = saver.flush(project(1))
    const second = saver.flush(project(1))
    expect(request).toHaveBeenCalledTimes(1)
    release(project(2))
    await first
    await second
    await tick()
    expect(request).toHaveBeenCalledTimes(2)
    expect(request.mock.calls[1][0].version).toBe(2) // вторая правка ушла уже с новой версией
  })

  it('сообщает о конфликте версий и не теряет ответ сервера', async () => {
    const fresh = project(9, [{ id: 'чужой' }])
    const onConflict = vi.fn()
    const request = async () => {
      throw new ApiError(409, 'version_conflict', 'устарело', { project: fresh })
    }
    const saver = createSaver({ request, delay: 0, onConflict })
    await saver.flush(project(1))
    expect(onConflict).toHaveBeenCalledWith(fresh)
  })

  it('сообщает об ошибке проверки списком полей', async () => {
    const onInvalid = vi.fn()
    const request = async () => {
      throw new ApiError(422, 'invalid_project', 'плохо', { errors: [{ field: 'clips[0].out', message: 'коротко' }] })
    }
    const saver = createSaver({ request, delay: 0, onInvalid })
    await saver.flush(project(1))
    expect(onInvalid).toHaveBeenCalledWith([{ field: 'clips[0].out', message: 'коротко' }])
  })

  it('прочие ошибки отдаёт как есть', async () => {
    const onError = vi.fn()
    const request = async () => {
      throw new ApiError(500, 'internal_error', 'ой')
    }
    const saver = createSaver({ request, delay: 0, onError })
    await saver.flush(project(1))
    expect(onError).toHaveBeenCalled()
  })

  it('после конфликта не пытается досохранить старое', async () => {
    const fresh = project(9)
    const request = vi.fn(async () => {
      throw new ApiError(409, 'version_conflict', 'устарело', { project: fresh })
    })
    const saver = createSaver({ request, delay: 0, onConflict: () => {} })
    await saver.flush(project(1))
    await saver.flush(project(1))
    expect(request).toHaveBeenCalledTimes(2) // каждая попытка честная, накопленной очереди нет
    expect(saver.pending()).toBe(false)
  })

  it('знает, есть ли несохранённые правки', async () => {
    vi.useFakeTimers()
    const saver = createSaver({ request: async () => project(2), delay: 500 })
    expect(saver.pending()).toBe(false)
    saver.schedule(project(1))
    expect(saver.pending()).toBe(true)
    await vi.advanceTimersByTimeAsync(500)
    expect(saver.pending()).toBe(false)
    vi.useRealTimers()
  })
})
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `cd web && npm test`
Expected: FAIL, нет модуля `./project`.

- [ ] **Step 3: Реализация**

Создать `web/src/project.ts`:

```ts
import { api, ApiError } from './api'
import type { Clip } from './timeline/model'

export type Output = { aspect: '16:9' | '9:16' | '1:1'; fit: 'pad' | 'crop'; fps: number }
export type Music = { asset_id: string; volume: number; fade_in: number; fade_out: number; loop: boolean }
export type Subtitles = { source: 'file' | 'transcript'; asset_id: string; mode: 'burn' | 'soft'; style: string }
export type ProjectDoc = { output: Output; clips: Clip[]; music: Music | null; subtitles: Subtitles | null }

export type Project = {
  id: string
  name: string
  version: number
  status: 'draft' | 'finished'
  created_at: string
  updated_at: string
  finished_at: string | null
  doc: ProjectDoc
}

export type ProjectCard = Omit<Project, 'doc'> & { clips_count: number; duration: number }
export type FieldError = { field: string; message: string }

export const SAVE_DELAY_MS = 500

export function loadProject(id: string): Promise<Project> {
  return api<Project>(`/api/v1/projects/${encodeURIComponent(id)}`)
}

export function listProjects(): Promise<{ projects: ProjectCard[] }> {
  return api<{ projects: ProjectCard[] }>('/api/v1/projects')
}

export function createProject(name: string): Promise<Project> {
  return api<Project>('/api/v1/projects', { method: 'POST', body: JSON.stringify({ name }) })
}

export function saveRequest(project: Project): Promise<Project> {
  return api<Project>(`/api/v1/projects/${encodeURIComponent(project.id)}`, {
    method: 'PUT',
    body: JSON.stringify({ name: project.name, version: project.version, doc: project.doc }),
  })
}

type SaverOptions = {
  request?: (project: Project) => Promise<Project>
  delay?: number
  onSaved?: (project: Project) => void
  onConflict?: (fresh: Project) => void
  onInvalid?: (errors: FieldError[]) => void
  onError?: (error: unknown) => void
  onStateChange?: (state: 'idle' | 'pending' | 'saving') => void
}

function conflictProject(error: ApiError): Project | null {
  const details = error.details as { project?: Project } | null
  return details?.project ?? null
}

function invalidErrors(error: ApiError): FieldError[] {
  const details = error.details as { errors?: FieldError[] } | null
  return details?.errors ?? []
}

/**
 * Автосохранение: правка ждёт тишины, запрос идёт один за раз.
 *
 * Пока запрос в полёте, новая правка не отправляется, а ждёт ответа: иначе второй запрос ушёл бы
 * со старой версией и получил бы конфликт, которого на самом деле нет. Ответ сервера всегда
 * возвращается наверх — он нормализованный, с подтянутыми резами и новой версией.
 */
export function createSaver(options: SaverOptions = {}) {
  const request = options.request ?? saveRequest
  const delay = options.delay ?? SAVE_DELAY_MS
  let timer: number | undefined
  let queued: Project | null = null
  let saving = false

  const notify = (state: 'idle' | 'pending' | 'saving') => options.onStateChange?.(state)

  async function run(project: Project): Promise<void> {
    saving = true
    notify('saving')
    try {
      const saved = await request(project)
      options.onSaved?.(saved)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const fresh = conflictProject(error)
        // Правку поверх чужой не склеиваем: копим только последнее состояние, а его уже нет смысла слать.
        queued = null
        if (fresh) options.onConflict?.(fresh)
        else options.onError?.(error)
      } else if (error instanceof ApiError && error.status === 422) {
        queued = null
        options.onInvalid?.(invalidErrors(error))
      } else {
        options.onError?.(error)
      }
    } finally {
      saving = false
      const next = queued
      queued = null
      if (next) await run(next)
      else notify('idle')
    }
  }

  return {
    /** Отложить сохранение: последняя правка выигрывает. */
    schedule(project: Project): void {
      queued = project
      notify('pending')
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        const next = queued
        queued = null
        if (next && !saving) void run(next)
      }, delay)
    },
    /** Сохранить немедленно (уход со страницы, кнопка «сохранить»). */
    async flush(project: Project): Promise<void> {
      window.clearTimeout(timer)
      if (saving) {
        queued = project
        return
      }
      queued = null
      await run(project)
    },
    pending(): boolean {
      return queued !== null || saving
    },
    cancel(): void {
      window.clearTimeout(timer)
      queued = null
    },
  }
}
```

- [ ] **Step 4: Прогон**

Run: `cd web && npm test && npm run build`
Expected: зелено. Тест про очередь ждёт, что вторая правка уйдёт после ответа на первую: если реализация ведёт себя иначе, разобраться, кто прав, и записать в «Поправки».

- [ ] **Step 5: Commit**

```bash
git add web/src/project.ts web/src/project.test.ts
git commit -m "feat(web): project client with debounced single-flight autosave"
```

---
### Task 3: Волна и кадры

**Files:**
- Create: `web/src/strip.ts`, `web/src/strip.test.ts`

- [ ] **Step 1: Тесты**

Создать `web/src/strip.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { barsFor, sliceThumbs, thumbBackground, waveBars } from './strip'

const meta = { count: 6, cols: 3, rows: 2, interval: 2, width: 160, height: 90 }

describe('волна', () => {
  it('сжимает пики до нужного числа столбиков, беря максимум окна', () => {
    const peaks = [0, 10, 200, 5, 0, 0, 100, 100]
    expect(waveBars(peaks, 4)).toEqual([10, 200, 0, 100])
  })

  it('растягивает короткий ряд без выхода за границы', () => {
    expect(waveBars([100, 200], 4)).toEqual([100, 100, 200, 200])
    expect(waveBars([], 3)).toEqual([0, 0, 0])
    expect(waveBars([50], 0)).toEqual([])
  })

  it('берёт участок пиков под отрезок исходника', () => {
    const peaks = Array.from({ length: 500 }, (_, i) => i % 256)
    const bars = barsFor({ peaks, rate: 50 }, { from: 2, to: 4 }, 10)
    expect(bars).toHaveLength(10)
    expect(Math.max(...bars)).toBeLessThanOrEqual(255)
  })

  it('участок за пределами записи даёт нули, а не ошибку', () => {
    expect(barsFor({ peaks: [1, 2, 3], rate: 50 }, { from: 100, to: 101 }, 3)).toEqual([0, 0, 0])
    expect(barsFor(null, { from: 0, to: 1 }, 3)).toEqual([0, 0, 0])
  })
})

describe('кадры из спрайта', () => {
  it('считает фон для кадра по времени', () => {
    expect(thumbBackground(meta, 0)).toEqual({ x: 0, y: 0, width: 480, height: 180 })
    expect(thumbBackground(meta, 3)).toEqual({ x: -160, y: 0, width: 480, height: 180 })
    expect(thumbBackground(meta, 5)).toEqual({ x: -320, y: 0, width: 480, height: 180 })
    expect(thumbBackground(meta, 7)).toEqual({ x: 0, y: -90, width: 480, height: 180 })
    expect(thumbBackground(meta, 1000)).toEqual({ x: -320, y: -90, width: 480, height: 180 })
  })

  it('раскладывает кадры по ширине блока', () => {
    const frames = sliceThumbs(meta, { from: 0, to: 6 }, 320)
    expect(frames).toHaveLength(2) // 320 px при кадре 160 px
    expect(frames[0].left).toBe(0)
    expect(frames[1].left).toBe(160)
    expect(frames[0].background.x).toBe(0)
  })

  it('узкий блок получает хотя бы один кадр', () => {
    expect(sliceThumbs(meta, { from: 0, to: 1 }, 20)).toHaveLength(1)
    expect(sliceThumbs(meta, { from: 0, to: 1 }, 0)).toEqual([])
  })

  it('без раскладки кадров ничего не рисует', () => {
    expect(sliceThumbs(null, { from: 0, to: 5 }, 300)).toEqual([])
  })
})

describe('загрузка данных ассета', () => {
  it('читает пики и раскладку один раз на ассет', async () => {
    const fetcher = vi.fn(async (url: string) =>
      url.endsWith('peaks.json') ? { rate: 50, peaks: [1, 2] } : meta,
    )
    const { assetData } = await import('./strip')
    const cache = new Map()
    const first = await assetData('ast_1', { peaks: '/p/peaks.json', thumbs_meta: '/p/thumbs.json' }, cache, fetcher)
    const second = await assetData('ast_1', { peaks: '/p/peaks.json', thumbs_meta: '/p/thumbs.json' }, cache, fetcher)
    expect(first).toBe(second)
    expect(fetcher).toHaveBeenCalledTimes(2) // пики и раскладка, но только по одному разу
  })

  it('переживает недоступные файлы', async () => {
    const { assetData } = await import('./strip')
    const data = await assetData('ast_2', { peaks: '/нет', thumbs_meta: '/нет' }, new Map(), async () => {
      throw new Error('404')
    })
    expect(data.peaks).toBeNull()
    expect(data.thumbs).toBeNull()
  })
})
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `cd web && npm test`
Expected: FAIL, нет модуля `./strip`.

- [ ] **Step 3: Реализация**

Создать `web/src/strip.ts`:

```ts
/**
 * Данные для отрисовки блока клипа: звуковая волна из пиков и кадры из спрайта.
 *
 * Оба файла посчитал воркер: `peaks.json` (50 значений в секунду, 0..255) и `thumbs.json`
 * (раскладка спрайта `thumbs.jpg`). Здесь только арифметика: что показать на отрезке исходника.
 */

export type Peaks = { rate: number; peaks: number[] }
export type ThumbsMeta = { count: number; cols: number; rows: number; interval: number; width: number; height: number }
export type Range = { from: number; to: number }
export type AssetData = { peaks: Peaks | null; thumbs: ThumbsMeta | null }

/** Сжать ряд пиков до нужного числа столбиков: в каждом столбике максимум своего окна. */
export function waveBars(peaks: number[], count: number): number[] {
  if (count <= 0) return []
  if (peaks.length === 0) return new Array(count).fill(0)
  const out: number[] = []
  for (let i = 0; i < count; i++) {
    const from = Math.floor((i * peaks.length) / count)
    const to = Math.max(from + 1, Math.floor(((i + 1) * peaks.length) / count))
    let max = 0
    for (let j = from; j < to && j < peaks.length; j++) max = Math.max(max, peaks[j])
    out.push(max)
  }
  return out
}

/** Столбики волны для отрезка исходника. Нет пиков — ровная линия, а не ошибка. */
export function barsFor(data: Peaks | null, range: Range, count: number): number[] {
  if (count <= 0) return []
  if (!data || !data.peaks.length) return new Array(count).fill(0)
  const from = Math.max(0, Math.round(range.from * data.rate))
  const to = Math.min(data.peaks.length, Math.round(range.to * data.rate))
  if (to <= from) return new Array(count).fill(0)
  return waveBars(data.peaks.slice(from, to), count)
}

/** Смещение фона спрайта для кадра, ближайшего к моменту времени. */
export function thumbBackground(
  meta: ThumbsMeta,
  seconds: number,
): { x: number; y: number; width: number; height: number } {
  const raw = Math.floor(Math.max(0, seconds) / meta.interval)
  const index = Math.min(meta.count - 1, Math.max(0, raw))
  const col = index % meta.cols
  const row = Math.floor(index / meta.cols)
  return {
    x: col ? -col * meta.width : 0,
    y: row ? -row * meta.height : 0,
    width: meta.cols * meta.width,
    height: meta.rows * meta.height,
  }
}

export type Frame = { left: number; background: ReturnType<typeof thumbBackground> }

/** Кадры, которые влезают в блок шириной width: по одному на каждые meta.width пикселей. */
export function sliceThumbs(meta: ThumbsMeta | null, range: Range, width: number): Frame[] {
  if (!meta || width <= 0) return []
  const count = Math.max(1, Math.floor(width / meta.width))
  const span = Math.max(0, range.to - range.from)
  const frames: Frame[] = []
  for (let i = 0; i < count; i++) {
    const at = range.from + (span * i) / count
    frames.push({ left: i * meta.width, background: thumbBackground(meta, at) })
  }
  return frames
}

type Fetcher = (url: string) => Promise<unknown>

const defaultFetcher: Fetcher = async (url: string) => {
  const response = await fetch(url, { credentials: 'same-origin' })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json()
}

/**
 * Пики и раскладка кадров ассета, по одному запросу на файл за всё время жизни страницы.
 * Недоступный файл (ещё не посчитан, истёк по сроку) даёт null: блок нарисуется без волны или кадров.
 */
export async function assetData(
  assetId: string,
  links: { peaks: string | null; thumbs_meta: string | null },
  cache: Map<string, Promise<AssetData>>,
  fetcher: Fetcher = defaultFetcher,
): Promise<AssetData> {
  const existing = cache.get(assetId)
  if (existing) return existing
  const loading = (async (): Promise<AssetData> => {
    const load = async <T>(url: string | null): Promise<T | null> => {
      if (!url) return null
      try {
        return (await fetcher(url)) as T
      } catch {
        return null
      }
    }
    const [peaks, thumbs] = await Promise.all([
      load<Peaks>(links.peaks),
      load<ThumbsMeta>(links.thumbs_meta),
    ])
    return { peaks, thumbs }
  })()
  cache.set(assetId, loading)
  return loading
}
```

- [ ] **Step 4: Прогон**

Run: `cd web && npm test && npm run build`
Expected: зелено.

- [ ] **Step 5: Commit**

```bash
git add web/src/strip.ts web/src/strip.test.ts
git commit -m "feat(web): waveform bars and sprite frames for timeline blocks"
```

---

### Task 4: Состояние воспроизведения склейки

**Files:**
- Create: `web/src/playback.ts`, `web/src/playback.test.ts`

- [ ] **Step 1: Тесты**

Создать `web/src/playback.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import type { Clip } from './timeline/model'
import { aspectRatio, musicVolume, nextClip, seekPlan, stepPlan } from './playback'

function clip(id: string, inS: number, outS: number, asset = 'ast_1'): Clip {
  return { id, asset_id: asset, in: inS, out: outS, snap_to_pauses: false, in_verified: false, out_verified: false }
}

const clips = [clip('c1', 0, 4), clip('c2', 10, 12, 'ast_2'), clip('c3', 1, 4.5)]

describe('переходы между клипами', () => {
  it('знает следующий клип и его точку входа', () => {
    expect(nextClip(clips, 0)).toEqual({ index: 1, assetId: 'ast_2', at: 10 })
    expect(nextClip(clips, 2)).toBeNull()
    expect(nextClip([], 0)).toBeNull()
  })

  it('считает, куда перемотать при переходе на время шкалы', () => {
    expect(seekPlan(clips, 0)).toEqual({ index: 0, assetId: 'ast_1', time: 0, timelineTime: 0 })
    expect(seekPlan(clips, 4.5)).toEqual({ index: 1, assetId: 'ast_2', time: 10.5, timelineTime: 4.5 })
    expect(seekPlan(clips, 99)).toBeNull()
    expect(seekPlan([], 0)).toBeNull()
  })

  it('на шаге внутри клипа просто обновляет время шкалы', () => {
    const plan = stepPlan(clips, { index: 0, sourceTime: 2.5 })
    expect(plan).toEqual({ kind: 'playing', timelineTime: 2.5 })
  })

  it('на достижении точки выхода переключает клип', () => {
    expect(stepPlan(clips, { index: 0, sourceTime: 4 })).toEqual({
      kind: 'advance',
      index: 1,
      assetId: 'ast_2',
      time: 10,
      timelineTime: 4,
    })
    expect(stepPlan(clips, { index: 0, sourceTime: 4.2 })).toMatchObject({ kind: 'advance', index: 1 })
  })

  it('после последнего клипа останавливается', () => {
    expect(stepPlan(clips, { index: 2, sourceTime: 4.5 })).toEqual({ kind: 'end', timelineTime: 9.5 })
  })

  it('исчезнувший клип не роняет плеер', () => {
    expect(stepPlan(clips, { index: 9, sourceTime: 1 })).toEqual({ kind: 'end', timelineTime: 9.5 })
  })
})

describe('музыка', () => {
  it('затухает на входе и на выходе', () => {
    const music = { volume: 0.8, fade_in: 2, fade_out: 2 }
    expect(musicVolume(music, 0, 10)).toBeCloseTo(0)
    expect(musicVolume(music, 1, 10)).toBeCloseTo(0.4)
    expect(musicVolume(music, 5, 10)).toBeCloseTo(0.8)
    expect(musicVolume(music, 9, 10)).toBeCloseTo(0.4)
    expect(musicVolume(music, 10, 10)).toBeCloseTo(0)
  })

  it('без затуханий держит громкость ровно', () => {
    expect(musicVolume({ volume: 0.5, fade_in: 0, fade_out: 0 }, 0, 10)).toBe(0.5)
    expect(musicVolume(null, 1, 10)).toBe(0)
  })

  it('короткий ролик не даёт затуханиям наложиться', () => {
    const music = { volume: 1, fade_in: 5, fade_out: 5 }
    const middle = musicVolume(music, 1, 2)
    expect(middle).toBeGreaterThan(0)
    expect(middle).toBeLessThanOrEqual(1)
  })
})

describe('кадр вывода', () => {
  it('переводит пропорцию в число и режим в свойство', () => {
    expect(aspectRatio('16:9')).toBeCloseTo(16 / 9)
    expect(aspectRatio('9:16')).toBeCloseTo(9 / 16)
    expect(aspectRatio('1:1')).toBe(1)
    expect(aspectRatio('что-то')).toBeCloseTo(16 / 9)
  })
})
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `cd web && npm test`
Expected: FAIL.

- [ ] **Step 3: Реализация**

Создать `web/src/playback.ts`:

```ts
/**
 * Состояние воспроизведения склейки: какой клип играет, когда переключаться, какая громкость музыки.
 *
 * Здесь нет DOM: функции решают, что делать, а драйвер в редакторе двигает элементы video и audio.
 * Так логика шва проверяется тестами, а не глазами.
 */
import { clipAt, clipDuration, ms, timelineStart, totalDuration, type Clip } from './timeline/model'

export type SeekPlan = { index: number; assetId: string; time: number; timelineTime: number }
export type StepPlan =
  | { kind: 'playing'; timelineTime: number }
  | { kind: 'advance'; index: number; assetId: string; time: number; timelineTime: number }
  | { kind: 'end'; timelineTime: number }

/** Следующий клип и его точка входа: скрытый элемент video готовит его заранее. */
export function nextClip(clips: Clip[], index: number): { index: number; assetId: string; at: number } | null {
  const next = clips[index + 1]
  if (!next) return null
  return { index: index + 1, assetId: next.asset_id, at: next.in }
}

/** Куда встать при перемотке на время шкалы. */
export function seekPlan(clips: Clip[], timelineTime: number): SeekPlan | null {
  const found = clipAt(clips, timelineTime)
  if (found === null) return null
  return {
    index: found.index,
    assetId: found.clip.asset_id,
    time: ms(found.clip.in + found.offset),
    timelineTime: ms(timelineTime),
  }
}

/**
 * Что делать на очередном тике: играем дальше, переключаемся на следующий клип или закончили.
 * Сравнение с точкой выхода нестрогое: элемент video редко попадает в неё точно.
 */
export function stepPlan(clips: Clip[], at: { index: number; sourceTime: number }): StepPlan {
  const current = clips[at.index]
  if (!current) return { kind: 'end', timelineTime: totalDuration(clips) }
  const played = Math.min(clipDuration(current), Math.max(0, at.sourceTime - current.in))
  const timelineTime = ms(timelineStart(clips, at.index) + played)
  if (at.sourceTime < current.out) return { kind: 'playing', timelineTime }
  const next = nextClip(clips, at.index)
  if (next === null) return { kind: 'end', timelineTime: totalDuration(clips) }
  return { kind: 'advance', index: next.index, assetId: next.assetId, time: next.at, timelineTime }
}

/** Громкость музыки в момент ролика с учётом затуханий. Затухания не перекрывают друг друга. */
export function musicVolume(
  music: { volume: number; fade_in: number; fade_out: number } | null,
  timelineTime: number,
  total: number,
): number {
  if (!music) return 0
  const half = total / 2
  const fadeIn = Math.min(music.fade_in, half)
  const fadeOut = Math.min(music.fade_out, half)
  let gain = music.volume
  if (fadeIn > 0 && timelineTime < fadeIn) gain *= timelineTime / fadeIn
  const fromEnd = total - timelineTime
  if (fadeOut > 0 && fromEnd < fadeOut) gain *= Math.max(0, fromEnd) / fadeOut
  return Math.max(0, Math.min(1, gain))
}

const ASPECTS: Record<string, number> = { '16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1 }

/** Пропорция кадра вывода числом; неизвестное значение считаем широким. */
export function aspectRatio(aspect: string): number {
  return ASPECTS[aspect] ?? ASPECTS['16:9']
}
```

- [ ] **Step 4: Прогон**

Run: `cd web && npm test && npm run build`
Expected: зелено.

- [ ] **Step 5: Commit**

```bash
git add web/src/playback.ts web/src/playback.test.ts
git commit -m "feat(web): playback state for seam switching and music fades"
```

---
### Task 5: Шкала в браузере

**Files:**
- Create: `web/src/timeline/view.ts`
- Modify: `web/src/style.css`

Модуль работает с DOM, поэтому тестами не покрывается: вся арифметика уже проверена в `model.ts` и `strip.ts`, а поведение проверяется живьём (Task 8). Задача — не изобретать логику заново, а только звать готовые функции.

- [ ] **Step 1: Шкала**

Создать `web/src/timeline/view.ts`:

```ts
/**
 * Шкала монтажа: блоки клипов с кадрами и волной, перетаскивание, подрезка ручками, курсор.
 *
 * Модуль только рисует и ловит указатель. Любая правка уходит наверх через onChange уже готовым
 * списком клипов: считает её модель (model.ts), а не эта обвязка.
 */
import { escapeHtml } from '../html'
import { barsFor, sliceThumbs, type AssetData } from '../strip'
import { clipAt, layout, moveClip, ms, totalDuration, trimClip, type Clip } from './model'

export type AssetInfo = { duration: number | null; files: { thumbs: string | null } }

export type TimelineHandlers = {
  onChange: (clips: Clip[]) => void
  onSeek: (time: number) => void
  onSelect: (id: string | null) => void
}

export type RenderInput = {
  clips: Clip[]
  assets: Map<string, AssetInfo>
  data: Map<string, AssetData>
  pxPerSec: number
}

const TRACK_HEIGHT = 72
const WAVE_HEIGHT = 22
const HANDLE_PX = 8

function waveCanvas(bars: number[], width: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(width))
  canvas.height = WAVE_HEIGHT
  canvas.className = 'wave'
  const ctx = canvas.getContext('2d')
  if (ctx) {
    ctx.fillStyle = 'rgba(255,255,255,.55)'
    bars.forEach((value, x) => {
      const height = Math.max(1, (value / 255) * WAVE_HEIGHT)
      ctx.fillRect(x, WAVE_HEIGHT - height, 1, height)
    })
  }
  return canvas
}

/** Шкала: возвращает управление для редактора. */
export function mountTimeline(el: HTMLElement, handlers: TimelineHandlers) {
  el.innerHTML = `
    <div class="timeline">
      <div class="ruler" id="tl-ruler"></div>
      <div class="track" id="tl-track"><div class="playhead" id="tl-playhead"></div></div>
      <div class="tl-hint muted" id="tl-hint"></div>
    </div>`
  const ruler = el.querySelector('#tl-ruler') as HTMLElement
  const track = el.querySelector('#tl-track') as HTMLElement
  const playhead = el.querySelector('#tl-playhead') as HTMLElement
  const hint = el.querySelector('#tl-hint') as HTMLElement

  let current: RenderInput = { clips: [], assets: new Map(), data: new Map(), pxPerSec: 40 }
  let selected: string | null = null
  let drag: { id: string; index: number; kind: 'move' | 'in' | 'out'; startX: number; clips: Clip[] } | null = null

  const timeAt = (clientX: number): number => {
    const rect = track.getBoundingClientRect();
    return Math.max(0, (clientX - rect.left + track.scrollLeft) / current.pxPerSec)
  }

  function blockHtml(clip: Clip, width: number): string {
    const asset = current.assets.get(clip.asset_id)
    const info = current.data.get(clip.asset_id)
    const frames = sliceThumbs(info?.thumbs ?? null, { from: clip.in, to: clip.out }, width)
    const sprite = asset?.files.thumbs
    const cells = sprite
      ? frames
          .map(f => {
            const bg = f.background
            return `<i class="frame" style="left:${f.left}px;background-image:url('${escapeHtml(sprite)}');
              background-position:${bg.x}px ${bg.y}px;background-size:${bg.width}px ${bg.height}px"></i>`
          })
          .join('')
      : ''
    const marks =
      clip.snap_to_pauses && (!clip.in_verified || !clip.out_verified)
        ? '<span class="unverified" title="Граница не подтверждена паузой">!</span>'
        : ''
    return `${cells}<span class="label">${escapeHtml(clip.id)} · ${(clip.out - clip.in).toFixed(1)} с${marks}</span>
      <b class="handle handle-in"></b><b class="handle handle-out"></b>`
  }

  function render(input?: Partial<RenderInput>): void {
    current = { ...current, ...input }
    const blocks = layout(current.clips, current.pxPerSec)
    const width = Math.max(200, totalDuration(current.clips) * current.pxPerSec)
    track.style.width = `${width}px`
    ruler.style.width = `${width}px`
    ruler.innerHTML = Array.from({ length: Math.ceil(width / (current.pxPerSec * 5)) + 1 }, (_, i) => {
      const seconds = i * 5
      return `<span class="tick" style="left:${seconds * current.pxPerSec}px">${seconds} с</span>`
    }).join('')

    track.querySelectorAll('.block').forEach(node => node.remove())
    blocks.forEach((block, index) => {
      const clip = current.clips[index]
      const node = document.createElement('div')
      node.className = `block${clip.id === selected ? ' selected' : ''}`
      node.style.left = `${block.left}px`
      node.style.width = `${block.width}px`
      node.style.height = `${TRACK_HEIGHT}px`
      node.dataset.id = clip.id
      node.dataset.index = String(index)
      node.innerHTML = blockHtml(clip, block.width)
      const info = current.data.get(clip.asset_id)
      node.appendChild(waveCanvas(barsFor(info?.peaks ?? null, { from: clip.in, to: clip.out }, Math.round(block.width)), block.width))
      track.appendChild(node)
    })
  }

  function finishDrag(clientX: number): void {
    if (!drag) return
    const dx = clientX - drag.startX
    if (drag.kind === 'move') {
      const target = clipAt(current.clips, timeAt(clientX))
      const to = target ? target.index : current.clips.length - 1
      if (to !== drag.index) handlers.onChange(moveClip(drag.clips, drag.index, to))
      else render()
    } else {
      const clip = drag.clips[drag.index]
      const delta = dx / current.pxPerSec
      const duration = current.assets.get(clip.asset_id)?.duration ?? undefined
      const edges = drag.kind === 'in' ? { in: ms(clip.in + delta) } : { out: ms(clip.out + delta) }
      handlers.onChange(trimClip(drag.clips, clip.id, edges, { duration: duration ?? undefined }))
    }
    drag = null
    hint.textContent = ''
  }

  track.addEventListener('pointerdown', event => {
    const target = event.target as HTMLElement
    const node = target.closest('.block') as HTMLElement | null
    if (!node) {
      handlers.onSeek(timeAt(event.clientX))
      return
    }
    const id = node.dataset.id ?? ''
    const index = Number(node.dataset.index ?? 0)
    selected = id
    handlers.onSelect(id)
    const rect = node.getBoundingClientRect()
    const kind: 'move' | 'in' | 'out' = target.classList.contains('handle-in')
      ? 'in'
      : target.classList.contains('handle-out')
        ? 'out'
        : event.clientX - rect.left < HANDLE_PX
          ? 'in'
          : rect.right - event.clientX < HANDLE_PX
            ? 'out'
            : 'move'
    drag = { id, index, kind, startX: event.clientX, clips: current.clips }
    track.setPointerCapture(event.pointerId)
    render()
  })

  track.addEventListener('pointermove', event => {
    if (!drag) return
    const delta = (event.clientX - drag.startX) / current.pxPerSec
    const clip = drag.clips[drag.index]
    if (drag.kind === 'move') hint.textContent = `перенос «${clip.id}»`
    else {
      const value = drag.kind === 'in' ? clip.in + delta : clip.out + delta
      hint.textContent = `${drag.kind === 'in' ? 'начало' : 'конец'}: ${Math.max(0, value).toFixed(2)} с`
    }
  })

  const stop = (event: PointerEvent) => {
    if (drag) finishDrag(event.clientX)
  }
  track.addEventListener('pointerup', stop)
  track.addEventListener('pointercancel', stop)

  return {
    render,
    setPlayhead(time: number): void {
      playhead.style.left = `${time * current.pxPerSec}px`
    },
    setZoom(pxPerSec: number): void {
      render({ pxPerSec: Math.max(4, Math.min(400, pxPerSec)) })
    },
    zoom(): number {
      return current.pxPerSec
    },
    select(id: string | null): void {
      selected = id
      render()
    },
    selected(): string | null {
      return selected
    },
  }
}
```

- [ ] **Step 2: Стили**

Добавить в `web/src/style.css`:

```css
.timeline { border: 1px solid #8884; border-radius: 8px; overflow-x: auto; background: #0002; }
.ruler { position: relative; height: 18px; font-size: 11px; }
.ruler .tick { position: absolute; top: 2px; opacity: .6; border-left: 1px solid #8886; padding-left: 3px; }
.track { position: relative; height: 76px; }
.block { position: absolute; top: 2px; overflow: hidden; border-radius: 5px; background: #2f5d4a;
  border: 1px solid #0006; cursor: grab; user-select: none; touch-action: none; }
.block.selected { outline: 2px solid #7fd1ab; }
.block .frame { position: absolute; top: 0; width: 160px; height: 90px; background-repeat: no-repeat; opacity: .75; }
.block .label { position: absolute; left: 6px; top: 4px; font-size: 11px; color: #fff; text-shadow: 0 1px 2px #000; }
.block .wave { position: absolute; left: 0; bottom: 0; }
.block .handle { position: absolute; top: 0; bottom: 0; width: 8px; background: #7fd1ab88; cursor: ew-resize; }
.block .handle-in { left: 0; } .block .handle-out { right: 0; }
.block .unverified { margin-left: 6px; color: #ffd166; font-weight: bold; }
.playhead { position: absolute; top: 0; bottom: 0; width: 2px; background: #ff6b6b; pointer-events: none; }
.tl-hint { height: 18px; font-size: 12px; padding-left: 6px; }
.editor { display: grid; grid-template-columns: minmax(240px, 1fr) minmax(320px, 2fr); gap: 16px; }
.stage { background: #000; border-radius: 8px; overflow: hidden; display: grid; place-items: center; }
.stage video { max-width: 100%; max-height: 100%; }
.stage.crop video { width: 100%; height: 100%; object-fit: cover; }
.save-state { font-size: 12px; opacity: .75; }
```

- [ ] **Step 3: Прогон**

Run: `cd web && npm test && npm run build`
Expected: зелено (тестов у модуля нет, но `tsc` обязан пройти).

- [ ] **Step 4: Commit**

```bash
git add web/src/timeline/view.ts web/src/style.css
git commit -m "feat(web): timeline view with drag, trim and frames"
```

---

### Task 6: Панель исходника

**Files:**
- Create: `web/src/source.ts`

- [ ] **Step 1: Панель**

Создать `web/src/source.ts`:

```ts
/**
 * Панель исходника: выбор готового файла, плеер прокси, выделение куска и кнопка «в шкалу».
 *
 * Выделение хранится числами, а не в DOM: кнопка отдаёт наверх готовый диапазон, а редактор
 * решает, что с ним делать.
 */
import { escapeHtml } from './html'
import type { Asset } from './assets'
import { fmtDuration } from './assets'

export type SourceHandlers = {
  onAdd: (asset: Asset, range: { from: number; to: number }) => void
}

const READY = new Set(['ready', 'proxy_ready'])

export function mountSource(el: HTMLElement, handlers: SourceHandlers) {
  el.innerHTML = `
    <main class="card">
      <h3>Исходник</h3>
      <select id="src-pick"><option value="">— выберите файл —</option></select>
      <div id="src-player"></div>
      <div class="row">
        <button id="src-mark-in" type="button">Начало</button>
        <button id="src-mark-out" type="button">Конец</button>
        <span id="src-range" class="muted">весь файл</span>
      </div>
      <button id="src-add" type="button" disabled>Добавить в шкалу</button>
      <p class="muted" id="src-note"></p>
    </main>`
  const pick = el.querySelector('#src-pick') as HTMLSelectElement
  const playerBox = el.querySelector('#src-player') as HTMLElement
  const rangeLabel = el.querySelector('#src-range') as HTMLElement
  const addButton = el.querySelector('#src-add') as HTMLButtonElement
  const note = el.querySelector('#src-note') as HTMLElement

  let assets: Asset[] = []
  let current: Asset | null = null
  let from = 0
  let to = 0

  const video = (): HTMLMediaElement | null => playerBox.querySelector('video, audio')

  function refreshRange(): void {
    rangeLabel.textContent = current ? `${fmtDuration(from)} — ${fmtDuration(to)}` : 'весь файл'
    addButton.disabled = !current || to - from < 0.1
  }

  function choose(asset: Asset | null): void {
    current = asset
    from = 0
    to = asset?.duration ?? 0
    playerBox.innerHTML = asset?.files.proxy
      ? `<video class="player" controls preload="metadata" src="${escapeHtml(asset.files.proxy)}"></video>`
      : ''
    note.textContent = asset && !asset.files.proxy ? 'Прокси ещё готовится: выделять можно будет после обработки.' : ''
    refreshRange()
  }

  pick.addEventListener('change', () => {
    choose(assets.find(a => a.id === pick.value) ?? null)
  })

  el.querySelector('#src-mark-in')!.addEventListener('click', () => {
    const player = video()
    if (!player || !current) return
    from = Math.min(player.currentTime, to - 0.1)
    refreshRange()
  })

  el.querySelector('#src-mark-out')!.addEventListener('click', () => {
    const player = video()
    if (!player || !current) return
    to = Math.max(player.currentTime, from + 0.1)
    refreshRange()
  })

  addButton.addEventListener('click', () => {
    if (!current) return
    handlers.onAdd(current, { from, to })
  })

  return {
    /** Список файлов: в шкалу годятся только готовые видео. */
    setAssets(list: Asset[]): void {
      assets = list.filter(a => a.kind === 'video' && READY.has(a.status))
      const keep = current?.id ?? ''
      pick.innerHTML =
        '<option value="">— выберите файл —</option>' +
        assets
          .map(a => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.original_name)}</option>`)
          .join('')
      if (assets.some(a => a.id === keep)) pick.value = keep
      else choose(null)
    },
    current(): Asset | null {
      return current
    },
  }
}
```

- [ ] **Step 2: Стили**

Добавить в `web/src/style.css`:

```css
.row { display: flex; gap: 8px; align-items: center; margin: 8px 0; }
select { padding: 6px 8px; width: 100%; }
```

- [ ] **Step 3: Прогон**

Run: `cd web && npm test && npm run build`
Expected: зелено.

- [ ] **Step 4: Commit**

```bash
git add web/src/source.ts web/src/style.css
git commit -m "feat(web): source panel with range selection"
```

---
### Task 7: Экран редактора, список проектов, роутер

**Files:**
- Create: `web/src/editor.ts`, `web/src/projects.ts`
- Modify: `web/src/main.ts`, `web/src/style.css`

- [ ] **Step 1: Список проектов**

Создать `web/src/projects.ts`:

```ts
/** Список проектов на главной: создать, открыть, завершить, удалить. */
import { api, ApiError } from './api'
import { fmtDuration } from './assets'
import { escapeHtml } from './html'
import { createProject, listProjects, type ProjectCard } from './project'

function row(p: ProjectCard): string {
  const state = p.status === 'finished' ? 'завершён' : 'в работе'
  const open = p.status === 'draft'
    ? `<a class="button" href="#/p/${encodeURIComponent(p.id)}">Открыть</a>`
    : ''
  const finish = p.status === 'draft'
    ? `<button data-finish="${escapeHtml(p.id)}" data-name="${escapeHtml(p.name)}">Завершить</button>`
    : ''
  return `<tr>
    <td>${escapeHtml(p.name)}</td><td>${p.clips_count}</td><td>${fmtDuration(p.duration)}</td>
    <td>${state}</td><td>${open} ${finish}
    <button data-drop="${escapeHtml(p.id)}" data-name="${escapeHtml(p.name)}">Удалить</button></td></tr>`
}

export function mountProjects(el: HTMLElement) {
  el.innerHTML = `
    <main class="card">
      <h2>Проекты</h2>
      <form id="prj-form"><input name="name" placeholder="Название проекта" required maxlength="200" /><button>Создать</button></form>
      <table>
        <thead><tr><th>Название</th><th>Клипов</th><th>Длина</th><th>Статус</th><th></th></tr></thead>
        <tbody id="prj-rows"><tr><td colspan="5">Пока нет</td></tr></tbody>
      </table>
      <pre id="prj-error" hidden></pre>
    </main>`
  const rows = el.querySelector('#prj-rows') as HTMLElement
  const errorBox = el.querySelector('#prj-error') as HTMLPreElement

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  async function refresh(): Promise<void> {
    const { projects } = await listProjects()
    rows.innerHTML = projects.map(row).join('') || '<tr><td colspan="5">Пока нет</td></tr>'
    rows.querySelectorAll<HTMLButtonElement>('button[data-drop]').forEach(b =>
      b.addEventListener('click', async () => {
        if (!window.confirm(`Удалить проект «${b.dataset.name}»? Файлы останутся.`)) return
        try {
          await api(`/api/v1/projects/${encodeURIComponent(b.dataset.drop ?? '')}`, { method: 'DELETE' })
          await refresh()
        } catch (e) {
          showError(e)
        }
      }),
    )
    rows.querySelectorAll<HTMLButtonElement>('button[data-finish]').forEach(b =>
      b.addEventListener('click', async () => {
        const ok = window.confirm(
          `Завершить проект «${b.dataset.name}»? Его файлы удалятся, если не заняты в других проектах.`,
        )
        if (!ok) return
        try {
          await api(`/api/v1/projects/${encodeURIComponent(b.dataset.finish ?? '')}/finish`, { method: 'POST' })
          await refresh()
        } catch (e) {
          showError(e)
        }
      }),
    )
  }

  const form = el.querySelector('#prj-form') as HTMLFormElement
  form.addEventListener('submit', async event => {
    event.preventDefault()
    const name = String(new FormData(form).get('name') ?? '').trim()
    try {
      const created = await createProject(name)
      location.hash = `#/p/${encodeURIComponent(created.id)}`
    } catch (e) {
      showError(e)
    }
  })

  void refresh().catch(showError)
  return { refresh }
}
```

- [ ] **Step 2: Редактор**

Создать `web/src/editor.ts`:

```ts
/**
 * Экран редактора: панель исходника, шкала, плеер склейки, автосохранение.
 *
 * Состояние — один документ проекта плюс версия. Любая правка идёт через applyClips: он кладёт
 * новый список, перерисовывает и просит сохранить. Ответ сервера заменяет документ целиком:
 * там уже подтянутые резы, флаги подтверждения и новая версия.
 */
import { api, ApiError } from './api'
import type { Asset } from './assets'
import { escapeHtml } from './html'
import { aspectRatio, musicVolume, seekPlan, stepPlan } from './playback'
import { createSaver, loadProject, type FieldError, type Project } from './project'
import { assetData, type AssetData } from './strip'
import { insertClip, ms, newClipId, removeClip, splitAt, totalDuration, type Clip } from './timeline/model'
import { mountSource } from './source'
import { mountTimeline, type AssetInfo } from './timeline/view'

const STATE_TEXT = { idle: 'сохранено', pending: 'правки не сохранены', saving: 'сохраняю…' }

export function mountEditor(el: HTMLElement, projectId: string) {
  el.innerHTML = `
    <header class="bar">
      <a class="button" href="#/">← к файлам</a>
      <strong id="ed-name">Проект</strong>
      <span class="save-state" id="ed-state">загрузка…</span>
      <span id="ed-notice" class="muted"></span>
    </header>
    <div class="editor">
      <section id="ed-source"></section>
      <section>
        <div class="stage" id="ed-stage"></div>
        <div class="row">
          <button id="ed-play" type="button">▶</button>
          <button id="ed-split" type="button">Разрезать</button>
          <button id="ed-delete" type="button">Удалить клип</button>
          <button id="ed-zoom-in" type="button">+</button>
          <button id="ed-zoom-out" type="button">−</button>
          <select id="ed-aspect">
            <option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option>
          </select>
          <span class="muted" id="ed-total"></span>
        </div>
        <div id="ed-timeline"></div>
      </section>
    </div>
    <pre id="ed-error" hidden></pre>`

  const nameBox = el.querySelector('#ed-name') as HTMLElement
  const stateBox = el.querySelector('#ed-state') as HTMLElement
  const noticeBox = el.querySelector('#ed-notice') as HTMLElement
  const stage = el.querySelector('#ed-stage') as HTMLElement
  const totalBox = el.querySelector('#ed-total') as HTMLElement
  const errorBox = el.querySelector('#ed-error') as HTMLPreElement
  const aspectPick = el.querySelector('#ed-aspect') as HTMLSelectElement

  let project: Project | null = null
  let assets = new Map<string, AssetInfo>()
  let assetList: Asset[] = []
  const dataCache = new Map<string, Promise<AssetData>>()
  const data = new Map<string, AssetData>()
  let playing = false
  let playIndex = 0
  let timelineTime = 0
  let stopped = false

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  const notice = (text: string) => {
    noticeBox.textContent = text
    if (text) window.setTimeout(() => (noticeBox.textContent = ''), 6000)
  }

  const saver = createSaver({
    onSaved: saved => {
      if (stopped) return
      project = saved
      render()
    },
    onConflict: fresh => {
      project = fresh
      render()
      notice('Проект изменился в другом месте, показана свежая версия')
    },
    onInvalid: (errors: FieldError[]) => {
      notice(`Не сохранено: ${errors.map(e => `${e.field} — ${e.message}`).join('; ')}`)
    },
    onError: showError,
    onStateChange: state => (stateBox.textContent = STATE_TEXT[state]),
  })

  // Плеер склейки: активный элемент играет, скрытый держит следующий клип на его точке входа.
  const videoA = document.createElement('video')
  const videoB = document.createElement('video')
  const music = document.createElement('audio')
  let active = videoA
  ;[videoA, videoB].forEach(v => {
    v.preload = 'auto'
    v.playsInline = true
    stage.appendChild(v)
  })
  videoB.style.display = 'none'
  music.preload = 'auto'

  const proxyOf = (assetId: string): string | null => assetList.find(a => a.id === assetId)?.files.proxy ?? null

  function swap(): void {
    const hidden = active === videoA ? videoB : videoA
    active.pause()
    active.style.display = 'none'
    hidden.style.display = ''
    active = hidden
  }

  function prepareNext(index: number): void {
    const clips = project?.doc.clips ?? []
    const next = clips[index + 1]
    const hidden = active === videoA ? videoB : videoA
    if (!next) return
    const src = proxyOf(next.asset_id)
    if (!src) return
    if (!hidden.src.endsWith(src)) hidden.src = src
    hidden.currentTime = next.in
  }

  function seek(time: number): void {
    const clips = project?.doc.clips ?? []
    const plan = seekPlan(clips, time)
    if (!plan) return
    const src = proxyOf(plan.assetId)
    if (!src) return
    playIndex = plan.index
    timelineTime = plan.timelineTime
    if (!active.src.endsWith(src)) active.src = src
    active.currentTime = plan.time
    timeline.setPlayhead(timelineTime)
    prepareNext(plan.index)
  }

  active.addEventListener('timeupdate', () => {
    if (!project) return
    const plan = stepPlan(project.doc.clips, { index: playIndex, sourceTime: active.currentTime })
    timelineTime = plan.timelineTime
    timeline.setPlayhead(timelineTime)
    if (plan.kind === 'advance') {
      swap()
      playIndex = plan.index
      active.currentTime = plan.time
      if (playing) void active.play().catch(() => {})
      prepareNext(plan.index)
    } else if (plan.kind === 'end') {
      playing = false
      active.pause()
      music.pause()
    }
    if (project.doc.music) {
      music.volume = musicVolume(project.doc.music, timelineTime, totalDuration(project.doc.clips))
    }
  })
  videoB.addEventListener('timeupdate', () => {}) // второй элемент только буферизует

  const timeline = mountTimeline(el.querySelector('#ed-timeline') as HTMLElement, {
    onChange: applyClips,
    onSeek: seek,
    onSelect: () => {},
  })

  const source = mountSource(el.querySelector('#ed-source') as HTMLElement, {
    onAdd: (asset, range) => {
      const clips = project?.doc.clips ?? []
      const clip: Clip = {
        id: newClipId(clips),
        asset_id: asset.id,
        in: ms(range.from),
        out: ms(range.to),
        snap_to_pauses: false,
        in_verified: false,
        out_verified: false,
      }
      applyClips(insertClip(clips, clip))
    },
  })

  function applyClips(clips: Clip[]): void {
    if (!project) return
    project = { ...project, doc: { ...project.doc, clips } }
    render()
    saver.schedule(project)
  }

  async function ensureData(clips: Clip[]): Promise<void> {
    const ids = new Set(clips.map(c => c.asset_id))
    await Promise.all(
      Array.from(ids).map(async id => {
        if (data.has(id)) return
        const asset = assetList.find(a => a.id === id)
        if (!asset) return
        const files = asset.files as { peaks?: string | null; thumbs_meta?: string | null }
        const loaded = await assetData(id, { peaks: files.peaks ?? null, thumbs_meta: files.thumbs_meta ?? null }, dataCache)
        data.set(id, loaded)
      }),
    )
    if (!stopped) timeline.render({ data })
  }

  function render(): void {
    if (!project) return
    nameBox.textContent = project.name
    aspectPick.value = project.doc.output.aspect
    stage.style.aspectRatio = String(aspectRatio(project.doc.output.aspect))
    stage.classList.toggle('crop', project.doc.output.fit === 'crop')
    totalBox.textContent = `${totalDuration(project.doc.clips).toFixed(1)} с`
    timeline.render({ clips: project.doc.clips, assets, data })
    timeline.setPlayhead(timelineTime)
    void ensureData(project.doc.clips)
  }

  el.querySelector('#ed-play')!.addEventListener('click', () => {
    if (!project || !project.doc.clips.length) return
    playing = !playing
    if (playing) {
      if (!active.src) seek(timelineTime)
      void active.play().catch(showError)
      if (project.doc.music) void music.play().catch(() => {})
    } else {
      active.pause()
      music.pause()
    }
  })

  el.querySelector('#ed-split')!.addEventListener('click', () => {
    if (!project) return
    const next = splitAt(project.doc.clips, timelineTime)
    if (next === project.doc.clips) notice('Здесь резать нечего: курсор на краю клипа')
    else applyClips(next)
  })

  el.querySelector('#ed-delete')!.addEventListener('click', () => {
    const id = timeline.selected()
    if (!project || !id) return notice('Сначала выберите клип на шкале')
    applyClips(removeClip(project.doc.clips, id))
  })

  el.querySelector('#ed-zoom-in')!.addEventListener('click', () => timeline.setZoom(timeline.zoom() * 1.5))
  el.querySelector('#ed-zoom-out')!.addEventListener('click', () => timeline.setZoom(timeline.zoom() / 1.5))

  aspectPick.addEventListener('change', () => {
    if (!project) return
    const aspect = aspectPick.value as '16:9' | '9:16' | '1:1'
    project = { ...project, doc: { ...project.doc, output: { ...project.doc.output, aspect } } }
    render()
    saver.schedule(project)
  })

  async function boot(): Promise<void> {
    const [loaded, list] = await Promise.all([
      loadProject(projectId),
      api<{ assets: Asset[] }>('/api/v1/assets'),
    ])
    if (stopped) return
    project = loaded
    assetList = list.assets
    assets = new Map(list.assets.map(a => [a.id, { duration: a.duration, files: { thumbs: a.files.thumbs } }]))
    source.setAssets(list.assets)
    if (project.doc.music) {
      const musicAsset = list.assets.find(a => a.id === project?.doc.music?.asset_id)
      if (musicAsset?.files.proxy) music.src = musicAsset.files.proxy
    }
    stateBox.textContent = STATE_TEXT.idle
    render()
  }

  void boot().catch(showError)

  return {
    stop(): void {
      stopped = true
      saver.cancel()
      active.pause()
      music.pause()
    },
  }
}
```

- [ ] **Step 3: Роутер**

В `web/src/main.ts`:

- добавить импорты `import { mountEditor } from './editor'` и `import { mountProjects } from './projects'`;
- завести переменную `let editor: { stop: () => void } | null = null`;
- в `renderSettings` после секции `#assets` добавить `<section id="projects"></section>` и вызвать `mountProjects(document.getElementById('projects') as HTMLElement)`;
- заменить `void boot()` на роутер:

```ts
function route(): void {
  editor?.stop()
  editor = null
  assetsPanel?.stop()
  assetsPanel = null
  const match = /^#\/p\/([\w-]+)$/.exec(location.hash)
  if (match) {
    void api<Me>('/api/v1/me')
      .then(() => {
        editor = mountEditor(root, match[1])
      })
      .catch(e => {
        if (e instanceof ApiError && e.status === 401) renderLogin()
        else renderError(e)
      })
    return
  }
  void boot()
}

window.addEventListener('hashchange', route)
route()
```

- [ ] **Step 4: Прогон**

Run: `cd web && npm test && npm run build`
Expected: зелено. Если `tsc` ругается на типы файлов ассета (`peaks`, `thumbs_meta` отсутствуют в типе `Asset`), дополнить тип в `web/src/assets.ts`: `files: { proxy: string | null; thumbs: string | null; thumbs_meta: string | null; peaks: string | null; analysis: string | null; vtt: string | null }`.

- [ ] **Step 5: Commit**

```bash
git add web/src/editor.ts web/src/projects.ts web/src/main.ts web/src/assets.ts web/src/style.css
git commit -m "feat(web): project list, editor screen and hash routing"
```

---

### Task 8: Документация и живая проверка

**Files:**
- Modify: `README.md`
- Живая проверка в браузере (координатор)

- [ ] **Step 1: README**

Добавить после раздела «Проекты (M2a)»:

```markdown
### Редактор в браузере (M2b)

- Экраны разводит хеш: `#/` — файлы, проекты и настройки, `#/p/{id}` — редактор проекта.
- Слева панель исходника: выбрать готовое видео, проиграть прокси, отметить начало и конец, положить кусок на шкалу.
- Шкала: блоки клипов с кадрами из полоски и звуковой волной, перетаскивание меняет порядок, ручки по краям подрезают, кнопка режет клип по курсору, выбранный клип удаляется. Неподтверждённая граница помечена восклицательным знаком.
- Плеер играет склейку целиком: два элемента `video` меняются местами на стыке, следующий клип подгружается заранее. Кадр показывается в пропорции вывода.
- Правки уезжают на сервер через 500 мс тишины, по одному запросу за раз. Ответ сервера заменяет документ: в нём уже подтянутые резы и новая версия. Если проект изменили в другом месте, редактор показывает свежую версию и говорит об этом.
```

- [ ] **Step 2: Прогон и коммит**

Run: `cd web && npm test && npm run build && cd .. && uv run python -m pytest && uv run ruff check .`

```bash
git add README.md
git commit -m "docs: browser editor"
```

- [ ] **Step 3: Слияние и выкатка** (координатор)

`git checkout main && git merge --ff-only m2b-timeline && git push origin main m2b-timeline`, затем на VM `sudo bash /opt/editing-site/deploy/deploy.sh`.

- [ ] **Step 4: Живая проверка в браузере** (координатор)

1. Открыть сайт, создать проект, перейти в редактор по ссылке.
2. Выбрать обработанный файл, отметить кусок, добавить в шкалу: блок появился с кадрами и волной.
3. Добавить второй кусок, перетащить блоки местами, подрезать край ручкой.
4. Нажать «Разрезать» на середине клипа: получились два блока.
5. Нажать воспроизведение: склейка играет, на стыке нет чёрного кадра, курсор идёт по шкале.
6. Подождать полсекунды после правки: статус меняется на «сохранено».
7. Открыть тот же проект во второй вкладке, сохранить правку там, затем править в первой: редактор показывает свежую версию и уведомление.
8. Поставить клипу подтяжку к паузам через API и убедиться, что после сохранения время сдвинулось, а знак неподтверждённой границы исчез.

---

## Поправки по ходу выполнения

- **Task 1** (`68c3c37`): тест требовал разные значения от трёх вызовов `newClipId` на одном списке, а версия из плана была детерминированной и возвращала одно и то же. Идентификатор получил случайный хвост, как и было описано в комментарии к функции.
- **Task 2** (`d6c2e85`): в плане очередь автосохранения отправлялась повторно со старой версией и упиралась бы в ложный конфликт: теперь перед повтором подставляется версия из ответа сервера. `window.setTimeout` заменён на глобальный: в тестовом окружении DOM нет. Пара моков в тестах получила типизированный параметр, иначе строгий TypeScript не собирал файл.
- **Task 7** (`18c1173`): слушатель события времени висел на одном элементе `video`, поэтому после первого шва плеер вставал. Теперь он на обоих, с проверкой активного элемента.
- **Финальное ревью ветки** (`8a6df66`): два блокера. Уход с экрана редактора выбрасывал правку, не дожившую до конца задержки в 500 мс, — теперь она дописывается. Провалившееся сохранение показывалось как «сохранено» — появилось отдельное состояние. Плюс: правка списка во время воспроизведения оставляла плеер на удалённом клипе (теперь встаём заново по времени шкалы), а следующий клип без прокси давал пустой кадр вместо честной остановки.
- **Живая проверка в браузере 2026-09-05**: домен с машины разработки не резолвится, поэтому проверял на локальном стенде с настоящими данными (два ролика, обработанных воркером, сессия посеяна прямо в базу). Проверено: создание проекта и переход в редактор, выбор исходника и отметка куска, два блока на шкале с кадрами из спрайта, разрез клипа (один блок стал двумя, длина сохранилась), воспроизведение склейки с настоящей заменой элементов `video` на стыке (проверено сравнением объектов до и после), конфликт версий (правка снаружи дала уведомление и свежую версию в шапке), сохранение правки при уходе с экрана через 0 мс после неё (версия выросла, клип удалён). Стенд после проверки остановлен и удалён.

## Что остаётся на M3

Рендер: сборщик команды ffmpeg из документа проекта, задание `render` в воркере с прогрессом и отменой, таблица рендеров и их выдача, `POST /projects/{id}/render`, скачивание готового файла, удаление рендеров при завершении проекта, живой смоук агентским скриптом целиком.
