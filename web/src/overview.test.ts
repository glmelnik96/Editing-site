import { describe, expect, it } from 'vitest'
import { othersOnly, ownerLabel, usageHtml, type PersonUse, type TeamAsset } from './overview'

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

describe('место по людям', () => {
  it('имя или почта, место и число записей', () => {
    const rows: PersonUse[] = [
      { email: 'two@ya.ru', name: 'Второй', bytes: 1_073_741_824, records: 3 },
      { email: 'one@ya.ru', name: '', bytes: 1024, records: 0 },
    ]
    const html = usageHtml(rows)
    expect(html).toContain('Второй')
    expect(html).toContain('1.0 ГБ · записей: 3')
    expect(html).toContain('one@ya.ru')
    expect(html).toContain('1.0 КБ · записей: 0')
  })

  it('пустой список так и говорит, а не рисует нули', () => {
    expect(usageHtml([])).toContain('На диске пока ничего нет')
  })
})

describe('подпись владельца', () => {
  it('называет по имени, а без имени — по почте', () => {
    expect(ownerLabel({ owner_email: 'one@ya.ru', owner_name: 'Первый' })).toBe('Первый')
    expect(ownerLabel({ owner_email: 'one@ya.ru', owner_name: '   ' })).toBe('one@ya.ru')
    expect(ownerLabel({ owner_email: 'one@ya.ru', owner_name: '' })).toBe('one@ya.ru')
  })
})

describe('только чужое', () => {
  it('убирает своё: оно и так стоит в своём списке прямо над этим', () => {
    const list = [
      asset({ id: 'a1', owner_email: 'me@ya.ru' }),
      asset({ id: 'a2', owner_email: 'two@ya.ru' }),
      asset({ id: 'a3', owner_email: 'three@ya.ru' }),
    ]
    expect(othersOnly(list, 'me@ya.ru').map(a => a.id)).toEqual(['a2', 'a3'])
  })

  it('почту сравнивает без регистра и пробелов, как её хранит сервер', () => {
    const list = [asset({ id: 'a1', owner_email: 'Me@Ya.ru' }), asset({ id: 'a2', owner_email: 'two@ya.ru' })]
    expect(othersOnly(list, ' me@ya.ru ').map(a => a.id)).toEqual(['a2'])
  })
})
