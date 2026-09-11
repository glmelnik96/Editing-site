import { describe, expect, it } from 'vitest'
import { createHistory } from './history'

describe('отмена и «Вернуть»', () => {
  it('пустая ничего не отдаёт', () => {
    const h = createHistory<string>()
    expect(h.canUndo()).toBe(false)
    expect(h.canRedo()).toBe(false)
    expect(h.undo('сейчас')).toBeNull()
    expect(h.redo('сейчас')).toBeNull()
  })

  it('отмена отдаёт прошлое, «Вернуть» — отменённое', () => {
    const h = createHistory<string>()
    h.push('а')
    h.push('б')
    expect(h.undo('в')).toBe('б')
    expect(h.canRedo()).toBe(true)
    expect(h.redo('б')).toBe('в')
    expect(h.undo('в')).toBe('б')
    expect(h.undo('б')).toBe('а')
    expect(h.undo('а')).toBeNull()
  })

  it('новая правка очищает «Вернуть»', () => {
    const h = createHistory<string>()
    h.push('а')
    expect(h.undo('б')).toBe('а')
    h.push('а')
    expect(h.canRedo()).toBe(false)
    expect(h.redo('в')).toBeNull()
  })

  it('держит не больше заданного', () => {
    const h = createHistory<number>(3)
    for (let i = 1; i <= 5; i++) h.push(i)
    expect(h.size()).toBe(3)
    expect([h.undo(6), h.undo(5), h.undo(4)]).toEqual([5, 4, 3])
    expect(h.undo(3)).toBeNull()
  })

  it('по умолчанию — пятьдесят шагов', () => {
    const h = createHistory<number>()
    for (let i = 1; i <= 60; i++) h.push(i)
    expect(h.size()).toBe(50)
  })

  it('чистится целиком, вместе с «Вернуть»', () => {
    const h = createHistory<string>()
    h.push('а')
    h.undo('б')
    h.clear()
    expect(h.canUndo()).toBe(false)
    expect(h.canRedo()).toBe(false)
  })
})
