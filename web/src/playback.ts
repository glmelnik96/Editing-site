/**
 * Состояние воспроизведения склейки: какой клип играет, когда переключаться, какая громкость музыки.
 *
 * Здесь нет DOM: функции решают, что делать, а драйвер в редакторе двигает элементы video и audio.
 * Так логика шва проверяется тестами, а не глазами.
 */
import { clipAt, clipDuration, fadeInto, ms, timelineStart, totalDuration, type Clip } from './timeline/model'

export type SeekPlan = { index: number; assetId: string; time: number; timelineTime: number; incoming?: Incoming }
export type Incoming = { index: number; assetId: string; time: number; mix: number }
export type StepPlan =
  | { kind: 'playing'; timelineTime: number; incoming?: Incoming }
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
  const plan: SeekPlan = {
    index: found.index,
    assetId: found.clip.asset_id,
    time: ms(found.clip.in + found.offset),
    timelineTime: ms(timelineTime),
  }
  const incoming = incomingAt(clips, timelineTime)
  if (incoming) plan.incoming = incoming
  return plan
}

/** Вторая картинка во время перехода: mix 0 — ещё предыдущий клип, 1 — уже следующий. */
export function incomingAt(clips: Clip[], timelineTime: number): Incoming | undefined {
  const found = clipAt(clips, timelineTime)
  if (found === null) return undefined
  const nextIndex = found.index + 1
  const next = clips[nextIndex]
  if (!next) return undefined
  const fade = fadeInto(next, nextIndex)
  if (fade <= 0) return undefined
  const start = timelineStart(clips, nextIndex)
  if (timelineTime < start) return undefined
  const mix = Math.min(1, Math.max(0, (timelineTime - start) / fade))
  return {
    index: nextIndex,
    assetId: next.asset_id,
    time: ms(next.in + (timelineTime - start)),
    mix: ms(mix),
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
  if (at.sourceTime < current.out) {
    const incoming = incomingAt(clips, timelineTime)
    return incoming ? { kind: 'playing', timelineTime, incoming } : { kind: 'playing', timelineTime }
  }
  const next = nextClip(clips, at.index)
  if (next === null) return { kind: 'end', timelineTime: totalDuration(clips) }
  const fade = fadeInto(clips[next.index], next.index)
  return {
    kind: 'advance',
    index: next.index,
    assetId: next.assetId,
    time: ms(next.at + fade),
    timelineTime,
  }
}

export type ResumePlan =
  | { kind: 'play' }
  | { kind: 'advance'; index: number; assetId: string; time: number; timelineTime: number }
  | { kind: 'stop'; timelineTime: number }

/**
 * Что делать по Play/пробелу: продолжить, перейти на следующий клип или остаться на обрезе.
 *
 * HTML video знает весь исходник, а не выбранный кусок. После `end` currentTime уже на `out`
 * или чуть дальше — без этой проверки пробел снова вызвал бы play() и картинка уехала бы
 * в обрезанный хвост.
 */
export function resumePlan(clips: Clip[], at: { index: number; sourceTime: number }): ResumePlan {
  const step = stepPlan(clips, at)
  if (step.kind === 'playing') return { kind: 'play' }
  if (step.kind === 'advance') {
    return {
      kind: 'advance',
      index: step.index,
      assetId: step.assetId,
      time: step.time,
      timelineTime: step.timelineTime,
    }
  }
  return { kind: 'stop', timelineTime: step.timelineTime }
}

/** Громкость музыки в момент ролика с учётом затуханий. Затухания не перекрывают друг друга. */
export type Silence = { start: number; end: number }
export type Ducking = { sourceTime: number; silences: Silence[] }

const DUCK_SPEECH_GAIN = 0.3

export function musicVolume(
  music: { volume: number; fade_in: number; fade_out: number; duck?: boolean } | null,
  timelineTime: number,
  total: number,
  ducking?: Ducking | null,
): number {
  if (!music) return 0
  const half = total / 2
  const fadeIn = Math.min(music.fade_in, half)
  const fadeOut = Math.min(music.fade_out, half)
  let gain = music.volume
  if (fadeIn > 0 && timelineTime < fadeIn) gain *= timelineTime / fadeIn
  const fromEnd = total - timelineTime
  if (fadeOut > 0 && fromEnd < fadeOut) gain *= Math.max(0, fromEnd) / fadeOut
  if (music.duck && ducking) {
    const pause = ducking.silences.some(
      s => ducking.sourceTime >= s.start && ducking.sourceTime < s.end,
    )
    if (!pause) gain *= DUCK_SPEECH_GAIN
  }
  return Math.max(0, Math.min(1, gain))
}

/**
 * HTML video.volume принимает 0…1.
 * Громкость клипа и ползунок «Речь» считаем вместе: в сборке speech_volume есть
 * только если есть музыка, и режет уже после volume клипа. Усиление выше 1
 * в превью не слышно.
 */
export function previewClipVolume(volume: number, speechVolume = 1): number {
  return Math.max(0, Math.min(1, volume * speechVolume))
}

/** Без музыки ползунок речи в сборке не действует — в превью тоже. */
export function previewSpeechGain(music: { speech_volume?: number } | null | undefined): number {
  if (!music) return 1
  const value = music.speech_volume
  if (typeof value !== 'number' || !Number.isFinite(value)) return 1
  return Math.max(0, Math.min(1, value))
}

const ASPECTS: Record<string, number> = { '16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1 }

/** Пропорция кадра вывода числом; неизвестное значение считаем широким. */
export function aspectRatio(aspect: string): number {
  return ASPECTS[aspect] ?? ASPECTS['16:9']
}
