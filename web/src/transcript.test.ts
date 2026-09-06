import { describe, expect, it } from 'vitest'
import { flattenWords, selectionBounds, selectionText, wordAtTime, type FlatWord } from './transcript'
import type { TranscriptSegment } from './project'

function segment(over: Partial<TranscriptSegment>): TranscriptSegment {
  return {
    id: 1,
    start: 0,
    end: 1,
    text: 'текст',
    start_verified: true,
    end_verified: true,
    suspect: false,
    ...over,
  }
}

const words: FlatWord[] = [
  { w: 'раз', s: 0, e: 0.5, segment: 0 },
  { w: 'два', s: 0.5, e: 1, segment: 0 },
  { w: 'три', s: 2, e: 2.5, segment: 1 },
]

describe('слова транскрипта одним списком', () => {
  it('собирает слова сегментов подряд и помнит номер сегмента', () => {
    const flat = flattenWords([
      segment({ id: 1, start: 0, end: 1, text: 'раз два', words: [
        { w: 'раз', s: 0, e: 0.5 },
        { w: 'два', s: 0.5, e: 1 },
      ] }),
      segment({ id: 2, start: 2, end: 2.5, text: 'три', words: [{ w: 'три', s: 2, e: 2.5 }] }),
    ])
    expect(flat).toEqual(words)
  })

  it('сегмент без пословных времён даёт одно слово во весь свой текст', () => {
    const flat = flattenWords([segment({ start: 3, end: 5, text: 'чужой транскрипт без слов' })])
    expect(flat).toEqual([{ w: 'чужой транскрипт без слов', s: 3, e: 5, segment: 0 }])
  })

  it('на пустом транскрипте даёт пустой список', () => {
    expect(flattenWords([])).toEqual([])
  })
})

describe('слово по времени плеера', () => {
  it('находит слово внутри его отрезка', () => {
    expect(wordAtTime(words, 0.2)).toBe(0)
    expect(wordAtTime(words, 0.7)).toBe(1)
    expect(wordAtTime(words, 2.4)).toBe(2)
  })

  it('на общей границе выбирает следующее слово, а не предыдущее', () => {
    expect(wordAtTime(words, 0)).toBe(0)
    expect(wordAtTime(words, 0.5)).toBe(1)
  })

  it('в паузе, до первого слова и после последнего не подсвечивает ничего', () => {
    expect(wordAtTime(words, 1.5)).toBe(-1)
    expect(wordAtTime(words, -1)).toBe(-1)
    expect(wordAtTime(words, 99)).toBe(-1)
    expect(wordAtTime([], 0)).toBe(-1)
  })

  it('находит любое слово длинного списка', () => {
    const many: FlatWord[] = Array.from({ length: 1000 }, (_, i) => ({
      w: `с${i}`, s: i * 2, e: i * 2 + 1, segment: 0,
    }))
    for (const index of [0, 1, 499, 998, 999]) {
      expect(wordAtTime(many, many[index].s + 0.5)).toBe(index)
    }
    expect(wordAtTime(many, 1.5)).toBe(-1) // пауза между словами
  })
})

describe('границы выделения', () => {
  it('не зависят от порядка кликов', () => {
    expect(selectionBounds(2, 7)).toEqual({ from: 2, to: 7 })
    expect(selectionBounds(7, 2)).toEqual({ from: 2, to: 7 })
  })

  it('выделение из одного слова — тоже выделение', () => {
    expect(selectionBounds(4, 4)).toEqual({ from: 4, to: 4 })
  })
})

describe('подпись кнопки «Взять кусок»', () => {
  it('склеивает выделенные слова пробелами', () => {
    expect(selectionText(words, 0, 2)).toBe('раз два три')
    expect(selectionText(words, 1, 1)).toBe('два')
  })

  it('длинное выделение сворачивает по краям', () => {
    const many: FlatWord[] = Array.from({ length: 20 }, (_, i) => ({
      w: `слово${i}`, s: i, e: i + 1, segment: 0,
    }))
    expect(selectionText(many, 0, 19)).toBe('слово0 … слово19')
  })

  it('пару слов не сворачивает, даже если она длиннее предела', () => {
    const long: FlatWord[] = [
      { w: 'первое-очень-длинное-слово', s: 0, e: 1, segment: 0 },
      { w: 'второе-очень-длинное-слово', s: 1, e: 2, segment: 0 },
    ]
    expect(selectionText(long, 0, 1)).toBe('первое-очень-длинное-слово второе-очень-длинное-слово')
  })

  it('на пустом выделении даёт пустую строку', () => {
    expect(selectionText(words, 2, 1)).toBe('')
    expect(selectionText([], 0, 0)).toBe('')
  })
})
