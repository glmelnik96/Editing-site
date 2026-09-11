/**
 * Почему кнопка серая — одна правда на весь редактор.
 *
 * Серая кнопка раньше молчала: `disabled` глотает нажатие, и человек не узнавал, чего не хватает.
 * Теперь кнопка остаётся серой, но нажимается и говорит причину. Правило «гасим, а не прячем»
 * прежнее: место кнопки постоянно, меняется только доступность.
 */

export type PickedKind = 'none' | 'clip' | 'sound' | 'overlay'

export type EditorState = {
  hasClips: boolean
  picked: PickedKind
  canUndo: boolean
  canRedo: boolean
  cuesReady: boolean
}

export type EditorBlocks = {
  play: string | null
  split: string | null
  zoom: string | null
  duplicate: string | null
  remove: string | null
  undo: string | null
  redo: string | null
  burn: string | null
}

export const EMPTY_TIMELINE = 'Шкала пустая — сначала добавьте кусок из «Исходников»'

export function editorBlocks(s: EditorState): EditorBlocks {
  const empty = s.hasClips ? null : EMPTY_TIMELINE
  return {
    play: empty,
    split: empty,
    zoom: empty,
    duplicate:
      s.picked === 'none' ? 'Выберите клип на шкале' : s.picked === 'clip' ? null : 'Звук и наложение пока не дублируются',
    remove: s.picked === 'none' ? 'Выберите клип, звук или наложение на шкале' : null,
    undo: s.canUndo ? null : 'Отменять пока нечего',
    redo: s.canRedo ? null : 'Возвращать нечего',
    burn: s.cuesReady ? null : 'Сначала сделайте субтитры во вкладке «Субтитры»',
  }
}

export type SourceState = { hasFile: boolean; longEnough: boolean; overlayable: boolean }

export function sourceBlocks(s: SourceState): { add: string | null; over: string | null } {
  const add = !s.hasFile
    ? 'Сначала выберите файл выше'
    : !s.longEnough
      ? 'Кусок короче 0.1 с — раздвиньте границы'
      : null
  return { add, over: add ?? (s.overlayable ? null : 'Поверх кладутся только картинки и видео') }
}

export function tabBlock(enabled: boolean): string | null {
  return enabled ? null : 'Появится, когда на шкале будет клип'
}

/** Серая кнопка с причиной. Прежняя подсказка кнопки возвращается, когда причина уходит. */
export function setBlocked(button: HTMLElement, reason: string | null): void {
  if (button.dataset.plainTitle === undefined) button.dataset.plainTitle = button.title
  button.setAttribute('aria-disabled', String(reason !== null))
  button.dataset.reason = reason ?? ''
  button.title = reason ?? button.dataset.plainTitle
}

/** Причина, если кнопка серая; null — можно нажимать. */
export function blockedReason(button: HTMLElement): string | null {
  return button.getAttribute('aria-disabled') === 'true' ? button.dataset.reason || null : null
}
