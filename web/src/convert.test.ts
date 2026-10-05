import { describe, expect, it } from 'vitest'
import {
  convertFormatsFor,
  convertHint,
  convertJobText,
  conversionsListHtml,
  convertibleAsset,
  emptyConvertHtml,
  formatChipsHtml,
  keepText,
  pickConvertFile,
  runningConvertsFromJobs,
} from './convert'

describe('convert screen helpers', () => {
  it('берёт только готовые видео, звук и картинки, не субтитры', () => {
    expect(convertibleAsset({ kind: 'video', status: 'ready' })).toBe(true)
    expect(convertibleAsset({ kind: 'audio', status: 'proxy_ready' })).toBe(true)
    expect(convertibleAsset({ kind: 'image', status: 'ready' })).toBe(true)
    expect(convertibleAsset({ kind: 'subtitle', status: 'ready' })).toBe(false)
    expect(convertibleAsset({ kind: 'video', status: 'analyzing' })).toBe(false)
  })

  it('даёт mp4 и webm только видео, webp — видео и картинке', () => {
    expect(convertFormatsFor('video')).toEqual([
      'mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg', 'mp4', 'webm', 'webp',
    ])
    expect(convertFormatsFor('audio')).toEqual(['mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg'])
    expect(convertFormatsFor('image')).toEqual(['webp'])
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

  it('подсказка видео: минуты по длительности, кадр до 1080p, срок с сервера', () => {
    const hint = convertHint('mp4', 600, 24)
    expect(hint).toContain('кодируется заново')
    // Тот же расчёт, что у панели сборки при «среднем» качестве: 600 с / 1.04 → 10 мин.
    expect(hint).toContain('около 10 мин')
    expect(hint).toContain('Кадр больше 1080p уменьшится до 1080p')
    expect(hint).toContain('хранится сутки')
  })

  it('webm кодируется вдвое дольше mp4', () => {
    expect(convertHint('webm', 600, 24)).toContain('около 20 мин')
  })

  it('webp из видео — анимация без звука, из картинки — секунды', () => {
    const video = convertHint('webp', 600, 24)
    expect(video).toContain('анимированным WebP без звука')
    expect(video).toContain('720p')
    expect(video).toContain('10 кадров')
    const still = convertHint('webp', null, 24)
    expect(still).toContain('за секунды')
    expect(still).not.toContain('без звука')
  })

  it('подсказка звука — без кадра и минут', () => {
    const hint = convertHint('mp3', 600, 24)
    expect(hint).toContain('за секунды')
    expect(hint).not.toContain('1080')
    expect(hint).toContain('хранится сутки')
  })

  it('срок не доехал с сервера — о нём молчим', () => {
    expect(convertHint('mp4', 60, null)).not.toContain('хранится')
    expect(convertHint('mp3', 60, null)).not.toContain('хранится')
  })

  it('срок словами', () => {
    expect(keepText(24)).toBe('сутки')
    expect(keepText(72)).toBe('3 дня')
    expect(keepText(168)).toBe('7 дней')
    expect(keepText(12)).toBe('12 часов')
    expect(keepText(1)).toBe('1 час')
  })

  it('ход конвертации — в процентах', () => {
    expect(convertJobText('running', 0.4)).toContain('%')
  })

  it('подхватывает свою идущую конвертацию после перезагрузки экрана, чужую — нет', () => {
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
      // Чужая конвертация: очередь общая, но конвертер личный — её не подхватываем.
      {
        id: 'job_4',
        type: 'convert',
        status: 'running',
        progress: 0.2,
        error: null,
        created_at: '',
        finished_at: null,
        label: 'b.mp4',
        cancelable: true,
        quality: null,
        target_id: 'ast_9',
        owner_email: 'x@y.z',
        owner_name: 'X',
      },
    ], 'A@B.c')
    expect(rows).toEqual([
      {
        assetId: 'ast_1',
        job: {
          id: 'job_1',
          type: 'convert',
          status: 'running',
          progress: 0.4,
          error: null,
          created_at: '',
          started_at: undefined,
          format: undefined,
        },
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
