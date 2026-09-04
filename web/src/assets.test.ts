import { describe, expect, it } from 'vitest'
import { fmtDuration, fmtSize, needsPolling, statusText } from './assets'

describe('assets helpers', () => {
  it('formats sizes', () => {
    expect(fmtSize(0)).toBe('0 Б')
    expect(fmtSize(1536)).toBe('1.5 КБ')
    expect(fmtSize(5 * 1024 ** 3)).toBe('5.0 ГБ')
  })
  it('formats durations', () => {
    expect(fmtDuration(null)).toBe('—')
    expect(fmtDuration(65.4)).toBe('1:05')
    expect(fmtDuration(3725)).toBe('1:02:05')
  })
  it('names statuses in russian and knows which are final', () => {
    expect(statusText('uploaded')).toBe('загружен, ждёт анализа')
    expect(statusText('proxy_ready')).toBe('готов')
    expect(statusText('weird')).toBe('weird')
    expect(needsPolling([{ status: 'proxy_ready' }, { status: 'failed' }])).toBe(false)
    expect(needsPolling([{ status: 'ready' }, { status: 'analyzing' }])).toBe(true)
  })

  it('keeps polling while anything is still being processed', () => {
    expect(needsPolling([{ status: 'uploaded' }])).toBe(true)
    expect(needsPolling([{ status: 'analyzing' }])).toBe(true)
    expect(needsPolling([{ status: 'ready' }])).toBe(true)
    expect(needsPolling([{ status: 'proxy_ready' }, { status: 'failed' }])).toBe(false)
  })
})
