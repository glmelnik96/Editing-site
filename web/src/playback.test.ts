import { describe, expect, it } from 'vitest'
import type { Clip } from './timeline/model'
import { aspectRatio, musicVolume, nextClip, seekPlan, stepPlan } from './playback'

function clip(id: string, inS: number, outS: number, asset = 'ast_1'): Clip {
  return { id, asset_id: asset, in: inS, out: outS, snap_to_pauses: false, in_verified: false, out_verified: false }
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
})

describe('кадр вывода', () => {
  it('переводит пропорцию в число и режим в свойство', () => {
    expect(aspectRatio('16:9')).toBeCloseTo(16 / 9)
    expect(aspectRatio('9:16')).toBeCloseTo(9 / 16)
    expect(aspectRatio('1:1')).toBe(1)
    expect(aspectRatio('что-то')).toBeCloseTo(16 / 9)
  })
})
