import { describe, expect, it } from 'vitest'
import type { Asset } from './assets'
import type { JobListItem, ProjectDoc, RenderOptions } from './project'
import {
  estimateRenderBytes,
  estimateRenderMinutes,
  fmtBitrate,
  outputSize,
  renderLabel,
  renderSummary,
  runningRenderFromJobs,
  sizeOptions,
  sourceStats,
  sourcesLine,
} from './render'

const doc: ProjectDoc = {
  output: { aspect: '9:16', fit: 'pad', fps: 30 },
  clips: [],
  sounds: [
    { id: 'bg', asset_id: 'ast_m', at: 0, in: 0, out: 30, volume: 0.25, loop: true, duck: true, fade_in: 0, fade_out: 0 },
  ],
  subtitles: {
    source: 'cues',
    asset_id: null,
    mode: 'burn',
    style: 'default',
    enabled: true,
    cues: [{ start: 0, end: 2, text: 'Начали' }],
  },
}

const opts = (over: Partial<RenderOptions> = {}): RenderOptions => ({
  quality: 'high',
  format: 'mp4',
  short_side: 1080,
  bitrate_kbps: 4000,
  ...over,
})

const clip = (id: string, asset: string) => ({
  id,
  asset_id: asset,
  in: 0,
  out: 4,
  volume: 1,
  snap_to_pauses: false,
  in_verified: false,
  out_verified: false,
})

const asset = (over: Partial<Asset>): Asset =>
  ({
    id: 'ast_v',
    kind: 'video',
    original_name: 'a.mp4',
    size: 100_000_000,
    status: 'proxy_ready',
    duration: 100,
    width: 1920,
    height: 1080,
    bit_rate: 8_000_000,
    error: null,
    files: { proxy: null, thumbs: null, thumbs_meta: null, peaks: null, analysis: null, vtt: null, transcript: null },
    ...over,
  }) as Asset

describe('оценка времени сборки', () => {
  it('считает минуты как ceil(длительность / k), не меньше одной', () => {
    expect(estimateRenderMinutes(720, 'final')).toBe(12)
    expect(estimateRenderMinutes(720, 'draft')).toBe(9)
    expect(estimateRenderMinutes(4, 'draft')).toBe(1)
    expect(estimateRenderMinutes(0, 'final')).toBe(1)
  })

  it('превью собирается как черновик, высокое — как финал', () => {
    expect(estimateRenderMinutes(720, 'preview')).toBe(estimateRenderMinutes(720, 'draft'))
    expect(estimateRenderMinutes(720, 'high')).toBe(estimateRenderMinutes(720, 'final'))
  })

  it('webm честно дольше, а звук без картинки — за секунды', () => {
    expect(estimateRenderMinutes(720, 'high', 'webm')).toBe(24)
    expect(estimateRenderMinutes(720, 'high', 'm4a')).toBe(1)
  })
})

describe('вес файла', () => {
  it('предсказуем у целевого качества и у звука', () => {
    expect(estimateRenderBytes(10, opts({ quality: 'target', bitrate_kbps: 4000 }))).toBe(5_200_000)
    expect(estimateRenderBytes(10, opts({ format: 'm4a' }))).toBe(240_000)
  })

  it('у постоянного качества не выдумывается: он зависит от картинки', () => {
    expect(estimateRenderBytes(10, opts({ quality: 'medium' }))).toBeNull()
    expect(estimateRenderBytes(10, opts({ quality: 'high' }))).toBeNull()
  })
})

describe('разрешение по пропорции', () => {
  it('считает кадр той же формулой, что сервер', () => {
    // Числа сверены с настоящими сборками: 854×480 и 1080×1920 вышли из ffmpeg именно такими.
    expect(outputSize('16:9', 720)).toEqual({ width: 1280, height: 720 })
    expect(outputSize('16:9', 480)).toEqual({ width: 854, height: 480 })
    expect(outputSize('9:16', 1080)).toEqual({ width: 1080, height: 1920 })
    expect(outputSize('1:1', 720)).toEqual({ width: 720, height: 720 })
    expect(outputSize('16:9', 2160)).toEqual({ width: 3840, height: 2160 })
  })

  it('предупреждает, что кадр больше исходников', () => {
    const options = sizeOptions('16:9', 720)
    expect(options.find(o => o.short === 720)?.label).toBe('1280×720')
    expect(options.find(o => o.short === 1080)?.label).toBe('1920×1080 — больше исходников')
    // Неизвестные исходники — не повод пугать: молчим.
    expect(sizeOptions('16:9', null).every(o => !o.label.includes('больше'))).toBe(true)
  })
})

describe('подпись об исходниках', () => {
  it('берёт самый плотный битрейт и самый крупный кадр среди клипов', () => {
    const withClips = { ...doc, clips: [clip('c1', 'ast_a'), clip('c2', 'ast_b')] }
    const stats = sourceStats(withClips, [
      asset({ id: 'ast_a', bit_rate: 3_000_000, width: 1280, height: 720 }),
      asset({ id: 'ast_b', bit_rate: 8_200_000, width: 1920, height: 1080 }),
      asset({ id: 'ast_c', bit_rate: 50_000_000 }), // лежит в записях, но не в клипах
    ])
    expect(stats).toEqual({ maxBitrate: 8_200_000, maxShort: 1080 })
    expect(sourcesLine(stats)).toBe('Исходники: кадр до 1080p, битрейт до 8.2 Мбит/с.')
  })

  it('у записей старше колонки битрейта берёт вес файла на длительность', () => {
    const withClips = { ...doc, clips: [clip('c1', 'ast_old')] }
    const stats = sourceStats(withClips, [asset({ id: 'ast_old', bit_rate: null, size: 50_000_000, duration: 100 })])
    expect(stats.maxBitrate).toBe(4_000_000)
  })

  it('картинки не в счёт: битрейта у кадра нет', () => {
    const withClips = { ...doc, clips: [clip('c1', 'ast_pic')] }
    const stats = sourceStats(withClips, [asset({ id: 'ast_pic', kind: 'image', bit_rate: null, duration: null })])
    expect(stats).toEqual({ maxBitrate: null, maxShort: null })
    expect(sourcesLine(stats)).toBe('')
  })

  it('битрейт пишет в понятных единицах', () => {
    expect(fmtBitrate(8_200_000)).toBe('8.2 Мбит/с')
    expect(fmtBitrate(640_000)).toBe('640 кбит/с')
  })
})

describe('сводка вкладки Рендер', () => {
  it('говорит человеческим языком, без argv', () => {
    const text = renderSummary(doc, opts(), 720)
    expect(text).toContain('Высокое: 1080×1920, 9:16, поля, 30 к/с, mp4.')
    expect(text).toContain('Звуковая дорожка: 1 звук.')
    expect(text).toContain('Фон приглушается под речь')
    expect(text).toContain('Субтитры впечатаны в кадр')
    expect(text).toContain('Около 12 мин, если воркер свободен')
    expect(text).not.toContain('ffmpeg')
    expect(text).not.toContain('-filter_complex')
  })

  it('у целевого качества называет битрейт и вес', () => {
    const text = renderSummary({ ...doc, sounds: [], subtitles: null }, opts({ quality: 'target' }), 10)
    expect(text).toContain('Целевое: 1080×1920, 9:16, поля, 30 к/с, mp4, 4000 кбит/с.')
    expect(text).toContain('файл около')
  })

  it('у только звука нет ни кадра, ни субтитров', () => {
    const text = renderSummary(doc, opts({ format: 'm4a' }), 60)
    expect(text).toContain('Только звук: m4a, 192 кбит/с.')
    expect(text).not.toContain('Субтитры')
    expect(text).not.toContain('×')
  })

  it('без дакинга не обещает приглушение', () => {
    const text = renderSummary(
      { ...doc, sounds: doc.sounds!.map(s => ({ ...s, duck: false })), subtitles: { ...doc.subtitles!, mode: 'soft' } },
      opts(),
      60,
    )
    expect(text).toContain('Звуковая дорожка')
    expect(text).not.toContain('приглушается')
    expect(text).toContain('Субтитры отдельной дорожкой')
  })

  it('без реплик не обещает субтитры, даже если галочка стоит', () => {
    // Реплики удаляют по одной, и документ остаётся с enabled: true и пустым списком.
    const empty = renderSummary({ ...doc, subtitles: { ...doc.subtitles!, cues: [] } }, opts(), 60)
    expect(empty).not.toContain('Субтитры')
  })

  it('называет переходы и звуковую дорожку, если они есть', () => {
    const withFade = {
      ...doc,
      clips: [clip('c1', 'a'), { ...clip('c2', 'a'), transition: { kind: 'fade' as const, duration: 0.5 } }],
      sounds: [
        { id: 's1', asset_id: 'x', at: 0, in: 0, out: 1, volume: 1, loop: false, duck: false, fade_in: 0, fade_out: 0 },
        { id: 's2', asset_id: 'x', at: 2, in: 0, out: 1, volume: 1, loop: false, duck: false, fade_in: 0, fade_out: 0 },
      ],
    }
    const text = renderSummary(withFade, opts({ quality: 'preview', short_side: 480 }), 5.5)
    expect(text).toContain('Переход между клипами')
    expect(text).toContain('Звуковая дорожка: 2 звука.')
  })
})

describe('подпись готового ролика', () => {
  const card = {
    id: 'rnd_1',
    project_id: 'prj_1',
    size: 1,
    duration: 1,
    created_at: 'x',
    expires_at: 'x',
    download: '/x',
  }

  it('называет качество, кадр и формат', () => {
    expect(renderLabel({ ...card, quality: 'high', format: 'webm', width: 1280, height: 720 })).toBe(
      'высокое · 1280×720 · webm',
    )
  })

  it('старый ролик без записанного кадра — просто его качество и mp4', () => {
    expect(renderLabel({ ...card, quality: 'final' })).toBe('финал · mp4')
  })
})

describe('наложения в сводке', () => {
  it('замедляют сборку, но не звук без картинки', () => {
    expect(estimateRenderMinutes(720, 'high', 'mp4', 2)).toBe(21)
    expect(estimateRenderMinutes(720, 'high', 'mp4', 10)).toBe(estimateRenderMinutes(720, 'high', 'mp4', 4))
    expect(estimateRenderMinutes(720, 'high', 'm4a', 3)).toBe(1)
  })

  it('называются в сводке, когда есть', () => {
    const overlay = { id: 'o1', asset_id: 'x', at: 0, in: 0, out: 2, place: 'tr' as const, size: 30, volume: 0, fade_in: 0, fade_out: 0 }
    const text = renderSummary({ ...doc, overlays: [overlay, { ...overlay, id: 'o2' }] }, opts(), 60)
    expect(text).toContain('Поверх основы: 2 наложения.')
    expect(renderSummary(doc, opts(), 60)).not.toContain('Поверх основы')
  })
})

describe('подхват идущей сборки', () => {
  const listed = (over: Partial<JobListItem>): JobListItem => ({
    id: 'job_1',
    type: 'render',
    status: 'running',
    progress: 0.5,
    error: null,
    created_at: '2026-09-11T10:00:00Z',
    finished_at: null,
    label: 'Ролик',
    cancelable: true,
    quality: 'medium',
    target_id: 'prj_1',
    owner_email: 'li@example.com',
    owner_name: 'Лиза',
    ...over,
  })

  it('находит живую сборку своего проекта; чужие проекты и другие задания не берёт', () => {
    const jobs = [
      listed({ id: 'job_other', target_id: 'prj_2' }),
      listed({ id: 'job_conv', type: 'convert' }),
      listed({ id: 'job_done', status: 'done' }),
      listed({ id: 'job_mine' }),
    ]
    expect(runningRenderFromJobs(jobs, 'prj_1')?.id).toBe('job_mine')
    expect(runningRenderFromJobs(jobs, 'prj_3')).toBeNull()
  })

  it('из нескольких берёт самую раннюю: она и выполняется', () => {
    const jobs = [
      listed({ id: 'job_late', status: 'queued', created_at: '2026-09-11T10:05:00Z' }),
      listed({ id: 'job_early', created_at: '2026-09-11T10:00:00Z' }),
    ]
    expect(runningRenderFromJobs(jobs, 'prj_1')?.id).toBe('job_early')
  })
})
