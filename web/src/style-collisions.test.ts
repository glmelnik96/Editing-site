import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'style.css'), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
)
/** Все правила «селекторы { свойства }», и вложенные в @media тоже. */
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m => ({
  selectors: m[1].split(',').map(s => s.trim()),
  body: m[2],
}))

test('сетка проектов остаётся сеткой: её класс не перебит слоями шкалы', () => {
  // Слой плиток волны однажды назвали так же, как сетку карточек проектов, — .tiles, — и его
  // position:absolute с pointer-events:none растянул сетку на всё окно и отключил в ней клики.
  const grid = rules.filter(rule => rule.selectors.some(s => /^\.tiles?(:[a-z-]+)?$/.test(s)))
  expect(grid.length).toBeGreaterThan(0)
  for (const rule of grid) {
    expect(rule.body, rule.selectors.join(', ')).not.toMatch(/position:\s*absolute|pointer-events:\s*none/)
  }
  expect(rules.some(rule => rule.selectors.includes('.wave-tiles'))).toBe(true)
})
