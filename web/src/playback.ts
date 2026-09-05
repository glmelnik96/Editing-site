/**
 * Состояние воспроизведения склейки: какой клип играет, когда переключаться, какая громкость музыки.
 *
 * Здесь нет DOM: функции решают, что делать, а драйвер в редакторе двигает элементы video и audio.
 * Так логика шва проверяется тестами, а не глазами.
 */
import { clipAt, clipDuration, ms, timelineStart, totalDuration, type Clip } from './timeline/model'

export type SeekPlan = { index: number; assetId: string; time: number; timelineTime: number }
export type StepPlan =
  | { kind: 'playing'; timelineTime: number }
  | { kind: 'advance'; index: number; assetId: string; time: number; timelineTime: number }
  | { kind: 'end'; timelineTime: number }

/** Следующий клип и его точка входа: скрытый элемент video готовит его заранее. */
export function nextClip(clips: Clip[], index: number): { index: number; assetId: string; at: number } | null {
  const next = clips[index + 1]
  if (!next) return null
  return { index: index + 1, assetId: next.asset_id, at: next.in }
}

/** Куда встать при перемотке на время шкалы. */
export function seekPlan(clips: Clip[], timelineTime: number): SeekPlan | null {
  const found = clipAt(clips, timelineTime)
  if (found === null) return null
  return {
    index: found.index,
    assetId: found.clip.asset_id,
    time: ms(found.clip.in + found.offset),
    timelineTime: ms(timelineTime),
  }
}

/**
 * Что делать на очередном тике: играем дальше, переключаемся на следующий клип или закончили.
 * Сравнение с точкой выхода нестрогое: элемент video редко попадает в неё точно.
 */
export function stepPlan(clips: Clip[], at: { index: number; sourceTime: number }): StepPlan {
  const current = clips[at.index]
  if (!current) return { kind: 'end', timelineTime: totalDuration(clips) }
  const played = Math.min(clipDuration(current), Math.max(0, at.sourceTime - current.in))
  const timelineTime = ms(timelineStart(clips, at.index) + played)
  if (at.sourceTime < current.out) return { kind: 'playing', timelineTime }
  const next = nextClip(clips, at.index)
  if (next === null) return { kind: 'end', timelineTime: totalDuration(clips) }
  return { kind: 'advance', index: next.index, assetId: next.assetId, time: next.at, timelineTime }
}

/** Громкость музыки в момент ролика с учётом затуханий. Затухания не перекрывают друг друга. */
export function musicVolume(
  music: { volume: number; fade_in: number; fade_out: number } | null,
  timelineTime: number,
  total: number,
): number {
  if (!music) return 0
  const half = total / 2
  const fadeIn = Math.min(music.fade_in, half)
  const fadeOut = Math.min(music.fade_out, half)
  let gain = music.volume
  if (fadeIn > 0 && timelineTime < fadeIn) gain *= timelineTime / fadeIn
  const fromEnd = total - timelineTime
  if (fadeOut > 0 && fromEnd < fadeOut) gain *= Math.max(0, fromEnd) / fadeOut
  return Math.max(0, Math.min(1, gain))
}

const ASPECTS: Record<string, number> = { '16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1 }

/** Пропорция кадра вывода числом; неизвестное значение считаем широким. */
export function aspectRatio(aspect: string): number {
  return ASPECTS[aspect] ?? ASPECTS['16:9']
}
