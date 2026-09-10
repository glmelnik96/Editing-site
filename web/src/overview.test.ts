import { describe, expect, it } from 'vitest'
import { diskByOwner, ownerLabel, type TeamAsset } from './overview'

const asset = (over: Partial<TeamAsset> = {}): TeamAsset => ({
  id: 'ast_1',
  original_name: 'встреча.mp4',
  owner_email: 'one@ya.ru',
  owner_name: 'Первый',
  kind: 'video',
  status: 'ready',
  size: 100,
  duration: 60,
  created_at: '2026-01-01T00:00:00.000Z',
  ...over,
})

describe('итог по людям', () => {
  it('складывает файлы одного человека и ставит тяжёлых выше', () => {
    const rows = diskByOwner([
      asset({ id: 'a1', size: 100 }),
      asset({ id: 'a2', owner_email: 'two@ya.ru', owner_name: 'Второй', size: 900 }),
      asset({ id: 'a3', size: 50 }),
    ])
    expect(rows.map(r => [r.email, r.bytes, r.count])).toEqual([
      ['two@ya.ru', 900, 1],
      ['one@ya.ru', 150, 2],
    ])
  })

  it('на пустом списке молчит, а не рисует нули', () => {
    expect(diskByOwner([])).toEqual([])
  })
})

describe('подпись владельца', () => {
  it('называет по имени, а без имени — по почте', () => {
    expect(ownerLabel({ owner_email: 'one@ya.ru', owner_name: 'Первый' })).toBe('Первый')
    expect(ownerLabel({ owner_email: 'one@ya.ru', owner_name: '   ' })).toBe('one@ya.ru')
    expect(ownerLabel({ owner_email: 'one@ya.ru', owner_name: '' })).toBe('one@ya.ru')
  })
})
