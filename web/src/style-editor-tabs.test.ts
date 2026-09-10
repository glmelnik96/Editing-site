import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'

/**
 * Блочные контейнеры, которые редактор прячет через `el.hidden`.
 *
 * Атрибут `hidden` — это всего лишь `display: none` из таблицы браузера, и любое собственное
 * `display: flex` его перебивает: панель остаётся на экране, хотя код уверен, что спрятал её.
 * Ловится это только глазами, а живых проверок вёрстки у нас нет — jsdom не подключён. Поэтому
 * проверяем сам файл стилей.
 */
const HIDDEN_FLEX = ['#ed-source', '.side-body', '.fold-body']

test('спрятанные панели не остаются на экране из-за display:flex', () => {
  const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'style.css'), 'utf8')
  for (const selector of HIDDEN_FLEX) {
    const escaped = selector.replace(/[.#]/g, '\\$&')
    const rule = css.match(new RegExp(`${escaped}\\[hidden\\]\\s*\\{[^}]+\\}`))
    expect(rule?.[0], `нет правила ${selector}[hidden]`).toContain('display: none')
  }
  expect(css).not.toMatch(/Исходники и музыка остаются/)
})
