import { describe, expect, it } from 'vitest'
import { formatTimecode, parseTimecode } from './timecode'

describe('разбор таймкода', () => {
  it('принимает голые секунды', () => {
    expect(parseTimecode('12')).toBe(12)
    expect(parseTimecode('12.5')).toBe(12.5)
    expect(parseTimecode('120.25')).toBe(120.25)
    expect(parseTimecode('0')).toBe(0)
  })

  it('принимает минуты и часы', () => {
    expect(parseTimecode('1:23')).toBe(83)
    expect(parseTimecode('1:23.5')).toBe(83.5)
    expect(parseTimecode('01:02:03')).toBe(3723)
    expect(parseTimecode('1:02:03.4')).toBe(3723.4)
  })

  it('принимает запятую как разделитель дробной части', () => {
    expect(parseTimecode('1:23,5')).toBe(83.5)
    expect(parseTimecode('12,25')).toBe(12.25)
  })

  it('терпит пробелы по краям', () => {
    expect(parseTimecode('  1:23  ')).toBe(83)
  })

  it('отвергает мусор, а не подставляет ноль', () => {
    for (const bad of ['', '   ', 'нет', '1:2:3:4', '1:60', '1:23:60', '-5', '1..2', '::', '1:', 'e5']) {
      expect(parseTimecode(bad)).toBeNull()
    }
  })

  it('округляет до миллисекунды', () => {
    expect(parseTimecode('1.23456')).toBe(1.235)
  })
})

describe('показ таймкода', () => {
  it('показывает минуты и секунды с десятыми', () => {
    expect(formatTimecode(0)).toBe('0:00.0')
    expect(formatTimecode(83.5)).toBe('1:23.5')
    expect(formatTimecode(9.04)).toBe('0:09.0')
  })

  it('добавляет часы, когда они есть', () => {
    expect(formatTimecode(3723.45)).toBe('1:02:03.5')
    expect(formatTimecode(3600)).toBe('1:00:00.0')
  })

  it('не порождает шестидесятую секунду при округлении', () => {
    expect(formatTimecode(59.98)).toBe('1:00.0')
    expect(formatTimecode(3599.99)).toBe('1:00:00.0')
  })

  it('отрицательное и нечисло показывает нулём', () => {
    expect(formatTimecode(-5)).toBe('0:00.0')
    expect(formatTimecode(Number.NaN)).toBe('0:00.0')
  })

  it('разбор и показ сходятся друг с другом', () => {
    for (const value of [0, 1.5, 83.5, 3723.4]) {
      expect(parseTimecode(formatTimecode(value))).toBe(value)
    }
  })
})
