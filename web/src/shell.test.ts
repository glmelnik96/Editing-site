import { describe, expect, it } from 'vitest'
import { navHtml, type Me } from './shell'

function me(role: Me['role']): Me {
  return {
    id: 'u1',
    email: 'someone@example.com',
    name: 'Кто-то',
    role,
    auth: 'cookie',
    quota: { used_bytes: 2_300_000_000, limit_bytes: 20_000_000_000 },
  }
}

describe('шапка', () => {
  it('«Проекты» и «Записи» есть у всех — вход к проектам не зависит от своих проектов', () => {
    for (const role of ['user', 'admin'] as const) {
      const html = navHtml(me(role))
      expect(html).toContain('<a href="#/projects">Проекты</a>')
      expect(html).toContain('<a href="#/files">Записи</a>')
    }
  })

  it('разделы стоят перед «Настройками»', () => {
    const html = navHtml(me('user'))
    expect(html.indexOf('#/projects')).toBeLessThan(html.indexOf('#/settings'))
    expect(html.indexOf('#/files')).toBeLessThan(html.indexOf('#/settings'))
  })

  it('«Кабинет доступа» — только у админа', () => {
    expect(navHtml(me('admin'))).toContain('<a href="#/admin">Кабинет доступа</a>')
    expect(navHtml(me('user'))).not.toContain('#/admin')
  })
})
