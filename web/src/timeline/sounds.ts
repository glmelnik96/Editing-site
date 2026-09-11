/**
 * Свободная колея: куски со своим местом на шкале — звуки под картинкой и наложения над ней.
 *
 * Отдельно от модели клипов, потому что правила у них противоположные. Клипы стоят встык и
 * сдвигают друг друга, а кусок колеи лежит ровно там, куда его положили: соседей он не трогает,
 * и перекрываться с ними ему можно — сборка звуки смешает, а наложения положит слоями.
 *
 * Общие функции работают с любым куском, у которого есть id, at, in и out; звуковые имена
 * оставлены для читаемости вызовов и старых тестов.
 */
import type { Sound } from '../project'
import { MIN_BLOCK_PX, ms, type Clip } from './model'

/** Кусок свободной колеи: своё время на шкале и свой отрезок записи. Звук может идти по кругу. */
export type Placed = Pick<Sound, 'id' | 'at' | 'in' | 'out'> & { loop?: boolean }

export type SoundBlock = { id: string; left: number; width: number }

/** Громкость звука на шкале, как у клипа: 0 — тишина, 2 — вдвое громче записи. */
export const SOUND_VOLUME_MAX = 2

export function soundLength(item: Pick<Placed, 'in' | 'out'>): number {
  return ms(item.out - item.in)
}

/**
 * Сколько кусок занимает на шкале. Звук по кругу идёт до конца ролика, и блок честно тянется
 * до него: иначе на шкале лежал бы кусок в 20 секунд, а слышно было бы все 60.
 */
export function laneSpan(item: Placed, total: number): number {
  if (item.loop) return ms(Math.max(0, total - item.at))
  return soundLength(item)
}

/**
 * Раскладка кусков в пикселях. Короткий кусок не уже порога: иначе его не поймать указателем.
 * total — длина ролика, до неё тянутся звуки по кругу.
 */
export function laneBlocks(items: Placed[], pxPerSec: number, total = Number.POSITIVE_INFINITY): SoundBlock[] {
  return items.map(item => ({
    id: item.id,
    left: ms(item.at * pxPerSec),
    width: Math.max(MIN_BLOCK_PX, ms(laneSpan(item, total) * pxPerSec)),
  }))
}
export const soundBlocks = laneBlocks

/**
 * Где кончается самый поздний кусок колеи.
 *
 * Шкала тянется до него, даже если он свисает за конец ролика: сборка этот хвост обрежет, но
 * человеку надо видеть его и уметь схватить, чтобы подвинуть назад.
 */
export function laneEnd(items: Placed[], total = Number.POSITIVE_INFINITY): number {
  return items.reduce((end, item) => Math.max(end, ms(item.at + laneSpan(item, total))), 0)
}
export const soundsEnd = laneEnd

/** Переложить кусок на новое место. Раньше начала ролика класть некуда. */
export function moveItem<T extends Placed>(items: T[], id: string, at: number): T[] {
  return items.map(item => (item.id === id ? { ...item, at: ms(Math.max(0, at)) } : item))
}
export const moveSound = moveItem

export function removeItem<T extends Placed>(items: T[], id: string): T[] {
  return items.filter(item => item.id !== id)
}
export const removeSound = removeItem

/** Правка полей звука с зажатием в допустимое: сервер отверг бы документ за пределами. */
export function updateSound(sounds: Sound[], id: string, patch: Partial<Sound>): Sound[] {
  return sounds.map(sound => {
    if (sound.id !== id) return sound
    const next = { ...sound, ...patch }
    return {
      ...next,
      volume: Math.min(SOUND_VOLUME_MAX, Math.max(0, next.volume)),
      fade_in: Math.max(0, next.fade_in),
      fade_out: Math.max(0, next.fade_out),
    }
  })
}

export function setSoundVolume(sounds: Sound[], id: string, volume: number): Sound[] {
  const clamped = Math.min(SOUND_VOLUME_MAX, Math.max(0, volume))
  return sounds.map(sound => (sound.id === id ? { ...sound, volume: clamped } : sound))
}

/**
 * Имя нового куска с приставкой колеи, не занятое никем на шкале.
 *
 * Выделение на шкале одно на все дорожки: совпади имя звука с именем клипа, щелчок по одному
 * выделял бы другой, а удаление по Del могло бы стереть не тот кусок. Сервер такой документ
 * и вовсе отвергнет. Случайный хвост — по той же причине, что у newClipId: два добавления
 * подряд, пока список ещё не обновился, получили бы одно и то же имя.
 */
export function newLaneId(prefix: string, own: number, taken: Iterable<string>): string {
  const used = new Set(taken)
  const base = own + 1
  let candidate: string
  do {
    candidate = `${prefix}${base}_${Math.random().toString(36).slice(2, 8)}`
  } while (used.has(candidate))
  return candidate
}

export function newSoundId(clips: Pick<Clip, 'id'>[], sounds: Pick<Sound, 'id'>[]): string {
  return newLaneId('s', sounds.length, [...clips, ...sounds].map(item => item.id))
}
