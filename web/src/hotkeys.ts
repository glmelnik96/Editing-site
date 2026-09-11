/**
 * Горячие клавиши редактора: разбор нажатия и список для подсказки.
 *
 * Разбор отдельно от DOM, потому что раскладка — это грабли. Смотрим на `code` (физическую
 * клавишу), а не на `key`: с русской раскладкой `key` у той же кнопки будет «с» вместо «s»,
 * и половина сочетаний молча перестала бы работать. Из `key` берём только то, чего в `code`
 * нет по смыслу.
 */

export type Shortcut =
  | 'play'
  | 'back'
  | 'forward'
  | 'start'
  | 'end'
  | 'edgeBack'
  | 'edgeForward'
  | 'undo'
  | 'redo'
  | 'split'
  | 'remove'
  | 'duplicate'
  | 'copy'
  | 'paste'
  | 'trimIn'
  | 'trimOut'
  | 'prevClip'
  | 'nextClip'
  | 'zoomIn'
  | 'zoomOut'
  | 'deselect'
  | 'help'

export type KeyLike = {
  code: string
  /** Напечатанный знак. Нужен ровно там, где важен сам символ, а не физическая клавиша. */
  key: string
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
  altKey: boolean
}

/** Ctrl на Windows и Cmd на маке — одно и то же действие, различать их незачем. */
const cmd = (event: KeyLike): boolean => event.ctrlKey || event.metaKey

export function shortcutFor(event: KeyLike): Shortcut | null {
  // Единственное место, где смотрим на напечатанный знак, а не на клавишу: «?» приходит с разных
  // кнопок — на латинской раскладке это Shift+Slash, на ЙЦУКЕН Shift+7, — а сама Slash на ЙЦУКЕН
  // печатает точку. По `code` подсказка не открывалась бы вовсе и открывалась бы на точку.
  if (event.key === '?' && !cmd(event)) return 'help'
  if (cmd(event)) {
    switch (event.code) {
      case 'KeyZ':
        return event.shiftKey ? 'redo' : 'undo'
      case 'KeyY':
        return 'redo'
      case 'KeyD':
        return 'duplicate'
      case 'KeyC':
        return 'copy'
      case 'KeyV':
        return 'paste'
      default:
        return null
    }
  }
  switch (event.code) {
    case 'Space':
      return 'play'
    case 'ArrowLeft':
      return event.altKey ? 'edgeBack' : 'back'
    case 'ArrowRight':
      return event.altKey ? 'edgeForward' : 'forward'
    case 'Home':
      return 'start'
    case 'End':
      return 'end'
    case 'KeyS':
      return 'split'
    case 'Delete':
    case 'Backspace':
      return 'remove'
    case 'BracketLeft':
      return 'trimIn'
    case 'BracketRight':
      return 'trimOut'
    // По клипам ходят стрелками вверх и вниз, как в монтажных программах. Tab сюда не годится:
    // это клавиша перехода по элементам страницы, и отняв её, мы отняли бы у человека
    // возможность работать с редактором с клавиатуры вообще.
    case 'ArrowUp':
      return 'prevClip'
    case 'ArrowDown':
      return 'nextClip'
    case 'Equal':
    case 'NumpadAdd':
      return 'zoomIn'
    case 'Minus':
    case 'NumpadSubtract':
      return 'zoomOut'
    case 'Escape':
      return 'deselect'
    default:
      return null
  }
}

/** Клавиши, которым нужен выбранный клип: без него честнее сказать, чем молча ничего не сделать. */
const NEEDS_CLIP = new Set<Shortcut>(['remove', 'duplicate', 'copy', 'trimIn', 'trimOut'])

export function needsClip(what: Shortcut): boolean {
  return NEEDS_CLIP.has(what)
}

export type HotkeyRow = { keys: string; what: string }

/** Подсказка для человека. Порядок — от частого к редкому, а не по алфавиту. */
export const HOTKEYS: HotkeyRow[] = [
  { keys: 'Пробел', what: 'играть или встать' },
  { keys: '← →', what: 'курсор на секунду (с Shift — на 0.1 с)' },
  { keys: 'Alt + ← →', what: 'к границе соседнего клипа' },
  { keys: 'Home / End', what: 'в начало и в конец ролика' },
  { keys: 'S', what: 'разрезать клип по курсору' },
  { keys: 'Del / Backspace', what: 'удалить выбранный клип' },
  { keys: 'Ctrl + D', what: 'дублировать клип — копия встаёт следом' },
  { keys: 'Ctrl + C / Ctrl + V', what: 'скопировать клип и вставить за выбранным' },
  { keys: '[ / ]', what: 'подрезать начало или конец клипа под курсор' },
  { keys: '↑ ↓', what: 'выбрать предыдущий или следующий клип' },
  { keys: '+ / −', what: 'крупнее и мельче' },
  { keys: 'Esc', what: 'снять выделение' },
  { keys: 'Ctrl + Z', what: 'отменить последнее действие' },
  { keys: 'Ctrl + Shift + Z / Ctrl + Y', what: 'вернуть отменённое' },
  { keys: '?', what: 'эта подсказка' },
]
