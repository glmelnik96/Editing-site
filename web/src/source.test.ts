import { expect, test } from 'vitest'
import { sourcePoolNote } from './source'

test('без готового видео — ссылка загрузить', () => {
  expect(sourcePoolNote(0)).toContain('#/files')
  expect(sourcePoolNote(0)).toContain('Загрузите запись')
  expect(sourcePoolNote(1)).toBe('')
})
