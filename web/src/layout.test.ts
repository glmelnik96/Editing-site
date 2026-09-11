import { describe, expect, it } from 'vitest'
import { COLUMN_GAP, PROPS_MAX, PROPS_MIN, SIDE_MIN, laneSizes, lanesHeight, sideWidths, stageBox } from './layout'

describe('сцена в верхнем ряду', () => {
  it('на 1280×800 упирается в высоту ряда', () => {
    expect(stageBox(1232, 298, 16 / 9)).toEqual({ width: 530, height: 298 })
  })

  it('в высоком окне упирается в ширину между минимальными колонками', () => {
    const room = 1232 - 2 * COLUMN_GAP - SIDE_MIN - PROPS_MIN
    expect(stageBox(1232, 600, 16 / 9)).toEqual({ width: room, height: Math.round((room * 9) / 16) })
  })

  it('вертикальный ролик узкий, высота та же', () => {
    expect(stageBox(1232, 298, 9 / 16)).toEqual({ width: 168, height: 298 })
  })

  it('без места — нулевой размер, а не отрицательный', () => {
    expect(stageBox(400, -10, 16 / 9)).toEqual({ width: 0, height: 0 })
  })
})

describe('боковые колонки', () => {
  it('свойствам 45 % остатка, исходникам — остальное', () => {
    expect(sideWidths(1232, 530)).toEqual({ side: 368, props: 302 })
  })

  it('свойства не шире 380 и не уже 240', () => {
    expect(sideWidths(1872, 700).props).toBe(PROPS_MAX)
    expect(sideWidths(1232, 680).props).toBe(PROPS_MIN)
  })

  it('колонки, сцена и зазоры занимают всю ширину', () => {
    const { side, props } = sideWidths(1488, 505)
    expect(side + props + 505 + 2 * COLUMN_GAP).toBe(1488)
  })
})

describe('дорожки шкалы', () => {
  it('обычные — как было', () => {
    const s = laneSizes(false)
    expect([s.overlay, s.track, s.audio, s.sound]).toEqual([52, 76, 52, 52])
    expect([s.clipBlock, s.clipFrame, s.audioBlock, s.laneBlock]).toEqual([72, 90, 44, 44])
  })

  it('низкие отдают сцене 56 px', () => {
    const s = laneSizes(true)
    expect([s.overlay, s.track, s.audio, s.sound]).toEqual([36, 60, 44, 36])
    expect(lanesHeight(laneSizes(false)) - lanesHeight(s)).toBe(56)
  })

  it('блок ниже своей колеи, кадр клипа — в прежней пропорции 90 к 72', () => {
    for (const s of [laneSizes(false), laneSizes(true)]) {
      expect(s.clipBlock).toBeLessThan(s.track)
      expect(s.audioBlock).toBeLessThan(s.audio)
      expect(s.laneBlock).toBeLessThan(Math.min(s.overlay, s.sound))
      expect(s.clipFrame / s.clipBlock).toBeCloseTo(90 / 72, 2)
    }
  })
})
