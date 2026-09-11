import { describe, expect, it } from 'vitest'
import { fadesFit, selectionKey, selectionTitle, type Selected } from './inspector'

const clip = {
  id: 'c1',
  asset_id: 'a',
  in: 1,
  out: 5,
  volume: 1,
  snap_to_pauses: false,
  in_verified: false,
  out_verified: false,
}
const sound = { id: 's1', asset_id: 'a', at: 0, in: 0, out: 3, volume: 1, loop: false, duck: false, fade_in: 0, fade_out: 0 }
const overlay = { id: 'o1', asset_id: 'a', at: 0, in: 0, out: 2, place: 'tr' as const, size: 30, volume: 0, fade_in: 0, fade_out: 0 }

describe('заголовок панели', () => {
  it('называет вид, имя и длину выбранного', () => {
    expect(selectionTitle({ kind: 'clip', clip, index: 0, hasAudio: true, maxFade: 0 })).toBe('Клип c1 · 4.0 с')
    expect(selectionTitle({ kind: 'sound', sound })).toBe('Звук s1 · 3.0 с')
    expect(selectionTitle({ kind: 'overlay', overlay, isImage: true })).toBe('Наложение o1 · 2.0 с')
    expect(selectionTitle({ kind: 'none' })).toBe('Ничего не выбрано')
  })
})

describe('ключ разметки', () => {
  it('меняется от куска к куску и от того, что у куска есть', () => {
    const withAudio: Selected = { kind: 'clip', clip, index: 1, hasAudio: true, maxFade: 2 }
    const silent: Selected = { kind: 'clip', clip, index: 1, hasAudio: false, maxFade: 2 }
    const first: Selected = { kind: 'clip', clip, index: 0, hasAudio: true, maxFade: 0 }
    expect(selectionKey(withAudio)).not.toBe(selectionKey(silent))
    expect(selectionKey(withAudio)).not.toBe(selectionKey(first))
    expect(selectionKey({ kind: 'overlay', overlay, isImage: true })).not.toBe(
      selectionKey({ kind: 'overlay', overlay, isImage: false }),
    )
  })

  it('не меняется от значений: ползунок под пальцем не должен терять разметку', () => {
    const a: Selected = { kind: 'sound', sound }
    const b: Selected = { kind: 'sound', sound: { ...sound, volume: 0.2, duck: true } }
    expect(selectionKey(a)).toBe(selectionKey(b))
  })
})

describe('затухания', () => {
  it('вместе не длиннее куска и не отрицательные', () => {
    expect(fadesFit(1, 2, 3)).toBe(true)
    expect(fadesFit(2, 2, 3)).toBe(false)
    expect(fadesFit(-1, 0, 3)).toBe(false)
  })
})
