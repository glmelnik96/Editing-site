import { describe, expect, it } from 'vitest'
import type { Clip } from './timeline/model'
import { aspectRatio, incomingAt, musicVolume, nextClip, previewClipVolume, previewSpeechGain, resumePlan, seekPlan, stepPlan } from './playback'

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

describe('музыка', () => {
  it('затухает на входе и на выходе', () => {
    const music = { volume: 0.8, fade_in: 2, fade_out: 2 }
    expect(musicVolume(music, 0, 10)).toBeCloseTo(0)
    expect(musicVolume(music, 1, 10)).toBeCloseTo(0.4)
    expect(musicVolume(music, 5, 10)).toBeCloseTo(0.8)
    expect(musicVolume(music, 9, 10)).toBeCloseTo(0.4)
    expect(musicVolume(music, 10, 10)).toBeCloseTo(0)
  })

  it('без затуханий держит громкость ровно', () => {
    expect(musicVolume({ volume: 0.5, fade_in: 0, fade_out: 0 }, 0, 10)).toBe(0.5)
    expect(musicVolume(null, 1, 10)).toBe(0)
  })

  it('короткий ролик не даёт затуханиям наложиться', () => {
    const music = { volume: 1, fade_in: 5, fade_out: 5 }
    const middle = musicVolume(music, 1, 2)
    expect(middle).toBeGreaterThan(0)
    expect(middle).toBeLessThanOrEqual(1)
  })

  it('в речи приглушает музыку, в паузе оставляет', () => {
    const music = { volume: 1, fade_in: 0, fade_out: 0, duck: true }
    const silences = [{ start: 2, end: 4 }]
    expect(musicVolume(music, 1, 10, { sourceTime: 1, silences })).toBeCloseTo(0.3)
    expect(musicVolume(music, 3, 10, { sourceTime: 3, silences })).toBeCloseTo(1)
  })

  it('без карты пауз дакинг не трогает громкость', () => {
    const music = { volume: 0.5, fade_in: 0, fade_out: 0, duck: true }
    expect(musicVolume(music, 1, 10)).toBe(0.5)
    expect(musicVolume(music, 1, 10, null)).toBe(0.5)
  })

  it('пустая карта пауз — вся речь, множитель 0.3', () => {
    const music = { volume: 1, fade_in: 0, fade_out: 0, duck: true }
    expect(musicVolume(music, 1, 10, { sourceTime: 1, silences: [] })).toBeCloseTo(0.3)
  })

  it('дакинг умножает уже посчитанный fade', () => {
    const music = { volume: 0.8, fade_in: 2, fade_out: 0, duck: true }
    // на 1 с из 2 с затухания gain = 0.4, речь → 0.12
    expect(musicVolume(music, 1, 10, { sourceTime: 0.5, silences: [] })).toBeCloseTo(0.12)
  })

  it('без флага duck карта пауз не действует', () => {
    const music = { volume: 0.5, fade_in: 0, fade_out: 0, duck: false }
    expect(musicVolume(music, 1, 10, { sourceTime: 1, silences: [] })).toBe(0.5)
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

  it('умножает на ползунок речи и тоже режет потолок', () => {
    expect(previewClipVolume(1, 0.5)).toBe(0.5)
    expect(previewClipVolume(0.4, 0.5)).toBeCloseTo(0.2)
    expect(previewClipVolume(2, 0.4)).toBeCloseTo(0.8)
    expect(previewClipVolume(1.7, 1)).toBe(1)
  })

  it('без музыки речь не приглушает', () => {
    expect(previewSpeechGain(null)).toBe(1)
    expect(previewSpeechGain(undefined)).toBe(1)
    expect(previewSpeechGain({ speech_volume: 0.3 })).toBe(0.3)
    expect(previewSpeechGain({})).toBe(1)
  })
})
