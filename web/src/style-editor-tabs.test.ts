import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'

test('спрятанные исходники не остаются на экране из-за display:flex', () => {
  const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'style.css'), 'utf8')
  const hidden = css.match(/#ed-source\[hidden\]\s*\{[^}]+\}/)
  expect(hidden?.[0]).toContain('display: none')
  expect(css).not.toMatch(/Исходники и музыка остаются/)
})
