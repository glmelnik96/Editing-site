/**
 * Стопка состояний для отмены и «Вернуть».
 *
 * Живёт в памяти страницы и не переживает перезагрузку: это отмена действия, а не история проекта.
 * Для долгого хранения есть версии на сервере. Пятьдесят шагов: пяти не хватало на пару минут
 * монтажа, а пятьдесят документов даже у проекта из ста клипов — меньше мегабайта.
 */
export function createHistory<T>(limit = 50) {
  let past: T[] = []
  let future: T[] = []
  return {
    /** Запомнить состояние ДО правки. Новая правка обрывает «Вернуть»: возвращать уже нечего. */
    push(state: T): void {
      past.push(state)
      if (past.length > limit) past = past.slice(past.length - limit)
      future = []
    },
    /** Прошлое состояние; текущее уходит в «Вернуть». null — отменять нечего. */
    undo(current: T): T | null {
      const previous = past.pop()
      if (previous === undefined) return null
      future.push(current)
      return previous
    },
    /** Отменённое состояние; текущее снова становится прошлым. null — возвращать нечего. */
    redo(current: T): T | null {
      const next = future.pop()
      if (next === undefined) return null
      past.push(current)
      return next
    },
    canUndo(): boolean {
      return past.length > 0
    },
    canRedo(): boolean {
      return future.length > 0
    },
    size(): number {
      return past.length
    },
    clear(): void {
      past = []
      future = []
    },
  }
}
