import { describe, expect, it } from 'vitest'
import type { Overlay } from '../project'
import {
  newOverlayId,
  overlayBox,
  overlayPlan,
  PLACES,
  removeOverlay,
  SIZED_PLACES,
  updateOverlay,
} from './overlays'

const overlay = (over: Partial<Overlay> = {}): Overlay => ({
  id: 'o1',
  asset_id: 'ast_1',
  at: 2,
  in: 0,
  out: 4,
  place: 'tr',
  size: 30,
  volume: 0,
  fade_in: 0,
  fade_out: 0,
  ...over,
})

describe('коробка наложения', () => {
  // Сверено с overlay_box на сервере для кадра 1280×720: отступ 2 % ширины — это 26 px по обеим осям.
  const wide = 16 / 9

  it('угловые прижимаются к своему углу с одинаковым отступом в пикселях', () => {
    const tr = overlayBox('tr', 30, wide)
    expect(tr).toMatchObject({ width: 30, height: 30, align: 'right top' })
    expect(tr.left).toBeCloseTo(68, 5)
    expect(tr.top).toBeCloseTo(2 * wide, 5) // 3.56 % высоты — те же пиксели, что 2 % ширины
    const bl = overlayBox('bl', 30, wide)
    expect(bl.left).toBe(2)
    expect(bl.top).toBeCloseTo(100 - 30 - 2 * wide, 5)
    expect(bl.align).toBe('left bottom')
  })

  it('весь кадр и половины не зависят от размера', () => {
    expect(overlayBox('full', 30, wide)).toEqual({ left: 0, top: 0, width: 100, height: 100, align: 'center center' })
    expect(overlayBox('full', 80, wide)).toEqual(overlayBox('full', 5, wide))
    expect(overlayBox('left', 30, wide)).toMatchObject({ left: 0, width: 50, height: 100 })
    expect(overlayBox('right', 30, wide)).toMatchObject({ left: 50, width: 50, height: 100 })
  })

  it('центр стоит посередине', () => {
    expect(overlayBox('center', 40, wide)).toEqual({ left: 30, top: 30, width: 40, height: 40, align: 'center center' })
  })

  it('у каждого пресета есть подпись, а размер значим не у всех', () => {
    expect(PLACES.map(p => p.value).sort()).toEqual(['bl', 'br', 'center', 'full', 'left', 'right', 'tl', 'tr'])
    expect(PLACES.every(p => p.label.trim())).toBe(true)
    expect(SIZED_PLACES.has('tr') && SIZED_PLACES.has('center')).toBe(true)
    expect(SIZED_PLACES.has('full') || SIZED_PLACES.has('left')).toBe(false)
  })
})

describe('что видно на сцене', () => {
  it('до своего времени наложения нет, внутри — своё место записи', () => {
    const list = [overlay({ in: 10, out: 14 })]
    expect(overlayPlan(list, 1.9, 60)).toEqual([])
    expect(overlayPlan(list, 3.5, 60)).toEqual([{ id: 'o1', assetId: 'ast_1', time: 11.5, opacity: 1, volume: 0 }])
    expect(overlayPlan(list, 6, 60)).toEqual([])
  })

  it('появление и исчезновение — линейная прозрачность, как в сборке', () => {
    const list = [overlay({ fade_in: 1, fade_out: 1 })] // с 2 по 6 с
    expect(overlayPlan(list, 2.5, 60)[0].opacity).toBe(0.5)
    expect(overlayPlan(list, 4, 60)[0].opacity).toBe(1)
    expect(overlayPlan(list, 5.75, 60)[0].opacity).toBe(0.25)
  })

  it('хвост за концом ролика не показывается — его обрежет сборка', () => {
    expect(overlayPlan([overlay({ at: 8 })], 9, 8.5)).toEqual([])
  })

  it('перекрывающиеся наложения видны оба, в порядке списка', () => {
    const list = [overlay(), overlay({ id: 'o2', at: 3 })]
    expect(overlayPlan(list, 3.5, 60).map(c => c.id)).toEqual(['o1', 'o2'])
  })
})

describe('правка наложения', () => {
  it('зажимает размер, громкость и появление в допустимое', () => {
    const [big] = updateOverlay([overlay()], 'o1', { size: 500 })
    expect(big.size).toBe(100)
    const [tiny] = updateOverlay([overlay()], 'o1', { size: 1, volume: 7, fade_in: -2 })
    expect([tiny.size, tiny.volume, tiny.fade_in]).toEqual([5, 2, 0])
  })

  it('не трогает соседей ни при правке, ни при удалении', () => {
    const list = [overlay(), overlay({ id: 'o2' })]
    expect(updateOverlay(list, 'o1', { place: 'full' })[1]).toBe(list[1])
    expect(removeOverlay(list, 'o1')).toEqual([list[1]])
  })

  it('имя нового наложения не совпадает ни с клипом, ни со звуком, ни с наложением', () => {
    const names = new Set(
      Array.from({ length: 100 }, () => newOverlayId([{ id: 'c1' }], [{ id: 's1' }], [{ id: 'o1' }])),
    )
    expect(names.size).toBe(100)
    for (const name of names) {
      expect(name).toMatch(/^o2_[0-9a-z]+$/)
    }
  })
})
