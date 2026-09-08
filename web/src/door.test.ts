import { expect, test } from 'vitest'
import { LOGIN_LABEL } from './door'

test('на двери всегда короткое Войти, без Яндекса', () => {
  expect(LOGIN_LABEL).toBe('Войти')
  expect(LOGIN_LABEL).not.toContain('Яндекс')
})
