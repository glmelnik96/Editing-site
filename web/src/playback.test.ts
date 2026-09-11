import { describe, expect, it } from 'vitest'
import type { Sound } from './project'
import type { Clip } from './timeline/model'
import {
  aspectRatio,
  incomingAt,
  nextClip,
  previewClipVolume,
  resumePlan,
  seekPlan,
  stepPlan,
  soundPlan,
  duckFactor,
  fadeGain,
} from './playback'

function clip(id: string, inS: number, outS: number, asset = 'ast_1'): Clip {
  return {
    id,
    asset_id: asset,
    in: inS,
    out: outS,
    volume: 1,
    snap_to_pauses: false,
    in_verified: false,
    out_verified: false,
  }
}

const clips = [clip('c1', 0, 4), clip('c2', 10, 12, 'ast_2'), clip('c3', 1, 4.5)]

describe('переходы между клипами', () => {
  it('знает следующий клип и его точку входа', () => {
    expect(nextClip(clips, 0)).toEqual({ index: 1, assetId: 'ast_2', at: 10 })
    expect(nextClip(clips, 2)).toBeNull()
    expect(nextClip([], 0)).toBeNull()
  })

  it('считает, куда перемотать при переходе на время шкалы', () => {
    expect(seekPlan(clips, 0)).toEqual({ index: 0, assetId: 'ast_1', time: 0, timelineTime: 0 })
    expect(seekPlan(clips, 4.5)).toEqual({ index: 1, assetId: 'ast_2', time: 10.5, timelineTime: 4.5 })
    expect(seekPlan(clips, 99)).toBeNull()
    expect(seekPlan([], 0)).toBeNull()
  })

  it('на шаге внутри клипа просто обновляет время шкалы', () => {
    const plan = stepPlan(clips, { index: 0, sourceTime: 2.5 })
    expect(plan).toEqual({ kind: 'playing', timelineTime: 2.5 })
  })

  it('на достижении точки выхода переключает клип', () => {
    expect(stepPlan(clips, { index: 0, sourceTime: 4 })).toEqual({
      kind: 'advance',
      index: 1,
      assetId: 'ast_2',
      time: 10,
      timelineTime: 4,
    })
    expect(stepPlan(clips, { index: 0, sourceTime: 4.2 })).toMatchObject({ kind: 'advance', index: 1 })
  })

  it('после последнего клипа останавливается', () => {
    expect(stepPlan(clips, { index: 2, sourceTime: 4.5 })).toEqual({ kind: 'end', timelineTime: 9.5 })
  })

  it('исчезнувший клип не роняет плеер', () => {
    expect(stepPlan(clips, { index: 9, sourceTime: 1 })).toEqual({ kind: 'end', timelineTime: 9.5 })
  })

  it('на обрезе последнего клипа пробел больше не играет хвост исходника', () => {
    expect(resumePlan(clips, { index: 2, sourceTime: 4.5 })).toEqual({ kind: 'stop', timelineTime: 9.5 })
    expect(resumePlan(clips, { index: 2, sourceTime: 5 })).toEqual({ kind: 'stop', timelineTime: 9.5 })
  })

  it('внутри клипа пробел продолжает, на шве — следующий кусок', () => {
    expect(resumePlan(clips, { index: 0, sourceTime: 2.5 })).toEqual({ kind: 'play' })
    expect(resumePlan(clips, { index: 0, sourceTime: 4 })).toMatchObject({
      kind: 'advance',
      index: 1,
      assetId: 'ast_2',
      time: 10,
    })
  })

  it('во время fade держит оба клипа и mix', () => {
    const faded = [
      clip('c1', 0, 4),
      { ...clip('c2', 10, 12, 'ast_2'), transition: { kind: 'fade' as const, duration: 0.5 } },
    ]
    expect(incomingAt(faded, 3.5)?.mix).toBe(0)
    expect(incomingAt(faded, 3.75)?.mix).toBe(0.5)
    expect(incomingAt(faded, 3.75)?.time).toBe(10.25)
    expect(seekPlan(faded, 3.75)?.incoming?.assetId).toBe('ast_2')
    expect(stepPlan(faded, { index: 0, sourceTime: 3.75 })).toMatchObject({
      kind: 'playing',
      incoming: { mix: 0.5, assetId: 'ast_2' },
    })
    expect(stepPlan(faded, { index: 0, sourceTime: 4 })).toEqual({
      kind: 'advance',
      index: 1,
      assetId: 'ast_2',
      time: 10.5,
      timelineTime: 4,
    })
  })
})

describe('края звука', () => {
  it('появление и затухание — линейно и по краям отрезка', () => {
    expect(fadeGain(0, 10, 2, 2)).toBeCloseTo(0)
    expect(fadeGain(1, 10, 2, 2)).toBeCloseTo(0.5)
    expect(fadeGain(5, 10, 2, 2)).toBeCloseTo(1)
    expect(fadeGain(9, 10, 2, 2)).toBeCloseTo(0.5)
    expect(fadeGain(10, 10, 2, 2)).toBeCloseTo(0)
  })

  it('без затуханий держит ровно, а короткий отрезок не даёт им наложиться', () => {
    expect(fadeGain(3, 10, 0, 0)).toBe(1)
    const middle = fadeGain(1, 2, 5, 5)
    expect(middle).toBeGreaterThan(0)
    expect(middle).toBeLessThanOrEqual(1)
  })
})

describe('приглушение под речь', () => {
  it('в речи тише, в паузе — как есть', () => {
    const silences = [{ start: 2, end: 4 }]
    expect(duckFactor({ sourceTime: 1, silences })).toBeCloseTo(0.3)
    expect(duckFactor({ sourceTime: 3, silences })).toBe(1)
  })

  it('без карты пауз не трогает, пустая карта — вся речь', () => {
    expect(duckFactor(null)).toBe(1)
    expect(duckFactor(undefined)).toBe(1)
    expect(duckFactor({ sourceTime: 1, silences: [] })).toBeCloseTo(0.3)
  })
})

describe('кадр вывода', () => {
  it('переводит пропорцию в число и режим в свойство', () => {
    expect(aspectRatio('16:9')).toBeCloseTo(16 / 9)
    expect(aspectRatio('9:16')).toBeCloseTo(9 / 16)
    expect(aspectRatio('1:1')).toBe(1)
    expect(aspectRatio('что-то')).toBeCloseTo(16 / 9)
  })
})

describe('громкость клипа в превью', () => {
  it('усиливает только до 1', () => {
    expect(previewClipVolume(0)).toBe(0)
    expect(previewClipVolume(0.4)).toBe(0.4)
    expect(previewClipVolume(1)).toBe(1)
    expect(previewClipVolume(1.7)).toBe(1)
  })

})

describe('звуковая дорожка в превью', () => {
  const s = (id: string, at: number, from: number, to: number, over: Partial<Sound> = {}): Sound =>
    ({ id, asset_id: `ast_${id}`, at, in: from, out: to, volume: 0.5, loop: false, duck: false, fade_in: 0, fade_out: 0, ...over })

  it('до своего места звук молчит, внутри играет со своего отрезка записи', () => {
    const list = [s('s1', 2, 10, 14)]
    expect(soundPlan(list, 1.9, 60)).toEqual([])
    expect(soundPlan(list, 3.5, 60)).toEqual([{ id: 's1', assetId: 'ast_s1', time: 11.5, gain: 0.5, duck: false }])
  })

  it('конец звука не входит: на стыке двух звучит только второй', () => {
    const list = [s('s1', 0, 0, 2), s('s2', 2, 0, 2)]
    expect(soundPlan(list, 2, 60).map(c => c.id)).toEqual(['s2'])
  })

  it('перекрывающиеся звуки звучат вместе', () => {
    const list = [s('s1', 0, 0, 5), s('s2', 3, 0, 5)]
    expect(soundPlan(list, 4, 60).map(c => c.id)).toEqual(['s1', 's2'])
  })

  it('хвост за концом ролика не звучит, как и в собранном файле', () => {
    expect(soundPlan([s('s1', 8, 0, 10)], 12, 10)).toEqual([])
  })

  it('звук по кругу идёт до конца ролика, а время внутри записи — по остатку', () => {
    const list = [s('s1', 1, 10, 14, { loop: true })] // кусок 4 с, с 1 с до конца
    expect(soundPlan(list, 6.5, 60)[0].time).toBe(11.5) // 5.5 с внутри → второй круг, 1.5 с
    expect(soundPlan(list, 59, 60)).toHaveLength(1)
    expect(soundPlan(list, 60, 60)).toEqual([])
  })

  it('громкость учитывает появление и затухание, а приглушение только помечает', () => {
    const list = [s('s1', 0, 0, 10, { volume: 1, fade_in: 2, fade_out: 2, duck: true })]
    expect(soundPlan(list, 1, 60)[0].gain).toBeCloseTo(0.5)
    expect(soundPlan(list, 5, 60)[0].gain).toBe(1)
    expect(soundPlan(list, 5, 60)[0].duck).toBe(true)
  })
})
