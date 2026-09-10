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
    // Новые качества из панели сборки называются своими словами, а не «черновиком».
    expect(jobTitle(job({ type: 'render', quality: 'preview', label: 'Ролик' }))).toBe('Сборка превью «Ролик»')
    expect(jobTitle(job({ type: 'render', quality: 'high', label: 'Ролик' }))).toBe(
      'Сборка в высоком качестве «Ролик»',
    )
    expect(jobTitle(job({ type: 'render', quality: 'target', label: 'Ролик' }))).toBe(
      'Сборка с заданным битрейтом «Ролик»',
    )
    expect(jobTitle(job({ type: 'render', quality: null, label: 'Ролик' }))).toBe('Сборка черновика «Ролик»')
    expect(jobTitle(job({ type: 'convert', label: 'утренний.mp3' }))).toBe('Конвертер «утренний.mp3»')
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

describe('пропавшее задание', () => {
  it('не объявляет готовым то, что просто не поместилось в ответ', () => {
    // Сервер отдаёт полсотни последних строк. На пачке загрузок живые задания вываливаются за
    // край ответа, и раньше панель писала «готово» о том, что даже не начиналось.
    const before: WorkState = { ...empty(), jobs: [job({ id: 'job_1', status: 'queued' })] }
    const after = foldIncoming(before, [], 1000)
    expect(after.flashes).toEqual([])
    expect(workRows(after, 1000)).toEqual([])
  })

  it('о законченном задании говорит по его статусу, а не по исчезновению', () => {
    const before: WorkState = { ...empty(), jobs: [job({ id: 'job_1', status: 'running' })] }
    const after = foldIncoming(before, [job({ id: 'job_1', status: 'done', progress: 1 })], 1000)
    expect(after.flashes).toEqual([
      { id: 'job_1', kind: 'done', until: 1000 + FLASH_MS, title: 'Анализ «Нарезка.mp4»' },
    ])
  })

  it('просроченную вспышку не показывает', () => {
    const state: WorkState = {
      ...empty(),
      flashes: [{ id: 'job_1', kind: 'done', until: 500, title: 'Анализ «Нарезка.mp4»' }],
    }
    expect(workRows(state, 1000)).toEqual([])
  })
})
