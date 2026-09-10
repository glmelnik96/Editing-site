/**
 * Звуковая дорожка: куски звука под картинкой, у каждого своё место на шкале.
 *
 * Отдельно от модели клипов, потому что правила у них противоположные. Клипы стоят встык и
 * сдвигают друг друга, а звук лежит ровно там, куда его положили: соседей он не трогает, и
 * перекрываться с ними ему можно — сборка их смешает.
 */
import type { Sound } from '../project'
import { MIN_BLOCK_PX, ms, type Clip } from './model'

export type SoundBlock = { id: string; left: number; width: number }

/** Громкость звука на шкале, как у клипа: 0 — тишина, 2 — вдвое громче записи. */
export const SOUND_VOLUME_MAX = 2

export function soundLength(sound: Pick<Sound, 'in' | 'out'>): number {
  return ms(sound.out - sound.in)
}

/** Раскладка звуков в пикселях. Короткий звук не уже порога: иначе его не поймать указателем. */
export function soundBlocks(sounds: Sound[], pxPerSec: number): SoundBlock[] {
  return sounds.map(sound => ({
    id: sound.id,
    left: ms(sound.at * pxPerSec),
    width: Math.max(MIN_BLOCK_PX, ms(soundLength(sound) * pxPerSec)),
  }))
}

/**
 * Где кончается самый поздний звук.
 *
 * Шкала тянется до него, даже если он свисает за конец ролика: сборка этот хвост обрежет, но
 * человеку надо видеть его и уметь схватить, чтобы подвинуть назад.
 */
export function soundsEnd(sounds: Sound[]): number {
  return sounds.reduce((end, sound) => Math.max(end, ms(sound.at + soundLength(sound))), 0)
}

/** Переложить звук на новое место. Раньше начала ролика класть некуда. */
export function moveSound(sounds: Sound[], id: string, at: number): Sound[] {
  return sounds.map(sound => (sound.id === id ? { ...sound, at: ms(Math.max(0, at)) } : sound))
}

export function removeSound(sounds: Sound[], id: string): Sound[] {
  return sounds.filter(sound => sound.id !== id)
}

export function setSoundVolume(sounds: Sound[], id: string, volume: number): Sound[] {
  const clamped = Math.min(SOUND_VOLUME_MAX, Math.max(0, volume))
  return sounds.map(sound => (sound.id === id ? { ...sound, volume: clamped } : sound))
}

/**
 * Имя нового звука, не занятое ни клипом, ни звуком.
 *
 * Выделение на шкале одно на обе дорожки: совпади имя звука с именем клипа, щелчок по одному
 * выделял бы другой, а удаление по Del могло бы стереть не тот кусок. Сервер такой документ
 * и вовсе отвергнет. Случайный хвост — по той же причине, что у newClipId: два добавления
 * подряд, пока список ещё не обновился, получили бы одно и то же имя.
 */
export function newSoundId(clips: Pick<Clip, 'id'>[], sounds: Pick<Sound, 'id'>[]): string {
  const taken = new Set([...clips, ...sounds].map(item => item.id))
  const base = sounds.length + 1
  let candidate: string
  do {
    candidate = `s${base}_${Math.random().toString(36).slice(2, 8)}`
  } while (taken.has(candidate))
  return candidate
}
