import { describe, expect, it } from 'vitest'
import { createHistory } from './history'

describe('стопка отмены', () => {
  it('пустая ничего не отдаёт', () => {
    const h = createHistory<string>(5)
    expect(h.canUndo()).toBe(false)
    expect(h.undo()).toBeNull()
  })

  it('отдаёт состояния в обратном порядке', () => {
    const h = createHistory<string>(5)
    h.push('первое')
    h.push('второе')
    expect(h.undo()).toBe('второе')
    expect(h.undo()).toBe('первое')
    expect(h.undo()).toBeNull()
  })

  it('держит только последние пять', () => {
    const h = createHistory<number>(5)
    for (let i = 1; i <= 8; i++) h.push(i)
    expect(h.size()).toBe(5)
    expect(h.undo()).toBe(8)
    expect([h.undo(), h.undo(), h.undo(), h.undo()]).toEqual([7, 6, 5, 4])
    expect(h.undo()).toBeNull()
  })

  it('чистится целиком', () => {
    const h = createHistory<string>(5)
    h.push('а')
    h.clear()
    expect(h.canUndo()).toBe(false)
  })

  it('знает, сколько шагов доступно', () => {
    const h = createHistory<string>(3)
    expect(h.size()).toBe(0)
    h.push('а')
    h.push('б')
    expect(h.size()).toBe(2)
    h.undo()
    expect(h.size()).toBe(1)
  })
})
