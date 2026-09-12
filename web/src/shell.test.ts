import { describe, expect, it } from 'vitest'
import { navHtml, type Me } from './shell'

function me(role: Me['role']): Me {
  return {
    id: 'u1',
    email: 'someone@example.com',
    name: 'Кто-то',
    role,
    auth: 'cookie',
    quota: { used_bytes: 1_073_741_824, limit_bytes: 21_474_836_480 },
    server: { files_bytes: 1_181_116_006, free_bytes: 93_415_538_688 },
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

  it('место — на всём сервере: файлы сервиса и сколько свободно', () => {
    expect(navHtml(me('user'))).toContain('1.1 ГБ · свободно 87.0 ГБ')
  })

  it('в подсказке — почта и свой расход с лимитом', () => {
    expect(navHtml(me('user'))).toContain('title="someone@example.com&#10;Ваши файлы: 1.0 ГБ из 20.0 ГБ"')
  })
})
