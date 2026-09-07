import { describe, expect, it } from 'vitest'
import { defaultMusic } from './music'

describe('выбор музыки', () => {
  it('при первом треке включает дакинг и ставит громкости из спеки', () => {
    expect(defaultMusic('ast_m')).toEqual({
      asset_id: 'ast_m',
      volume: 0.25,
      speech_volume: 1,
      fade_in: 0,
      fade_out: 0,
      loop: true,
      duck: true,
    })
  })
})
