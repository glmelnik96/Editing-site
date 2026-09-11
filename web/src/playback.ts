/**
 * Состояние воспроизведения склейки: какой клип играет, когда переключаться, какая громкость музыки.
 *
 * Здесь нет DOM: функции решают, что делать, а драйвер в редакторе двигает элементы video и audio.
 * Так логика шва проверяется тестами, а не глазами.
 */
import type { Sound } from './project'
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

export type Silence = { start: number; end: number }
export type Ducking = { sourceTime: number; silences: Silence[] }

/** Насколько тише фон под речью. В сборке это компрессор по боковой цепи; в превью — одно число. */
export const DUCK_SPEECH_GAIN = 0.3

/** Появление и затухание на краях отрезка. Каждое не длиннее половины — иначе они перекрылись бы. */
export function fadeGain(local: number, span: number, fadeIn: number, fadeOut: number): number {
  const half = span / 2
  const rise = Math.min(fadeIn, half)
  const fall = Math.min(fadeOut, half)
  let gain = 1
  if (rise > 0 && local < rise) gain *= Math.max(0, local) / rise
  const left = span - local
  if (fall > 0 && left < fall) gain *= Math.max(0, left) / fall
  return Math.max(0, Math.min(1, gain))
}

/** Множитель приглушения: под речью тише, в паузе и без карты пауз — как есть. */
export function duckFactor(ducking: Ducking | null | undefined): number {
  if (!ducking) return 1
  const pause = ducking.silences.some(s => ducking.sourceTime >= s.start && ducking.sourceTime < s.end)
  return pause ? 1 : DUCK_SPEECH_GAIN
}

/** HTML video.volume принимает 0…1: усиление выше 1 в превью не слышно. */
export function previewClipVolume(volume: number): number {
  return Math.max(0, Math.min(1, volume))
}

const ASPECTS: Record<string, number> = { '16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1 }

/** Пропорция кадра вывода числом; неизвестное значение считаем широким. */
export function aspectRatio(aspect: string): number {
  return ASPECTS[aspect] ?? ASPECTS['16:9']
}

export type SoundCue = { id: string; assetId: string; time: number; gain: number; duck: boolean }

/**
 * Какие звуки дорожки звучат в момент ролика, с какого места записи и насколько громко.
 *
 * Хвост за концом ролика не звучит и в превью: сборка его обрезает, и услышать в браузере то,
 * чего не будет в файле, хуже, чем не услышать. Конец звука не входит — как у клипов. Звук по
 * кругу идёт до конца ролика, а время внутри записи — по остатку от длины куска. Громкость — с
 * появлением и затуханием; приглушение под речь считает вызывающий: ему видно, кто говорит.
 */
export function soundPlan(sounds: Sound[], timelineTime: number, total: number): SoundCue[] {
  if (timelineTime >= total) return []
  const cues: SoundCue[] = []
  for (const sound of sounds) {
    const length = sound.out - sound.in
    const span = sound.loop ? total - sound.at : length
    const local = timelineTime - sound.at
    if (length <= 0 || local < 0 || local >= span) continue
    cues.push({
      id: sound.id,
      assetId: sound.asset_id,
      time: ms(sound.in + (sound.loop ? local % length : local)),
      gain: sound.volume * fadeGain(local, span, sound.fade_in, sound.fade_out),
      duck: sound.duck,
    })
  }
  return cues
}
