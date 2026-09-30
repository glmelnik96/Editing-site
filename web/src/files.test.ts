import { describe, expect, it } from 'vitest'
import type { Asset } from './assets'
import { acceptAttr, assetMetaText, dropAssetQuestion, formatRows, inUseText } from './files'

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

const rec = (over: Partial<Asset> = {}): Asset =>
  ({
    id: 'ast_1',
    kind: 'video',
    original_name: 'встреча.mp4',
    size: 1_048_576,
    status: 'proxy_ready',
    duration: 65,
    error: null,
    owner_email: 'liza@ya.ru',
    owner_name: 'Лиза',
    files: { proxy: null, thumbs: null, thumbs_meta: null, peaks: null, analysis: null, vtt: null, transcript: null },
    ...over,
  }) as Asset

describe('запись в общем списке', () => {
  it('называет автора, а у своей пишет «вы»', () => {
    expect(assetMetaText(rec(), 'gleb@ya.ru')).toMatch(/^Лиза · /)
    expect(assetMetaText(rec({ owner_email: 'gleb@ya.ru' }), 'gleb@ya.ru')).toMatch(/^вы · /)
    expect(assetMetaText(rec({ kind: 'image', duration: null }), 'gleb@ya.ru')).toContain('картинка')
  })

  it('перед удалением чужой называет автора и что уйдёт вместе с ней', () => {
    const ask = dropAssetQuestion(rec(), 'gleb@ya.ru')
    expect(ask).toContain('(автор — Лиза)')
    expect(ask).toContain('расшифровка')
    expect(dropAssetQuestion(rec({ owner_email: 'gleb@ya.ru' }), 'gleb@ya.ru')).not.toContain('автор')
  })

  it('отказ «стоит в проекте» называет проекты и их авторов', () => {
    const projects = [{ id: 'prj_1', name: 'Планёрка', owner_email: 'liza@ya.ru', owner_name: 'Лиза' }]
    expect(inUseText(projects, 'gleb@ya.ru')).toContain('«Планёрка» (Лиза)')
  })
})
