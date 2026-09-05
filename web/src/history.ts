/**
 * Стопка последних состояний для отмены действия.
 *
 * Живёт в памяти страницы и не переживает перезагрузку: это отмена действия, а не история проекта.
 * Для долгого хранения есть точки сохранения на сервере.
 */

export function createHistory<T>(limit = 5) {
  let items: T[] = []
  return {
    /** Запомнить состояние ДО правки. Самое старое вытесняется. */
    push(state: T): void {
      items.push(state)
      if (items.length > limit) items = items.slice(items.length - limit)
    },
    /** Последнее запомненное состояние или null, если откатывать нечего. */
    undo(): T | null {
      return items.pop() ?? null
    },
    canUndo(): boolean {
      return items.length > 0
    },
    size(): number {
      return items.length
    },
    clear(): void {
      items = []
    },
  }
}
