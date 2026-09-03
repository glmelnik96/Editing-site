import { expect, test } from 'vitest'
import { escapeHtml } from './html'

test('escapeHtml escapes the five special characters', () => {
  expect(escapeHtml(`<a href="x">Tom & 'Jerry'</a>`)).toBe(
    '&lt;a href=&quot;x&quot;&gt;Tom &amp; &#39;Jerry&#39;&lt;/a&gt;',
  )
})
