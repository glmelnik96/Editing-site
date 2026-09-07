import { describe, expect, it } from 'vitest'
import type { ProjectDoc } from './project'
import { estimateRenderMinutes, renderSummary } from './render'

const doc: ProjectDoc = {
  output: { aspect: '9:16', fit: 'pad', fps: 30 },
  clips: [],
  music: {
    asset_id: 'ast_m',
    volume: 0.25,
    fade_in: 0,
    fade_out: 0,
    loop: true,
    duck: true,
    speech_volume: 1,
  },
  subtitles: { source: 'cues', asset_id: null, mode: 'burn', style: 'default', enabled: true, cues: [] },
}

describe('оценка времени сборки', () => {
  it('считает минуты как ceil(длительность / k), не меньше одной', () => {
    expect(estimateRenderMinutes(720, 'final')).toBe(12)
    expect(estimateRenderMinutes(720, 'draft')).toBe(9)
    expect(estimateRenderMinutes(4, 'draft')).toBe(1)
    expect(estimateRenderMinutes(0, 'final')).toBe(1)
  })
})

describe('сводка вкладки Рендер', () => {
  it('говорит человеческим языком, без argv', () => {
    const text = renderSummary(doc, 'final', 720)
    expect(text).toContain('Финал: 1080p, 9:16, поля, 30 к/с')
    expect(text).toContain('Музыка с приглушением под речь')
    expect(text).toContain('Субтитры вжжены')
    expect(text).toContain('Около 12 мин, если воркер свободен')
    expect(text).not.toContain('ffmpeg')
    expect(text).not.toContain('-filter_complex')
  })

  it('черновик называет 720p и быстрее', () => {
    const text = renderSummary({ ...doc, music: null, subtitles: null }, 'draft', 60)
    expect(text).toContain('Черновик: 720p')
    expect(text).toContain('пресет быстрее')
    expect(text).not.toContain('Музыка')
    expect(text).not.toContain('Субтитры')
  })

  it('без дакинга не обещает приглушение', () => {
    const text = renderSummary(
      { ...doc, music: { ...doc.music!, duck: false }, subtitles: { ...doc.subtitles!, mode: 'soft' } },
      'final',
      60,
    )
    expect(text).toContain('Музыка')
    expect(text).not.toContain('приглушением')
    expect(text).toContain('Субтитры отдельной дорожкой')
  })
})
