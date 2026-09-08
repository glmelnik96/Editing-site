import { describe, expect, it } from 'vitest'
import {
  convertFormatsFor,
  convertHint,
  convertJobText,
  conversionsListHtml,
  emptyConvertHtml,
  formatChipsHtml,
} from './convert'

describe('convert screen helpers', () => {
  it('даёт mp4 и webm только видео', () => {
    expect(convertFormatsFor('video')).toEqual([
      'mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg', 'mp4', 'webm',
    ])
    expect(convertFormatsFor('audio')).toEqual(['mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg'])
  })

  it('пустой склад — ссылка в записи, без чипов', () => {
    const html = emptyConvertHtml()
    expect(html).toContain('#/files')
    expect(html).toContain('Сначала загрузите запись')
    expect(html).not.toContain('data-format')
  })

  it('чипы видео содержат webm, у звука — нет', () => {
    expect(formatChipsHtml('video', 'mp3', false)).toContain('data-format="webm"')
    expect(formatChipsHtml('audio', 'mp3', false)).not.toContain('data-format="webm"')
    expect(formatChipsHtml('audio', 'mp3', false)).not.toContain('data-format="mp4"')
  })

  it('во время задания чипы неактивны', () => {
    expect(formatChipsHtml('video', 'mp3', true)).toContain('disabled')
  })

  it('список готовых показывает имя исходника и срок', () => {
    const html = conversionsListHtml([
      {
        id: 'cnv_1',
        original_name: 'Нарезка.mp4',
        format: 'mp3',
        size: 1024,
        duration: 60,
        expires_at: '2099-01-02T03:04:00.000Z',
        download: '/files/u/assets/ast_1/conversions/cnv_1.mp3',
      },
    ])
    expect(html).toContain('Нарезка.mp4')
    expect(html).toContain('Скачать')
    expect(html).toContain('до ')
    expect(html).toContain('data-drop-conversion="cnv_1"')
  })

  it('поясняет звук и черновик видео', () => {
    expect(convertHint()).toMatch(/звук/)
    expect(convertHint()).toMatch(/mp4/)
    expect(convertJobText('running', 0.4)).toContain('%')
  })
})
