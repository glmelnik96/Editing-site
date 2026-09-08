import { describe, expect, it } from 'vitest'
import { nameReady } from './newproject'

describe('имя нового проекта', () => {
  it('пустое и одни пробелы — нельзя создать', () => {
    expect(nameReady('')).toBe(false)
    expect(nameReady('   ')).toBe(false)
  })
  it('после обрезки хотя бы один знак — можно', () => {
    expect(nameReady('Ролик')).toBe(true)
    expect(nameReady('  а  ')).toBe(true)
  })
})
