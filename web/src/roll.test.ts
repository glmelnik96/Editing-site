import { describe, expect, it } from 'vitest'
import { FPS_ITEMS, nextRollFold, rollSummary } from './roll'

describe('блок «Ролик»', () => {
  it('сводка называет пропорцию и вписывание', () => {
    expect(rollSummary({ aspect: '16:9', fit: 'pad' })).toBe('Таймлайн · 16:9 · поля')
    expect(rollSummary({ aspect: '9:16', fit: 'crop' })).toBe('Таймлайн · 9:16 · обрезка')
  })

  it('без выбора раскрыт, с выбором свёрнут', () => {
    expect(nextRollFold(null, false)).toEqual({ picked: false, open: true })
    expect(nextRollFold(null, true)).toEqual({ picked: true, open: false })
  })

  it('ручное положение держится, пока выбор не сменится с пустого на непустой или обратно', () => {
    const opened = { picked: true, open: true }
    expect(nextRollFold(opened, true)).toBe(opened)
    expect(nextRollFold(opened, false)).toEqual({ picked: false, open: true })
    const folded = { picked: false, open: false }
    expect(nextRollFold(folded, false)).toBe(folded)
    expect(nextRollFold(folded, true)).toEqual({ picked: true, open: false })
  })

  it('кадры в секунду — те, что принимает сборка', () => {
    expect(FPS_ITEMS.map(item => item.value)).toEqual(['25', '30', '50', '60'])
  })
})
