import { describe, expect, it, vi } from 'vitest'
import { barsFor, sliceThumbs, thumbBackground, waveBars } from './strip'

const meta = { count: 6, cols: 3, rows: 2, interval: 2, width: 160, height: 90 }

describe('волна', () => {
  it('сжимает пики до нужного числа столбиков, беря максимум окна', () => {
    const peaks = [0, 10, 200, 5, 0, 0, 100, 100]
    expect(waveBars(peaks, 4)).toEqual([10, 200, 0, 100])
  })

  it('растягивает короткий ряд без выхода за границы', () => {
    expect(waveBars([100, 200], 4)).toEqual([100, 100, 200, 200])
    expect(waveBars([], 3)).toEqual([0, 0, 0])
    expect(waveBars([50], 0)).toEqual([])
  })

  it('берёт участок пиков под отрезок исходника', () => {
    const peaks = Array.from({ length: 500 }, (_, i) => i % 256)
    const bars = barsFor({ peaks, rate: 50 }, { from: 2, to: 4 }, 10)
    expect(bars).toHaveLength(10)
    expect(Math.max(...bars)).toBeLessThanOrEqual(255)
  })

  it('участок за пределами записи даёт нули, а не ошибку', () => {
    expect(barsFor({ peaks: [1, 2, 3], rate: 50 }, { from: 100, to: 101 }, 3)).toEqual([0, 0, 0])
    expect(barsFor(null, { from: 0, to: 1 }, 3)).toEqual([0, 0, 0])
  })
})

describe('кадры из спрайта', () => {
  it('считает фон для кадра по времени', () => {
    expect(thumbBackground(meta, 0)).toEqual({ x: 0, y: 0, width: 480, height: 180 })
    expect(thumbBackground(meta, 3)).toEqual({ x: -160, y: 0, width: 480, height: 180 })
    expect(thumbBackground(meta, 5)).toEqual({ x: -320, y: 0, width: 480, height: 180 })
    expect(thumbBackground(meta, 7)).toEqual({ x: 0, y: -90, width: 480, height: 180 })
    expect(thumbBackground(meta, 1000)).toEqual({ x: -320, y: -90, width: 480, height: 180 })
  })

  it('раскладывает кадры по ширине блока', () => {
    const frames = sliceThumbs(meta, { from: 0, to: 6 }, 320)
    expect(frames).toHaveLength(2) // 320 px при кадре 160 px
    expect(frames[0].left).toBe(0)
    expect(frames[1].left).toBe(160)
    expect(frames[0].background.x).toBe(0)
  })

  it('узкий блок получает хотя бы один кадр', () => {
    expect(sliceThumbs(meta, { from: 0, to: 1 }, 20)).toHaveLength(1)
    expect(sliceThumbs(meta, { from: 0, to: 1 }, 0)).toEqual([])
  })

  it('без раскладки кадров ничего не рисует', () => {
    expect(sliceThumbs(null, { from: 0, to: 5 }, 300)).toEqual([])
  })
})

describe('загрузка данных ассета', () => {
  it('читает пики и раскладку один раз на ассет', async () => {
    const fetcher = vi.fn(async (url: string) =>
      url.endsWith('peaks.json') ? { rate: 50, peaks: [1, 2] } : meta,
    )
    const { assetData } = await import('./strip')
    const cache = new Map()
    const first = await assetData('ast_1', { peaks: '/p/peaks.json', thumbs_meta: '/p/thumbs.json' }, cache, fetcher)
    const second = await assetData('ast_1', { peaks: '/p/peaks.json', thumbs_meta: '/p/thumbs.json' }, cache, fetcher)
    expect(first).toBe(second)
    expect(fetcher).toHaveBeenCalledTimes(2) // пики и раскладка, но только по одному разу
  })

  it('переживает недоступные файлы', async () => {
    const { assetData } = await import('./strip')
    const data = await assetData('ast_2', { peaks: '/нет', thumbs_meta: '/нет' }, new Map(), async () => {
      throw new Error('404')
    })
    expect(data.peaks).toBeNull()
    expect(data.thumbs).toBeNull()
  })
})
