import { describe, expect, it } from 'vitest'
import {
  convertFormatsFor,
  convertHint,
  convertJobText,
  conversionsListHtml,
  convertibleAsset,
  emptyConvertHtml,
  formatChipsHtml,
  pickConvertFile,
  runningConvertsFromJobs,
} from './convert'

describe('convert screen helpers', () => {
  it('берёт только готовые видео и звук, не субтитры', () => {
    expect(convertibleAsset({ kind: 'video', status: 'ready' })).toBe(true)
    expect(convertibleAsset({ kind: 'audio', status: 'proxy_ready' })).toBe(true)
    expect(convertibleAsset({ kind: 'subtitle', status: 'ready' })).toBe(false)
    expect(convertibleAsset({ kind: 'video', status: 'analyzing' })).toBe(false)
  })

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

  it('подхватывает идущую конвертацию после перезагрузки экрана', () => {
    const rows = runningConvertsFromJobs([
      {
        id: 'job_1',
        type: 'convert',
        status: 'running',
        progress: 0.4,
        error: null,
        created_at: '',
        finished_at: null,
        label: 'a.mp4',
        cancelable: true,
        quality: null,
        target_id: 'ast_1',
        owner_email: 'a@b.c',
        owner_name: 'A',
      },
      {
        id: 'job_2',
        type: 'render',
        status: 'running',
        progress: 0.1,
        error: null,
        created_at: '',
        finished_at: null,
        label: 'Ролик',
        cancelable: true,
        quality: 'draft',
        target_id: 'prj_1',
        owner_email: 'a@b.c',
        owner_name: 'A',
      },
      {
        id: 'job_3',
        type: 'convert',
        status: 'done',
        progress: 1,
        error: null,
        created_at: '',
        finished_at: '',
        label: 'a.mp4',
        cancelable: false,
        quality: null,
        target_id: 'ast_1',
        owner_email: 'a@b.c',
        owner_name: 'A',
      },
    ])
    expect(rows).toEqual([
      {
        assetId: 'ast_1',
        job: { id: 'job_1', type: 'convert', status: 'running', progress: 0.4, error: null },
      },
    ])
  })

  it('после перезагрузки открывает файл, который сейчас конвертируется', () => {
    expect(pickConvertFile('ast_a', ['ast_a', 'ast_b'], ['ast_b'])).toBe('ast_b')
    expect(pickConvertFile('ast_b', ['ast_a', 'ast_b'], ['ast_b'])).toBe('ast_b')
    expect(pickConvertFile('ast_a', ['ast_a', 'ast_b'], [])).toBe('ast_a')
    expect(pickConvertFile('', ['ast_a'], [])).toBe('ast_a')
  })
})
