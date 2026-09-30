import { describe, expect, it } from 'vitest'
import { authorLabel, ownerLabel, usageHtml, type PersonUse } from './overview'

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

describe('подпись автора', () => {
  it('у своего — «вы», у чужого — имя или почта', () => {
    expect(authorLabel({ owner_email: 'Me@Ya.ru', owner_name: 'Я' }, ' me@ya.ru ')).toBe('вы')
    expect(authorLabel({ owner_email: 'two@ya.ru', owner_name: 'Второй' }, 'me@ya.ru')).toBe('Второй')
    expect(authorLabel({ owner_email: 'two@ya.ru', owner_name: '' }, 'me@ya.ru')).toBe('two@ya.ru')
  })
})
