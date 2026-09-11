import { expect, it } from 'vitest'
import { pieceLabel } from './names'

it('подпись куска — имя записи, без записи — номер', () => {
  expect(pieceLabel('интервью.mp4', 'c3_945zdp', '7.0 с')).toBe('интервью.mp4 · 7.0 с')
  expect(pieceLabel(null, 'c3_945zdp', '7.0 с')).toBe('c3_945zdp · 7.0 с')
  expect(pieceLabel('', 's1', 'по кругу')).toBe('s1 · по кругу')
})
