import { describe, expect, it } from 'vitest'
import {
  downloadFileName,
  fitFrame,
  fmtDuration,
  fmtSize,
  needsPolling,
  statusText,
  withoutExt,
} from './assets'

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

describe('имя файла для скачивания', () => {
  it('чистит то, что имя файла не переживёт', () => {
    expect(downloadFileName('Планёрка: 10/09 «итоги»', 'mp4')).toBe('Планёрка 10 09 «итоги».mp4')
    expect(downloadFileName('  два   пробела  ', 'mp3')).toBe('два пробела.mp3')
  })
  it('пустое имя не даёт файла с одной точкой', () => {
    expect(downloadFileName('', 'mp4')).toBe('файл.mp4')
    expect(downloadFileName('///', 'mp4')).toBe('файл.mp4')
  })
  it('снимает старое расширение, но не съедает точки в имени', () => {
    expect(withoutExt('запись.mp4')).toBe('запись')
    expect(withoutExt('10.09.2026.mov')).toBe('10.09.2026')
    expect(withoutExt('без расширения')).toBe('без расширения')
    expect(withoutExt('.hidden')).toBe('.hidden')
  })
})

describe('кадр записи в коробке', () => {
  it('16:9 — во всю коробку', () => {
    expect(fitFrame(1920, 1080)).toEqual({ width: 96, height: 54 })
  })

  it('4:3 и вертикальный — во всю высоту, поля по бокам', () => {
    expect(fitFrame(640, 480)).toEqual({ width: 72, height: 54 })
    expect(fitFrame(1080, 1920)).toEqual({ width: 30, height: 54 })
  })

  it('шире 16:9 — во всю ширину, поля сверху и снизу', () => {
    expect(fitFrame(2560, 1080)).toEqual({ width: 96, height: 41 })
  })

  it('без размеров — как 16:9', () => {
    expect(fitFrame(0, 0)).toEqual({ width: 96, height: 54 })
  })
})
