import { describe, expect, it } from 'vitest'
import { convertFormatsFor, convertHint, convertJobText, convertPanelHtml } from './files'

describe('convert menu on a recording card', () => {
  it('offers mp4 only for video', () => {
    expect(convertFormatsFor('video')).toEqual(['mp3', 'm4a', 'wav', 'mp4'])
    expect(convertFormatsFor('audio')).toEqual(['mp3', 'm4a', 'wav'])
  })

  it('explains that sound is quick and mp4 is like a draft render', () => {
    expect(convertHint()).toBe(
      'извлечь звук займёт секунды; mp4 — примерно как черновик сборки этой длительности',
    )
  })

  it('draws Конвертер only when the asset is ready', () => {
    const html = convertPanelHtml({
      assetId: 'ast_1',
      kind: 'video',
      ready: true,
      job: null,
      conversions: [],
    })
    expect(html).toContain('Конвертер')
    expect(html).toContain('data-convert="mp4"')
    expect(html).toContain(convertHint())
    expect(
      convertPanelHtml({ assetId: 'ast_1', kind: 'video', ready: false, job: null, conversions: [] }),
    ).toBe('')
  })

  it('hides the mp4 button on an audio card', () => {
    const html = convertPanelHtml({
      assetId: 'ast_1',
      kind: 'audio',
      ready: true,
      job: null,
      conversions: [],
    })
    expect(html).toContain('data-convert="mp3"')
    expect(html).not.toContain('data-convert="mp4"')
  })

  it('shows progress, cancel, and ready files with an until-date', () => {
    const html = convertPanelHtml({
      assetId: 'ast_1',
      kind: 'video',
      ready: true,
      job: { status: 'running', progress: 0.4 },
      conversions: [
        {
          id: 'cnv_1',
          format: 'mp3',
          size: 1024,
          duration: 60,
          expires_at: '2099-01-02T03:04:00.000Z',
          download: '/files/u/assets/ast_1/conversions/cnv_1.mp3',
        },
      ],
    })
    expect(html).toContain(convertJobText('running', 0.4))
    expect(html).toContain('data-cancel-convert="ast_1"')
    expect(html).toContain('Скачать')
    expect(html).toContain('до ')
    expect(html).toContain('data-drop-conversion="cnv_1"')
  })
})
