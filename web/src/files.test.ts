import { describe, expect, it } from 'vitest'
import { acceptAttr, formatRows } from './files'

// Ровно та форма, в какой список приходит с сервера: виды сгруппированы, внутри отсортировано.
const FORMATS = {
  video: ['avi', 'mkv', 'mov', 'mp4'],
  audio: ['m4a', 'mp3', 'wav'],
  image: ['jpg', 'png'],
  subtitle: ['srt', 'vtt'],
}

describe('подпись о форматах', () => {
  it('перечисляет виды в порядке от частого к редкому', () => {
    expect(formatRows(FORMATS).map(r => r.name)).toEqual(['Видео', 'Звук', 'Картинки', 'Субтитры'])
    expect(formatRows(FORMATS)[0].exts).toBe('avi, mkv, mov, mp4')
  })

  it('молчит о видах, которых сервер не назвал', () => {
    // Список приходит с сервера, и однажды он может недосчитаться вида. Пустая строка «Картинки —»
    // выглядела бы поломкой, а её причина — что грузить нечего.
    expect(formatRows({ video: ['mp4'], audio: [], image: [], subtitle: [] })).toEqual([
      { name: 'Видео', exts: 'mp4' },
    ])
    expect(formatRows({})).toEqual([])
  })
})

describe('фильтр окна выбора файла', () => {
  it('собирает расширения всех видов с точкой', () => {
    expect(acceptAttr(FORMATS)).toBe(
      '.avi,.mkv,.mov,.mp4,.m4a,.mp3,.wav,.jpg,.png,.srt,.vtt',
    )
  })

  it('на пустом списке не ставит фильтр вовсе', () => {
    // Пустой accept браузер понимает как «ничего нельзя выбрать»; пустая строка снимает фильтр.
    expect(acceptAttr({})).toBe('')
  })
})
