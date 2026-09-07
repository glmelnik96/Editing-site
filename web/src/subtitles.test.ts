import { describe, expect, it } from 'vitest'
import type { Project } from './project'
import { burnEnabled, cueTrouble, cuesReady, patchCues, plural, sameSubtitleView, splitCue } from './subtitles'

const cue = (start: number, end: number, text = 'раз два три четыре') => ({ start, end, text })

describe('счётчик реплик', () => {
  it('не спотыкается о падеж', () => {
    expect(plural(1)).toBe('1 реплика')
    expect(plural(3)).toBe('3 реплики')
    expect(plural(5)).toBe('5 реплик')
    expect(plural(11)).toBe('11 реплик')
    expect(plural(21)).toBe('21 реплика')
    expect(plural(112)).toBe('112 реплик')
  })
})

describe('разрезание реплики', () => {
  it('делит время пополам, а слова поровну', () => {
    const out = splitCue([cue(0, 4)], 0)
    expect(out).toHaveLength(2)
    expect(out[0]).toEqual({ start: 0, end: 2, text: 'раз два' })
    expect(out[1]).toEqual({ start: 2, end: 4, text: 'три четыре' })
  })

  it('слишком короткую не режет: половинки было бы не прочесть', () => {
    const one = [cue(0, 0.1)]
    expect(splitCue(one, 0)).toBe(one)
  })

  it('реплику из одного слова оставляет с текстом в первой половине', () => {
    const out = splitCue([cue(0, 2, 'слово')], 0)
    expect(out[0].text).toBe('слово')
    expect(out[1].text).toBe('…')
  })
})

describe('подсказка о негодной реплике', () => {
  it('молчит, когда всё в порядке', () => {
    expect(cueTrouble([cue(0, 2)], 0, 10)).toBe('')
  })

  it('видит наложение на предыдущую', () => {
    expect(cueTrouble([cue(0, 3), cue(2, 5)], 1, 10)).toBe('налезает на предыдущую')
  })

  it('видит выезд за конец ролика', () => {
    expect(cueTrouble([cue(12, 14)], 0, 10)).toBe('начинается после конца ролика')
  })

  it('видит перевёрнутое время и пустой текст', () => {
    expect(cueTrouble([cue(3, 1)], 0, 10)).toBe('конец раньше начала')
    expect(cueTrouble([cue(0, 1, '   ')], 0, 10)).toBe('пустой текст')
  })

  it('видит перебор по длине и по строкам', () => {
    expect(cueTrouble([cue(0, 1, 'я'.repeat(201))], 0, 10)).toBe('длиннее 200 знаков')
    expect(cueTrouble([cue(0, 1, 'раз\nдва\nтри')], 0, 10)).toBe('больше двух строк')
  })
})

describe('когда карточки незачем пересобирать', () => {
  const view = (cues = [cue(0, 2)], mode: 'burn' | 'soft' = 'burn'): Project =>
    ({
      id: 'p', name: 'x', version: 1, status: 'draft', created_at: '', updated_at: '', finished_at: null,
      doc: {
        output: { aspect: '16:9', fit: 'pad', fps: 30 }, clips: [], music: null,
        subtitles: { source: 'cues', asset_id: null, mode, style: 'default', cues },
      },
    }) as Project

  it('не перерисовывает карточки, когда изменились только клипы', () => {
    const a = view()
    const b = { ...a, version: 2, doc: { ...a.doc, clips: [{ id: 'c1' }] } } as Project
    expect(sameSubtitleView(a, b)).toBe(true)
  })

  it('перерисовывает, когда реплики действительно изменились', () => {
    expect(sameSubtitleView(view([cue(0, 2)]), view([cue(0, 2, 'другое')]))).toBe(false)
    expect(sameSubtitleView(view([cue(0, 2)], 'burn'), view([cue(0, 2)], 'soft'))).toBe(false)
  })

  it('не пересобирает карточки из-за галочки', () => {
    const on = view([cue(0, 2)])
    const off = {
      ...on,
      doc: { ...on.doc, subtitles: { ...on.doc.subtitles!, enabled: false } },
    } as Project
    expect(sameSubtitleView(on, off)).toBe(true)
  })

  it('первый приход проекта не считается тем же видом, что пустой экран', () => {
    const безРеплик = { ...view(), doc: { ...view().doc, subtitles: null } }
    expect(sameSubtitleView(null, безРеплик)).toBe(false)
    expect(sameSubtitleView(null, null)).toBe(true)
  })
})

describe('галочка над шкалой', () => {
  it('серая, пока реплик нет', () => {
    expect(cuesReady(null)).toBe(false)
    expect(burnEnabled(null)).toBe(false)
  })

  it('после сборки включена, даже если ключа ещё нет', () => {
    const subs = { source: 'cues' as const, asset_id: null, mode: 'burn' as const,
      style: 'default', cues: [cue(0, 2)] }
    expect(cuesReady(subs)).toBe(true)
    expect(burnEnabled(subs)).toBe(true)
  })

  it('снятая галочка не включает текст карточки обратно', () => {
    const previous = { source: 'cues' as const, asset_id: null, mode: 'burn' as const,
      style: 'default', enabled: false, cues: [cue(0, 2, 'старое')] }
    const next = patchCues(previous, [cue(0, 2, 'новое')])
    expect(next.enabled).toBe(false)
    expect(next.cues![0].text).toBe('новое')
  })
})
