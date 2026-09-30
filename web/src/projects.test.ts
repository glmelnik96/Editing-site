import { describe, expect, it } from 'vitest'
import type { ProjectCard } from './project'
import { dropProjectQuestion, projectCardHtml } from './projects'

const card = (over: Partial<ProjectCard> = {}): ProjectCard => ({
  id: 'prj_1',
  name: 'Планёрка',
  version: 3,
  created_at: '2026-09-01T10:00:00.000Z',
  updated_at: '2026-09-02T10:00:00.000Z',
  clips_count: 4,
  duration: 95,
  owner_email: 'liza@ya.ru',
  owner_name: 'Лиза',
  ...over,
})

describe('карточка проекта', () => {
  it('называет автора, а у своего пишет «вы»', () => {
    expect(projectCardHtml(card(), 0, 'gleb@ya.ru')).toContain('Лиза · 4 кл.')
    expect(projectCardHtml(card({ owner_email: 'gleb@ya.ru' }), 0, 'gleb@ya.ru')).toContain('вы · 4 кл.')
  })

  it('удалить может любой: кнопка есть и у чужого', () => {
    expect(projectCardHtml(card(), 0, 'gleb@ya.ru')).toContain('data-drop="prj_1"')
  })
})

describe('вопрос перед удалением', () => {
  it('у чужого проекта называет автора', () => {
    expect(dropProjectQuestion(card(), 'gleb@ya.ru')).toContain('«Планёрка» (автор — Лиза)')
  })

  it('у своего — без автора', () => {
    expect(dropProjectQuestion(card({ owner_email: 'gleb@ya.ru' }), 'gleb@ya.ru')).not.toContain('автор')
  })
})
