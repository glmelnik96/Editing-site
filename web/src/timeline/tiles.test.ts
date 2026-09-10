import { describe, expect, it } from 'vitest'
import { TILE_PX, tileRange, tileWidth, visibleTiles } from './tiles'

// Chrome перестаёт рисовать на холсте шире этого (замерено на Chrome 148).
const CANVAS_LIMIT = 65_535

describe('ширина плитки', () => {
  it('кратна ширине кадра, чтобы кадр не резался границей плитки', () => {
    expect(tileWidth(160)).toBe(1920)
    expect(tileWidth(160) % 160).toBe(0)
    expect(tileWidth(null)).toBe(TILE_PX)
    // Кадр шире желаемой плитки — плитка в один кадр, а не ноль.
    expect(tileWidth(3000)).toBe(3000)
  })
})

describe('видимые плитки', () => {
  it('у блока за пределами экрана плиток нет', () => {
    expect(visibleTiles(10_000, 500, 0, 3000, 2048)).toEqual([])
    expect(visibleTiles(0, 500, 600, 3000, 2048)).toEqual([])
  })

  it('берёт только плитки, пересекающие экран, и режет последнюю по концу блока', () => {
    // Блок с 1000 px шириной 5000, на экране шкала с 3000 по 6000: это пиксели 2000…5000 блока.
    expect(visibleTiles(1000, 5000, 3000, 6000, 2048)).toEqual([
      { index: 0, x0: 0, x1: 2048 },
      { index: 1, x0: 2048, x1: 4096 },
      { index: 2, x0: 4096, x1: 5000 },
    ])
  })

  it('на ровной границе не цепляет лишнюю плитку', () => {
    expect(visibleTiles(0, 10_000, 0, 4096, 2048).map(t => t.index)).toEqual([0, 1])
  })

  it('полтора часа на самом крупном масштабе — горстка плиток и ни одного холста за пределом', () => {
    // Ровно случай со снимка: запись 4951.9 с при 400 px/с — блок почти в два миллиона пикселей.
    const width = 4951.9 * 400
    const view = 1280
    const scroll = 900_000
    const tiles = visibleTiles(0, width, scroll - view, scroll + 2 * view, tileWidth(160))
    expect(tiles.length).toBeLessThanOrEqual(4)
    expect(tiles.every(t => t.x1 - t.x0 <= TILE_PX && t.x1 - t.x0 < CANVAS_LIMIT)).toBe(true)
  })
})

describe('отрезок исходника под плиткой', () => {
  it('переводит пиксели плитки в секунды клипа', () => {
    // Клип 10…20 с в блоке 1000 px: плитка 250…500 px — это 12.5…15 с.
    expect(tileRange({ index: 1, x0: 250, x1: 500 }, 1000, 10, 20)).toEqual({ from: 12.5, to: 15 })
  })

  it('у растянутого до минимума блока считает долями, а не пикселями в секунду', () => {
    // Клип 0.05 с растянут до 8 px: вся плитка — весь клип.
    expect(tileRange({ index: 0, x0: 0, x1: 8 }, 8, 3, 3.05)).toEqual({ from: 3, to: 3.05 })
  })
})
