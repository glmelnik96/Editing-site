import { describe, expect, it } from 'vitest'
import type { Sound } from '../project'
import {
  moveSound,
  newSoundId,
  removeSound,
  setSoundVolume,
  soundBlocks,
  soundsEnd,
} from './sounds'

const sound = (over: Partial<Sound> = {}): Sound => ({
  id: 's1',
  asset_id: 'ast_1',
  at: 2,
  in: 10,
  out: 14,
  volume: 1,
  ...over,
})

describe('раскладка звуков', () => {
  it('ставит звук по его собственному времени, а не встык за соседом', () => {
    const blocks = soundBlocks([sound({ at: 2 }), sound({ id: 's2', at: 3 })], 10)
    // Второй начинается на 3 с, внутри первого: у звуков нет очереди, они могут перекрываться.
    expect(blocks).toEqual([
      { id: 's1', left: 20, width: 40 },
      { id: 's2', left: 30, width: 40 },
    ])
  })

  it('короткий звук не уже порога указателя', () => {
    expect(soundBlocks([sound({ in: 0, out: 0.1 })], 10)[0].width).toBe(8)
  })

  it('шкала тянется до хвоста самого позднего звука', () => {
    expect(soundsEnd([sound({ at: 2 }), sound({ id: 's2', at: 30, in: 0, out: 5 })])).toBe(35)
    expect(soundsEnd([])).toBe(0)
  })
})

describe('правка звуков', () => {
  it('перекладывает звук и не пускает его раньше начала ролика', () => {
    expect(moveSound([sound()], 's1', 7.12345)[0].at).toBe(7.123)
    expect(moveSound([sound()], 's1', -3)[0].at).toBe(0)
  })

  it('не трогает соседей ни при переносе, ни при удалении', () => {
    const list = [sound(), sound({ id: 's2', at: 9 })]
    expect(moveSound(list, 's1', 20)[1]).toBe(list[1])
    expect(removeSound(list, 's1')).toEqual([list[1]])
  })

  it('держит громкость в пределах клипа', () => {
    expect(setSoundVolume([sound()], 's1', 5)[0].volume).toBe(2)
    expect(setSoundVolume([sound()], 's1', -1)[0].volume).toBe(0)
  })

  it('имя нового звука не совпадает ни с клипом, ни со звуком', () => {
    expect(newSoundId([], [])).toMatch(/^s1_[0-9a-z]+$/)
    const clips = [{ id: 'c1' }]
    const sounds = [{ id: 's1' }, { id: 's2_abc' }]
    const names = new Set(Array.from({ length: 200 }, () => newSoundId(clips, sounds)))
    // Двести вызовов подряд на одном и том же списке — как два быстрых добавления до его
    // обновления — не дают повторов и ни разу не попадают в занятое имя.
    expect(names.size).toBe(200)
    for (const name of names) {
      expect(name).toMatch(/^s3_[0-9a-z]+$/)
      expect(['c1', 's1', 's2_abc']).not.toContain(name)
    }
  })
})
