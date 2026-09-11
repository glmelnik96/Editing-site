import { expect, it } from 'vitest'
import { pageTitle } from './titles'

it('заголовок вкладки — экран или проект, потом сервис', () => {
  expect(pageTitle('Записи')).toBe('Записи — Editing site')
  expect(pageTitle('Pustoy')).toBe('Pustoy — Editing site')
  expect(pageTitle('')).toBe('Editing site')
  expect(pageTitle()).toBe('Editing site')
})
