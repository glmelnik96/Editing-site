# Вкладки редактора: одна работа слева — план

> **Для агентов:** выполнять задача за задачей. Шаги — чекбоксы.

**Цель:** слева видна одна вкладка; «Субтитры» и «Рендер» серые, пока нет клипа (рендер жив, если уже есть готовый файл).

**Спека:** `docs/superpowers/specs/2026-09-08-editor-tabs-design.md`.

**Архитектура:** чистые функции `tabEnabled` / `resolveTab` в `web/src/editor-tabs.ts`. `showTab` в `editor.ts` прячет все панели, включая исходники. При загрузке редактор спрашивает `listRenders`, чтобы знать, жив ли «Рендер» без клипов. `#ed-source[hidden] { display: none }` — иначе `display: flex` оставляет блок на экране.

**Стек:** vanilla TS, vitest. `cd web && npm test`, `npx tsc --noEmit`. Не коммитить `NUL` и `web/layout-preview.html`.

---

## Файлы

| Файл | Зачем |
|---|---|
| `web/src/editor-tabs.ts` | `tabEnabled`, `resolveTab` |
| `web/src/editor-tabs.test.ts` | живые / серые вкладки, возврат на исходники |
| `web/src/editor.ts` | взаимно исключать панели, `disabled`, `listRenders`, `syncTabs` после клипов |
| `web/src/render.ts` | после списка роликов сообщить число |
| `web/src/style.css` | `#ed-source[hidden]`, `.tab:disabled` |
| `web/src/style-editor-tabs.test.ts` | спрятанный исходник не `display:flex` |
| `README.md` | левая колонка: одна вкладка |

---

### Task 1: Правила вкладок

**Files:**
- Create: `web/src/editor-tabs.ts`
- Test: `web/src/editor-tabs.test.ts`

- [ ] **Step 1: Failing tests**

```ts
import { describe, expect, it } from 'vitest'
import { resolveTab, tabEnabled } from './editor-tabs'

describe('вкладки редактора', () => {
  it('исходники всегда живые', () => {
    expect(tabEnabled('source', 0, false)).toBe(true)
  })

  it('субтитры и рендер серые без клипов и без ролика', () => {
    expect(tabEnabled('subtitles', 0, false)).toBe(false)
    expect(tabEnabled('renders', 0, false)).toBe(false)
  })

  it('первый клип открывает обе', () => {
    expect(tabEnabled('subtitles', 1, false)).toBe(true)
    expect(tabEnabled('renders', 1, false)).toBe(true)
  })

  it('рендер жив без клипов, если есть готовый файл', () => {
    expect(tabEnabled('renders', 0, true)).toBe(true)
    expect(tabEnabled('subtitles', 0, true)).toBe(false)
  })

  it('неживая вкладка сбрасывается на исходники', () => {
    expect(resolveTab('subtitles', 0, false)).toBe('source')
    expect(resolveTab('renders', 0, true)).toBe('renders')
    expect(resolveTab('subtitles', 2, false)).toBe('subtitles')
  })
})
```

- [ ] **Step 2:** `cd web && npm test -- src/editor-tabs.test.ts` — FAIL (модуля нет)

- [ ] **Step 3: Implementation**

```ts
export type EditorTab = 'source' | 'subtitles' | 'renders'

export function tabEnabled(tab: EditorTab, clipCount: number, hasReadyRender: boolean): boolean {
  if (tab === 'source') return true
  if (tab === 'subtitles') return clipCount > 0
  return clipCount > 0 || hasReadyRender
}

export function resolveTab(tab: EditorTab, clipCount: number, hasReadyRender: boolean): EditorTab {
  return tabEnabled(tab, clipCount, hasReadyRender) ? tab : 'source'
}
```

- [ ] **Step 4:** тесты зелёные

- [ ] **Step 5:** commit `feat: правила живых вкладок редактора`

---

### Task 2: CSS

**Files:**
- Modify: `web/src/style.css`
- Test: `web/src/style-editor-tabs.test.ts`

- [ ] **Step 1: Failing test** — в CSS есть `#ed-source[hidden]` с `display: none`; комментарий про «исходники остаются» уходит.

- [ ] **Step 2:** FAIL (правила нет)

- [ ] **Step 3:** заменить комментарий у `.tabs`; после `#ed-source { display:flex }` добавить `#ed-source[hidden] { display: none; }`; `.tab:disabled { opacity: 0.35; cursor: not-allowed; }` и `.tab:disabled:hover { color: var(--muted); }`

- [ ] **Step 4:** тест зелёный

- [ ] **Step 5:** commit `fix: прятать исходники вместе с hidden`

---

### Task 3: Редактор и рендер

**Files:**
- Modify: `web/src/editor.ts`, `web/src/render.ts`, `README.md`

- [ ] **Step 1:** `showTab` прячет все панели (`key !== name`), в том числе `source`. Клик по `disabled` не вызывает `showTab`. `syncTabs()` ставит `disabled` с `tabEnabled`, затем `showTab(resolveTab(...))`. Вызывать из `applyClips` и после `listRenders`. `boot` параллельно грузит `listRenders`. `mountRender` четвёртым аргументом `onCount?: (n: number) => void` после каждого `refresh`. README: открыта одна вкладка; исходники не остаются на соседних.

- [ ] **Step 2:** `cd web && npm test && npx tsc --noEmit`

- [ ] **Step 3:** commit `feat: вкладки редактора показывают одну работу`
