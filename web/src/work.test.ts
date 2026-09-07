import { describe, expect, it } from 'vitest'
import { FLASH_MS, foldIncoming, jobTitle, workRows, type WorkJob, type WorkState } from './work'

const job = (over: Partial<WorkJob> = {}): WorkJob => ({
  id: 'job_1',
  type: 'analyze',
  status: 'running',
  progress: 0.4,
  error: null,
  label: 'Нарезка.mp4',
  cancelable: false,
  quality: null,
  ...over,
})

const empty = (): WorkState => ({
  jobs: [],
  uploads: [],
  dismissed: [],
  heldFailed: [],
  flashes: [],
  uploadFails: [],
})

describe('подписи', () => {
  it('ставит кавычки в действии', () => {
    expect(jobTitle(job())).toBe('Анализ «Нарезка.mp4»')
    expect(jobTitle(job({ type: 'proxy' }))).toBe('Прокси «Нарезка.mp4»')
    expect(jobTitle(job({ type: 'transcribe' }))).toBe('Расшифровка «Нарезка.mp4»')
    expect(jobTitle(job({ type: 'render', quality: 'draft', label: 'Ролик' }))).toBe('Сборка черновика «Ролик»')
    expect(jobTitle(job({ type: 'render', quality: 'final', label: 'Ролик' }))).toBe('Сборка финала «Ролик»')
  })
})

describe('склейка', () => {
  it('показывает загрузку и живое задание разными строками', () => {
    const state: WorkState = {
      ...empty(),
      uploads: [{ id: 'up_1', name: 'подкаст.mp4', done: 7, total: 10 }],
      jobs: [job()],
    }
    const rows = workRows(state, 0)
    expect(rows.map(r => r.key)).toEqual(['up:up_1', 'job_1'])
    expect(rows[0]?.title).toBe('Загрузка «подкаст.mp4»')
    expect(rows[0]?.percent).toBe(70)
    expect(rows[0]?.cancelable).toBe(true)
    expect(rows[1]?.percent).toBe(40)
    expect(rows[1]?.cancelable).toBe(false)
  })

  it('успех мелькает и пропадает', () => {
    const prev = { ...empty(), jobs: [job({ status: 'running' })] }
    const now = 1000
    const next = foldIncoming(prev, [job({ status: 'done', progress: 1 })], now)
    const during = workRows(next, now)
    expect(during.some(r => r.title.includes('готово'))).toBe(true)
    const after = workRows(next, now + FLASH_MS + 1)
    expect(after.some(r => r.key === 'job_1')).toBe(false)
  })

  it('сбой держится после того, как сервер его перестал отдавать', () => {
    const failed = job({ status: 'failed', error: 'нет места на диске' })
    const afterFail = foldIncoming(empty(), [failed], 0)
    const gone = foldIncoming(afterFail, [], 10)
    const rows = workRows(gone, 10)
    expect(rows).toHaveLength(1)
    expect(rows[0]?.error).toBe('нет места на диске')
    expect(rows[0]?.closeable).toBe(true)
    const closed = { ...gone, dismissed: ['job_1'] }
    expect(workRows(closed, 10)).toEqual([])
  })
})
