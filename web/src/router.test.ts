import { expect, test } from 'vitest'
import { parseRoute } from './router'

test('пустой адрес, «#» и «#/» — это главная', () => {
  expect(parseRoute('')).toEqual({ name: 'home' })
  expect(parseRoute('#')).toEqual({ name: 'home' })
  expect(parseRoute('#/')).toEqual({ name: 'home' })
})

test('у каждого экрана свой адрес', () => {
  expect(parseRoute('#/files')).toEqual({ name: 'files' })
  expect(parseRoute('#/new')).toEqual({ name: 'new' })
  expect(parseRoute('#/projects')).toEqual({ name: 'projects' })
  expect(parseRoute('#/settings')).toEqual({ name: 'settings' })
  expect(parseRoute('#/admin')).toEqual({ name: 'admin' })
})

test('адрес проекта отдаёт редактор с идентификатором', () => {
  expect(parseRoute('#/p/prj_0123456789ab')).toEqual({ name: 'editor', projectId: 'prj_0123456789ab' })
  expect(parseRoute('#/p/a-b_C9')).toEqual({ name: 'editor', projectId: 'a-b_C9' })
})

test('мусор в адресе уводит на главную, а не в редактор', () => {
  expect(parseRoute('#/чепуха')).toEqual({ name: 'home' })
  expect(parseRoute('#/p/')).toEqual({ name: 'home' })
  expect(parseRoute('#/p/../x')).toEqual({ name: 'home' })
  expect(parseRoute('#/p/a b')).toEqual({ name: 'home' })
  expect(parseRoute('#/p/a/b')).toEqual({ name: 'home' })
  expect(parseRoute('#/files/')).toEqual({ name: 'home' })
})
