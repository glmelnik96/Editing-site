/**
 * Наложения: картинка или видео поверх основы, со своим временем на шкале и местом в кадре.
 *
 * Разметка и перенос по шкале общие со звуками (свободная колея), здесь — то, что у наложений
 * своё: место в кадре пресетами, размер, появление и исчезновение, и что видно на сцене в
 * данный момент. Коробка считается по тем же правилам, что overlay_box на сервере: иначе превью
 * показывало бы наложение не там, где его положит сборка.
 */
import type { Overlay, OverlayPlace } from '../project'
import { ms, type Clip } from './model'
import { newLaneId, removeItem, type Placed } from './sounds'

export const PLACES: { value: OverlayPlace; label: string }[] = [
  { value: 'full', label: 'весь кадр' },
  { value: 'center', label: 'по центру' },
  { value: 'tl', label: 'слева сверху' },
  { value: 'tr', label: 'справа сверху' },
  { value: 'bl', label: 'слева снизу' },
  { value: 'br', label: 'справа снизу' },
  { value: 'left', label: 'левая половина' },
  { value: 'right', label: 'правая половина' },
]
/** Размер значим только здесь: весь кадр и половины сами задают коробку. */
export const SIZED_PLACES: ReadonlySet<OverlayPlace> = new Set<OverlayPlace>(['center', 'tl', 'tr', 'bl', 'br'])
export const OVERLAY_SIZE_MIN = 5
export const OVERLAY_SIZE_MAX = 100
export const OVERLAY_SIZE_DEFAULT = 30
/** Отступ угловых от края: доля ширины кадра, как OVERLAY_MARGIN на сервере. */
export const OVERLAY_MARGIN = 0.02

export function overlayLength(overlay: Pick<Overlay, 'in' | 'out'>): number {
  return ms(overlay.out - overlay.in)
}

export type Box = { left: number; top: number; width: number; height: number; align: string }

/**
 * Коробка наложения в процентах кадра и выравнивание кадра внутри неё.
 *
 * aspect — ширина кадра к высоте: отступ по вертикали в пикселях тот же, что по горизонтали,
 * а в процентах высоты он от этого больше. Угловые прижимаются к своему углу, остальные —
 * по центру коробки; сам кадр вписывается с сохранением пропорций (object-fit: contain).
 */
export function overlayBox(place: OverlayPlace, size: number, aspect: number): Box {
  const m = OVERLAY_MARGIN * 100
  const my = m * aspect
  switch (place) {
    case 'left':
      return { left: 0, top: 0, width: 50, height: 100, align: 'center center' }
    case 'right':
      return { left: 50, top: 0, width: 50, height: 100, align: 'center center' }
    case 'center':
      return { left: (100 - size) / 2, top: (100 - size) / 2, width: size, height: size, align: 'center center' }
    case 'tl':
      return { left: m, top: my, width: size, height: size, align: 'left top' }
    case 'tr':
      return { left: 100 - m - size, top: my, width: size, height: size, align: 'right top' }
    case 'bl':
      return { left: m, top: 100 - my - size, width: size, height: size, align: 'left bottom' }
    case 'br':
      return { left: 100 - m - size, top: 100 - my - size, width: size, height: size, align: 'right bottom' }
    default:
      // «full» и всё незнакомое из старых документов: во весь кадр честнее, чем никуда.
      return { left: 0, top: 0, width: 100, height: 100, align: 'center center' }
  }
}

export type OverlayCue = { id: string; assetId: string; time: number; opacity: number; volume: number }

/**
 * Что из наложений видно в момент ролика, с какого места своей записи и насколько прозрачно.
 *
 * Хвост за концом ролика не показывается: сборка его обрежет, и увидеть в превью то, чего не
 * будет в файле, хуже, чем не увидеть. Прозрачность — те же появление и исчезновение, что в
 * сборке, линейно.
 */
export function overlayPlan(overlays: Overlay[], timelineTime: number, total: number): OverlayCue[] {
  if (timelineTime >= total) return []
  return overlays
    .filter(overlay => timelineTime >= overlay.at && timelineTime < overlay.at + overlayLength(overlay))
    .map(overlay => {
      const local = timelineTime - overlay.at
      const length = overlayLength(overlay)
      let opacity = 1
      if (overlay.fade_in > 0 && local < overlay.fade_in) opacity = Math.min(opacity, local / overlay.fade_in)
      if (overlay.fade_out > 0 && length - local < overlay.fade_out) {
        opacity = Math.min(opacity, Math.max(0, length - local) / overlay.fade_out)
      }
      return {
        id: overlay.id,
        assetId: overlay.asset_id,
        time: ms(overlay.in + local),
        opacity: Math.round(opacity * 1000) / 1000,
        volume: overlay.volume,
      }
    })
}

/** Правка полей наложения с зажатием в допустимое: сервер отверг бы документ за пределами. */
export function updateOverlay(overlays: Overlay[], id: string, patch: Partial<Overlay>): Overlay[] {
  return overlays.map(overlay => {
    if (overlay.id !== id) return overlay
    const next = { ...overlay, ...patch }
    return {
      ...next,
      size: Math.min(OVERLAY_SIZE_MAX, Math.max(OVERLAY_SIZE_MIN, Math.round(next.size))),
      volume: Math.min(2, Math.max(0, next.volume)),
      fade_in: Math.max(0, next.fade_in),
      fade_out: Math.max(0, next.fade_out),
    }
  })
}

export function removeOverlay(overlays: Overlay[], id: string): Overlay[] {
  return removeItem(overlays, id)
}

/** Имя нового наложения, не занятое ни клипом, ни звуком, ни наложением: выделение у всех общее. */
export function newOverlayId(
  clips: Pick<Clip, 'id'>[],
  sounds: Pick<Placed, 'id'>[],
  overlays: Pick<Overlay, 'id'>[],
): string {
  return newLaneId('o', overlays.length, [...clips, ...sounds, ...overlays].map(item => item.id))
}
