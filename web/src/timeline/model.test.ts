import { describe, expect, it } from 'vitest'
import type { Clip } from './model'
import {
  clipAt,
  insertClip,
  layout,
  moveClip,
  newClipId,
  removeClip,
  sourceTime,
  splitAt,
  timelineStart,
  totalDuration,
  trimClip,
} from './model'

function clip(id: string, inS: number, outS: number, asset = 'ast_1'): Clip {
  return { id, asset_id: asset, in: inS, out: outS, snap_to_pauses: false, in_verified: false, out_verified: false }
}

const three = [clip('c1', 0, 4), clip('c2', 10, 12), clip('c3', 1, 4.5)]

describe('время шкалы', () => {
  it('складывает длительности клипов', () => {
    expect(totalDuration(three)).toBe(9.5)
    expect(totalDuration([])).toBe(0)
  })

  it('знает, где начинается каждый клип', () => {
    expect(timelineStart(three, 0)).toBe(0)
    expect(timelineStart(three, 1)).toBe(4)
    expect(timelineStart(three, 2)).toBe(6)
    expect(timelineStart(three, 9)).toBe(9.5)
  })

  it('находит клип по времени шкалы', () => {
    expect(clipAt(three, 0)?.index).toBe(0)
    expect(clipAt(three, 3.999)?.index).toBe(0)
    expect(clipAt(three, 4)?.index).toBe(1)
    expect(clipAt(three, 6.5)?.index).toBe(2)
    expect(clipAt(three, 9.5)).toBeNull()
    expect(clipAt(three, -1)).toBeNull()
    expect(clipAt([], 0)).toBeNull()
  })

  it('переводит время шкалы во время исходника', () => {
    expect(sourceTime(three, 0)).toEqual({ index: 0, assetId: 'ast_1', time: 0 })
    expect(sourceTime(three, 4.5)).toEqual({ index: 1, assetId: 'ast_1', time: 10.5 })
    expect(sourceTime(three, 100)).toBeNull()
  })
})

describe('правки списка', () => {
  it('вставляет клип в конец и в середину', () => {
    const added = insertClip(three, clip('c4', 0, 1), 1)
    expect(added.map(c => c.id)).toEqual(['c1', 'c4', 'c2', 'c3'])
    expect(insertClip(three, clip('c4', 0, 1)).map(c => c.id)).toEqual(['c1', 'c2', 'c3', 'c4'])
    expect(three).toHaveLength(3) // исходный список не меняется
  })

  it('удаляет клип по id', () => {
    expect(removeClip(three, 'c2').map(c => c.id)).toEqual(['c1', 'c3'])
    expect(removeClip(three, 'нет такого')).toHaveLength(3)
  })

  it('переставляет клип', () => {
    expect(moveClip(three, 0, 2).map(c => c.id)).toEqual(['c2', 'c3', 'c1'])
    expect(moveClip(three, 2, 0).map(c => c.id)).toEqual(['c3', 'c1', 'c2'])
    expect(moveClip(three, 1, 1).map(c => c.id)).toEqual(['c1', 'c2', 'c3'])
    expect(moveClip(three, 0, 9).map(c => c.id)).toEqual(['c2', 'c3', 'c1'])
  })

  it('режет клип по времени шкалы', () => {
    const cut = splitAt(three, 2)
    expect(cut.map(c => [c.id, c.in, c.out])).toEqual([
      ['c1', 0, 2],
      [cut[1].id, 2, 4],
      ['c2', 10, 12],
      ['c3', 1, 4.5],
    ])
    expect(cut[1].id).not.toBe('c1')
    expect(cut[1].asset_id).toBe('ast_1')
  })

  it('не режет по краю клипа и слишком близко к краю', () => {
    expect(splitAt(three, 0)).toBe(three)
    expect(splitAt(three, 4)).toBe(three)
    expect(splitAt(three, 0.05)).toBe(three)
    expect(splitAt(three, 3.95)).toBe(three)
    expect(splitAt(three, 100)).toBe(three)
  })

  it('разрез сбрасывает подтверждение новой границы', () => {
    const verified = [{ ...clip('c1', 0, 4), snap_to_pauses: true, in_verified: true, out_verified: true }]
    const cut = splitAt(verified, 2)
    expect(cut[0].in_verified).toBe(true)
    expect(cut[0].out_verified).toBe(false) // новый рез ещё не подтверждён
    expect(cut[1].in_verified).toBe(false)
    expect(cut[1].out_verified).toBe(true)
    expect(cut[1].snap_to_pauses).toBe(true)
  })

  it('подрезает клип и держит минимальную длину', () => {
    const list = [clip('c1', 5, 10)]
    expect(trimClip(list, 'c1', { in: 6 })[0].in).toBe(6)
    expect(trimClip(list, 'c1', { out: 9 })[0].out).toBe(9)
    expect(trimClip(list, 'c1', { in: 9.95 })[0].in).toBe(9.9) // не ближе 0.1 с к out
    expect(trimClip(list, 'c1', { out: 5.05 })[0].out).toBe(5.1)
    expect(trimClip(list, 'c1', { in: -3 })[0].in).toBe(0)
    expect(trimClip(list, 'c1', { out: 99 }, { duration: 12 })[0].out).toBe(12)
  })

  it('подрезка сбрасывает подтверждение только тронутой границы', () => {
    const list = [{ ...clip('c1', 5, 10), in_verified: true, out_verified: true }]
    const trimmed = trimClip(list, 'c1', { in: 6 })
    expect(trimmed[0].in_verified).toBe(false)
    expect(trimmed[0].out_verified).toBe(true)
  })

  it('округляет времена до миллисекунды', () => {
    const list = [clip('c1', 0, 10)]
    expect(trimClip(list, 'c1', { in: 1.00049 })[0].in).toBe(1)
    expect(splitAt(list, 3.33333)[0].out).toBe(3.333)
  })

  it('выдаёт неповторяющиеся id', () => {
    const ids = new Set([newClipId(three), newClipId(three), newClipId(three)])
    expect(ids.size).toBe(3)
    expect(newClipId(three).startsWith('c')).toBe(true)
  })
})

describe('раскладка в пиксели', () => {
  it('считает левый край и ширину блоков', () => {
    expect(layout(three, 10)).toEqual([
      { id: 'c1', left: 0, width: 40, start: 0, duration: 4 },
      { id: 'c2', left: 40, width: 20, start: 4, duration: 2 },
      { id: 'c3', left: 60, width: 35, start: 6, duration: 3.5 },
    ])
  })

  it('не даёт блоку схлопнуться в невидимую полоску', () => {
    const tiny = layout([clip('c1', 0, 0.1)], 10)
    expect(tiny[0].width).toBeGreaterThanOrEqual(8)
  })
})
