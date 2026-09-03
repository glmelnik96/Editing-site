import { expect, test } from 'vitest'
import { loginErrorText } from './errors'

test('loginErrorText maps known codes and falls back', () => {
  expect(loginErrorText(null)).toBe('')
  expect(loginErrorText('not_allowed')).toContain('не в списке')
  expect(loginErrorText('weird')).toBe('Не удалось войти (weird).')
})
