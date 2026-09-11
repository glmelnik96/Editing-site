/**
 * Раскладка монтажки по высоте окна — чистые числа.
 *
 * Редактор зовёт stageBox и sideWidths при смене окна и пропорции, шкала берёт laneSizes при
 * смене режима дорожек. Всё здесь считают тесты: живых проверок вёрстки нет, jsdom не подключён.
 */

/** Колонка исходников не уже этого; свойства — в этих пределах. */
export const SIDE_MIN = 280
export const PROPS_MIN = 240
export const PROPS_MAX = 380
/** Доля свойств в ширине, оставшейся после сцены: остальное — исходникам, им нужнее. */
export const PROPS_SHARE = 0.45
/** Зазор между колонками сетки — var(--space-2). */
export const COLUMN_GAP = 8

export type Box = { width: number; height: number }

/**
 * Сцена во всю высоту верхнего ряда, ширина — по пропорции. Не помещается между минимальными
 * боковыми колонками — упирается в ширину, и высота тогда меньше ряда.
 */
export function stageBox(gridWidth: number, rowHeight: number, ratio: number): Box {
  const room = Math.max(0, gridWidth - 2 * COLUMN_GAP - SIDE_MIN - PROPS_MIN)
  const height = Math.max(0, rowHeight)
  const width = height * ratio
  if (width <= room) return { width: Math.round(width), height: Math.round(height) }
  return { width: Math.round(room), height: Math.round(room / ratio) }
}

/** Остаток ширины после сцены: свойствам 45 % в пределах 240–380, исходникам — остальное. */
export function sideWidths(gridWidth: number, stageWidth: number): { side: number; props: number } {
  const rest = Math.max(0, gridWidth - 2 * COLUMN_GAP - stageWidth)
  const props = Math.round(Math.min(PROPS_MAX, Math.max(PROPS_MIN, rest * PROPS_SHARE)))
  return { side: Math.max(0, rest - props), props }
}

/** Окно ниже 760 px — низкие дорожки: на ноутбуке с браузером иначе сцене не остаётся места. */
export const COMPACT_QUERY = '(max-height: 759px)'

/**
 * Высоты шкалы. Колея — полоса дорожки, блок — кусок на ней (ниже колеи на отступы), клетка
 * кадра — кадр из спрайта: у клипа он выше блока и срезается его краем, как было всегда.
 *
 * Блоки втрое ниже прежних (клип 24 px вместо 72): шкала отдаёт высоту сцене. Каждая колея выше
 * своего блока на 4 px — блок стоит в ней по центру (`top: 2px` в стилях). Подпись в блоке —
 * одна строка по центру, она помещается и в низком режиме. Волна всегда во всю высоту блока:
 * по ней ищут паузы, чтобы попасть резом в тишину, а не в слово.
 */
export type LaneSizes = {
  overlay: number
  track: number
  audio: number
  sound: number
  clipBlock: number
  clipFrame: number
  audioBlock: number
  laneBlock: number
}

const NORMAL: LaneSizes = {
  overlay: 20, track: 28, audio: 20, sound: 20, clipBlock: 24, clipFrame: 30, audioBlock: 16, laneBlock: 16,
}
const COMPACT: LaneSizes = {
  overlay: 18, track: 24, audio: 18, sound: 18, clipBlock: 20, clipFrame: 25, audioBlock: 14, laneBlock: 14,
}

export function laneSizes(compact: boolean): LaneSizes {
  return compact ? COMPACT : NORMAL
}

/** Отступ с пунктиром между группами колей: margin 4 px и черта 1 px. */
const LANE_GAP = 5

/** Высота колей без линейки, полосы перемотки, рамки и полосы прокрутки. */
export function lanesHeight(s: LaneSizes): number {
  return s.overlay + s.track + s.audio + s.sound + 2 * LANE_GAP
}
