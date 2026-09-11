/**
 * Что сказать поверх сцены.
 *
 * Сцена молчала трижды: чёрный прямоугольник у пустого проекта, пока грузится видео и когда прокси
 * не пришёл вовсе (у видео не было обработчика ошибки). Загрузку показываем только после порога:
 * быстрые переходы между клипами не должны мигать надписью.
 */
import { escapeHtml } from './html'

export const LOADING_DELAY_MS = 300

export type VideoState = 'ready' | 'waiting' | 'error'

export type StageNote =
  | { kind: 'none' }
  | { kind: 'empty'; noRecords: boolean }
  | { kind: 'loading' }
  | { kind: 'error'; name: string }

export function stageNote(s: {
  clipCount: number
  readyRecords: number
  video: VideoState
  waitedMs: number
  name: string | null
}): StageNote {
  if (s.clipCount === 0) return { kind: 'empty', noRecords: s.readyRecords === 0 }
  if (s.video === 'error') return { kind: 'error', name: s.name || 'запись' }
  if (s.video === 'waiting' && s.waitedMs >= LOADING_DELAY_MS) return { kind: 'loading' }
  return { kind: 'none' }
}

export function stageNoteHtml(note: StageNote): string {
  switch (note.kind) {
    case 'empty':
      return (
        '<p><b>Здесь будет ролик.</b> Выберите запись в «Исходниках» слева и нажмите «Добавить в шкалу».</p>' +
        (note.noRecords ? '<p>Записей пока нет — <a href="#/files">загрузите их на экране «Записи»</a>.</p>' : '')
      )
    case 'loading':
      return '<p>Готовлю кадр…</p><i class="stage-bar" aria-hidden="true"></i>'
    case 'error':
      return `<p class="error">Не удалось загрузить «${escapeHtml(note.name)}». Обновите страницу; если повторится — загрузите файл заново.</p>`
    default:
      return ''
  }
}
