import { expect, test } from 'vitest'
import { emptyTrackHint } from './view'

test('пустая шкала просит кусок из исходников', () => {
  expect(emptyTrackHint(0)).toBe('Добавьте кусок из исходников')
  expect(emptyTrackHint(1)).toBe('')
})
