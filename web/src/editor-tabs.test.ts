import { describe, expect, it } from 'vitest'
import { resolveTab, tabEnabled } from './editor-tabs'

describe('вкладки редактора', () => {
  it('исходники всегда живые', () => {
    expect(tabEnabled('source', 0, false)).toBe(true)
  })

  it('субтитры и рендер серые без клипов и без ролика', () => {
    expect(tabEnabled('subtitles', 0, false)).toBe(false)
    expect(tabEnabled('renders', 0, false)).toBe(false)
  })

  it('первый клип открывает обе', () => {
    expect(tabEnabled('subtitles', 1, false)).toBe(true)
    expect(tabEnabled('renders', 1, false)).toBe(true)
  })

  it('рендер жив без клипов, если есть готовый файл', () => {
    expect(tabEnabled('renders', 0, true)).toBe(true)
    expect(tabEnabled('subtitles', 0, true)).toBe(false)
  })

  it('неживая вкладка сбрасывается на исходники', () => {
    expect(resolveTab('subtitles', 0, false)).toBe('source')
    expect(resolveTab('renders', 0, true)).toBe('renders')
    expect(resolveTab('subtitles', 2, false)).toBe('subtitles')
  })
})
