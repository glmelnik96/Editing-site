import { describe, expect, it } from 'vitest'
import { HOTKEYS, needsClip, shortcutFor, type KeyLike } from './hotkeys'

const press = (code: string, over: Partial<KeyLike> = {}): KeyLike => ({
  code,
  key: '',
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  altKey: false,
  ...over,
})

describe('разбор нажатия', () => {
  it('узнаёт клавиши правки', () => {
    expect(shortcutFor(press('KeyS'))).toBe('split')
    expect(shortcutFor(press('Delete'))).toBe('remove')
    expect(shortcutFor(press('Backspace'))).toBe('remove')
    expect(shortcutFor(press('BracketLeft'))).toBe('trimIn')
    expect(shortcutFor(press('BracketRight'))).toBe('trimOut')
    expect(shortcutFor(press('Escape'))).toBe('deselect')
  })

  it('Ctrl и Cmd — одно и то же', () => {
    expect(shortcutFor(press('KeyD', { ctrlKey: true }))).toBe('duplicate')
    expect(shortcutFor(press('KeyD', { metaKey: true }))).toBe('duplicate')
    expect(shortcutFor(press('KeyC', { ctrlKey: true }))).toBe('copy')
    expect(shortcutFor(press('KeyV', { metaKey: true }))).toBe('paste')
    expect(shortcutFor(press('KeyZ', { ctrlKey: true }))).toBe('undo')
  })

  it('с модификатором буква значит другое, чем без него', () => {
    // S без Ctrl режет клип, с Ctrl — это «сохранить» браузера, и перехватывать его мы не лезем.
    expect(shortcutFor(press('KeyS'))).toBe('split')
    expect(shortcutFor(press('KeyS', { ctrlKey: true }))).toBeNull()
    expect(shortcutFor(press('KeyD'))).toBeNull()
  })

  it('Alt у стрелок — прыжок по границам, Shift оставляем самому редактору', () => {
    expect(shortcutFor(press('ArrowLeft'))).toBe('back')
    expect(shortcutFor(press('ArrowLeft', { altKey: true }))).toBe('edgeBack')
    expect(shortcutFor(press('ArrowRight', { altKey: true }))).toBe('edgeForward')
    // Shift меняет шаг, но не смысл: разбор отдаёт то же действие.
    expect(shortcutFor(press('ArrowRight', { shiftKey: true }))).toBe('forward')
  })

  it('по клипам ходят стрелками вверх и вниз', () => {
    expect(shortcutFor(press('ArrowUp'))).toBe('prevClip')
    expect(shortcutFor(press('ArrowDown'))).toBe('nextClip')
  })

  it('Tab не трогаем: им ходят по элементам страницы', () => {
    // Отняв Tab, мы отняли бы возможность работать с редактором с клавиатуры вообще.
    expect(shortcutFor(press('Tab'))).toBeNull()
    expect(shortcutFor(press('Tab', { shiftKey: true }))).toBeNull()
  })

  it('масштаб берём и с основного ряда, и с цифрового блока', () => {
    expect(shortcutFor(press('Equal'))).toBe('zoomIn')
    expect(shortcutFor(press('NumpadAdd'))).toBe('zoomIn')
    expect(shortcutFor(press('Minus'))).toBe('zoomOut')
    expect(shortcutFor(press('NumpadSubtract'))).toBe('zoomOut')
  })

  it('«?» ловим по напечатанному знаку, а не по клавише', () => {
    // На ЙЦУКЕН «?» — это Shift+7, а физическая Slash печатает точку. По коду клавиши подсказка
    // не открывалась бы вовсе и открывалась бы на точку.
    expect(shortcutFor(press('Slash', { key: '?', shiftKey: true }))).toBe('help')
    expect(shortcutFor(press('Digit7', { key: '?', shiftKey: true }))).toBe('help')
    expect(shortcutFor(press('Slash', { key: '.' }))).toBeNull()
    expect(shortcutFor(press('Period', { key: '.' }))).toBeNull()
  })

  it('незнакомую клавишу не выдумывает', () => {
    expect(shortcutFor(press('KeyQ'))).toBeNull()
    expect(shortcutFor(press('F5'))).toBeNull()
  })
})

describe('подсказка', () => {
  it('называет клип там, где он нужен', () => {
    expect(needsClip('remove')).toBe(true)
    expect(needsClip('duplicate')).toBe(true)
    expect(needsClip('trimOut')).toBe(true)
    expect(needsClip('play')).toBe(false)
    expect(needsClip('split')).toBe(false)
  })

  it('перечисляет все действия, которые понимает разбор', () => {
    // Список для человека и разбор для машины расходятся молча: подсказка обещала бы клавишу,
    // которой нет, или молчала бы о работающей.
    expect(HOTKEYS.length).toBeGreaterThan(10)
    expect(HOTKEYS.every(row => row.keys.trim() && row.what.trim())).toBe(true)
  })
})
