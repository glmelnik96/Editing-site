import { describe, expect, it } from 'vitest'
import { LOADING_DELAY_MS, stageNote, stageNoteHtml } from './stage-note'

const base = { clipCount: 3, readyRecords: 2, video: 'ready' as const, waitedMs: 0, name: 'интервью.mp4' }

describe('надпись на сцене', () => {
  it('пустой проект подсказывает первый шаг, а без записей — где их взять', () => {
    expect(stageNote({ ...base, clipCount: 0 })).toEqual({ kind: 'empty', noRecords: false })
    expect(stageNote({ ...base, clipCount: 0, readyRecords: 0 })).toEqual({ kind: 'empty', noRecords: true })
    expect(stageNoteHtml({ kind: 'empty', noRecords: false })).toContain('Здесь будет ролик.')
    expect(stageNoteHtml({ kind: 'empty', noRecords: true })).toContain('href="#/files"')
  })

  it('загрузку показывает, только если ждём дольше порога — быстрые переходы не мигают', () => {
    expect(stageNote({ ...base, video: 'waiting', waitedMs: LOADING_DELAY_MS - 1 })).toEqual({ kind: 'none' })
    expect(stageNote({ ...base, video: 'waiting', waitedMs: LOADING_DELAY_MS })).toEqual({ kind: 'loading' })
    expect(stageNoteHtml({ kind: 'loading' })).toContain('Готовлю кадр…')
  })

  it('ошибка называет файл и что делать; имя экранируется', () => {
    expect(stageNote({ ...base, video: 'error' })).toEqual({ kind: 'error', name: 'интервью.mp4' })
    expect(stageNote({ ...base, video: 'error', name: null })).toEqual({ kind: 'error', name: 'запись' })
    const html = stageNoteHtml({ kind: 'error', name: '<b>x</b>.mp4' })
    expect(html).toContain('&lt;b&gt;x&lt;/b&gt;.mp4')
    expect(html).toContain('Обновите страницу')
  })

  it('готовое видео — ничего поверх', () => {
    expect(stageNote(base)).toEqual({ kind: 'none' })
    expect(stageNoteHtml({ kind: 'none' })).toBe('')
  })
})
