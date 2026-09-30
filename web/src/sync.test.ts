import { describe, expect, it } from 'vitest'
import { takeFresh } from './sync'

describe('свежий документ из опроса', () => {
  it('подставляется, когда проект подняли, а своей несохранённой правки нет', () => {
    expect(takeFresh(3, 4, false)).toBe(true)
  })

  it('не подставляется поверх несохранённой правки: опрос не должен её стирать', () => {
    expect(takeFresh(3, 4, true)).toBe(false)
  })

  it('старый или тот же ответ ничего не меняет', () => {
    expect(takeFresh(4, 4, false)).toBe(false)
    expect(takeFresh(5, 4, false)).toBe(false)
  })
})
