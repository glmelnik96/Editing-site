import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'

test('hover ключевой кнопки оставляет тёмный текст', () => {
  const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'style.css'), 'utf8')
  const hover = css.match(/\.btn-key:hover:not\(:disabled\)\s*\{[^}]+\}/)
  expect(hover?.[0]).toContain('color: var(--brand-ink)')
})
