/**
 * Таймкод: разбор введённого человеком и показ на экране.
 *
 * Принимаем то, что человек реально печатает: голые секунды, минуты с секундами, часы с минутами,
 * запятую вместо точки (на русской раскладке она под рукой). Мусор возвращает null: подставить
 * вместо непонятного ввода ноль — значит молча увести курсор в начало.
 */

// Дробная часть без ограничения на число цифр: точность до миллисекунды наводит округление ниже,
// а не сама форма — иначе '1.23456' отвергался бы целиком вместо округления до 1.235.
const SHAPE = /^\d+(?::\d{1,2}){0,2}(?:\.\d+)?$/

/** Секунды из строки или null, если разобрать не вышло. */
export function parseTimecode(text: string): number | null {
  const cleaned = (text ?? '').trim().replace(',', '.')
  if (!SHAPE.test(cleaned)) return null
  const parts = cleaned.split(':')
  const numbers = parts.map(Number)
  if (numbers.some(n => !Number.isFinite(n))) return null
  // Минуты и секунды в составном таймкоде обязаны быть меньше шестидесяти: «1:60» — опечатка.
  if (parts.length > 1 && numbers.slice(1).some(n => n >= 60)) return null
  const seconds = numbers.reduce((total, part) => total * 60 + part, 0)
  return Math.round(seconds * 1000) / 1000
}

/** Секунды в «1:23.5» или «1:02:03.5». Отрицательное и нечисло показываем нулём. */
export function formatTimecode(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0
  // Округляем до десятых заранее: иначе 59.98 превратилось бы в «0:60.0».
  const tenths = Math.round(safe * 10)
  const whole = Math.floor(tenths / 10)
  const rest = tenths % 10
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor((whole % 3600) / 60)
  const secs = whole % 60
  const tail = `${String(secs).padStart(2, '0')}.${rest}`
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${tail}` : `${minutes}:${tail}`
}
