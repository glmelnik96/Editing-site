/**
 * Арифметика монтажа: список клипов, время шкалы против времени исходника, правки списка.
 *
 * Здесь нет ни DOM, ни запросов: всё чистые функции над массивом клипов. Любая правка возвращает
 * новый массив, исходный не меняется — так проще откатывать и сравнивать состояния.
 */

export type Transition = { kind: 'fade'; duration: number }

export type Clip = {
  id: string
  asset_id: string
  in: number
  out: number
  volume: number
  snap_to_pauses: boolean
  in_verified: boolean
  out_verified: boolean
  transition?: Transition
}

export const MIN_CLIP = 0.1 // минимальная длина клипа, как на сервере
export const MIN_BLOCK_PX = 8 // блок уже этого не поймать указателем

/** Округление до миллисекунды: сервер хранит времена именно так. */
export function ms(value: number): number {
  return Math.round(value * 1000) / 1000
}

export function clipDuration(clip: Clip): number {
  return clip.out - clip.in
}

/** Длительность перехода «в» этот клип. У первого и при стыке — ноль. */
export function fadeInto(clip: Clip, index: number): number {
  if (index <= 0 || clip.transition?.kind !== 'fade') return 0
  const duration = clip.transition.duration
  return duration > 0 ? ms(duration) : 0
}

export function totalDuration(clips: Clip[]): number {
  return ms(clips.reduce((sum, clip, index) => sum + clipDuration(clip) - fadeInto(clip, index), 0))
}

/** Время начала клипа с этим номером на шкале. Номер за пределами списка даёт конец шкалы. */
export function timelineStart(clips: Clip[], index: number): number {
  const capped = Math.max(0, Math.min(index, clips.length))
  let start = 0
  for (let i = 0; i < capped; i++) start += clipDuration(clips[i]) - fadeInto(clips[i], i)
  if (capped < clips.length) start -= fadeInto(clips[capped], capped)
  return ms(start)
}

/** Клип под курсором шкалы: номер, сам клип и смещение внутри него. */
export function clipAt(clips: Clip[], time: number): { index: number; clip: Clip; offset: number } | null {
  if (time < 0) return null
  let start = 0
  for (let index = 0; index < clips.length; index++) {
    start -= fadeInto(clips[index], index)
    const duration = clipDuration(clips[index])
    if (time < start + duration) return { index, clip: clips[index], offset: ms(time - start) }
    start += duration
  }
  return null
}

/** Время шкалы → ассет и время внутри исходника. */
export function sourceTime(clips: Clip[], time: number): { index: number; assetId: string; time: number } | null {
  const found = clipAt(clips, time)
  if (found === null) return null
  return { index: found.index, assetId: found.clip.asset_id, time: ms(found.clip.in + found.offset) }
}

/** Уникальные записи шкалы в порядке появления: субтитры собираются только из них. */
export function clipAssetIds(clips: Pick<Clip, 'asset_id'>[]): string[] {
  const ids: string[] = []
  for (const clip of clips) {
    if (!ids.includes(clip.asset_id)) ids.push(clip.asset_id)
  }
  return ids
}

/**
 * Свободный id клипа: c<n> плюс случайный хвост, чтобы два быстрых разреза не совпали.
 *
 * Список клипов не меняется между быстрыми вызовами (например, между разрезами в одной сессии
 * до перерисовки состояния), поэтому одного счётчика по длине списка недостаточно — он бы
 * возвращал один и тот же id, пока список не обновится. Случайный хвост решает это, не трогая
 * входные данные и не завися от внешнего изменяемого состояния.
 */
export function newClipId(clips: Clip[]): string {
  const used = new Set(clips.map(c => c.id))
  const base = clips.length + 1
  let candidate: string
  do {
    const tail = Math.random().toString(36).slice(2, 8)
    candidate = `c${base}_${tail}`
  } while (used.has(candidate))
  return candidate
}

export function insertClip(clips: Clip[], clip: Clip, at?: number): Clip[] {
  const copy = clips.slice()
  copy.splice(at === undefined ? copy.length : Math.max(0, Math.min(at, copy.length)), 0, clip)
  return clampTransitions(copy)
}

export function removeClip(clips: Clip[], id: string): Clip[] {
  return clampTransitions(clips.filter(c => c.id !== id))
}

export function moveClip(clips: Clip[], from: number, to: number): Clip[] {
  if (from < 0 || from >= clips.length) return clips
  const copy = clips.slice()
  const [moved] = copy.splice(from, 1)
  copy.splice(Math.max(0, Math.min(to, copy.length)), 0, moved)
  return clampTransitions(copy)
}

/**
 * Режет клип под курсором шкалы на два. Слишком близко к краю не режем: получился бы огрызок
 * короче минимума, который сервер всё равно отвергнет.
 */
export function splitAt(clips: Clip[], time: number): Clip[] {
  const found = clipAt(clips, time)
  if (found === null) return clips
  const cut = ms(found.clip.in + found.offset)
  if (cut - found.clip.in < MIN_CLIP || found.clip.out - cut < MIN_CLIP) return clips
  const left: Clip = { ...found.clip, out: cut, out_verified: false }
  const right = withoutTransition({ ...found.clip, id: newClipId(clips), in: cut, in_verified: false })
  const copy = clips.slice()
  copy.splice(found.index, 1, left, right)
  return clampTransitions(copy)
}

/**
 * Двигает границу клипа. Границы держатся в пределах исходника и не сходятся ближе минимума;
 * тронутая граница теряет подтверждение — её снова проверит сервер при сохранении.
 */
export function trimClip(
  clips: Clip[],
  id: string,
  edges: { in?: number; out?: number },
  limits: { duration?: number } = {},
): Clip[] {
  const next = clips.map(clip => {
    if (clip.id !== id) return clip
    const patched = { ...clip }
    if (edges.in !== undefined) {
      patched.in = ms(Math.max(0, Math.min(edges.in, clip.out - MIN_CLIP)))
      patched.in_verified = false
    }
    if (edges.out !== undefined) {
      const top = limits.duration ?? Number.POSITIVE_INFINITY
      patched.out = ms(Math.min(top, Math.max(edges.out, patched.in + MIN_CLIP)))
      patched.out_verified = false
    }
    return patched
  })
  return clampTransitions(next)
}

export type Block = { id: string; left: number; width: number; start: number; duration: number }

/** Раскладка блоков в пикселях при заданном масштабе. Переходы дают видимое перекрытие. */
export function layout(clips: Clip[], pxPerSec: number): Block[] {
  let start = 0
  return clips.map((clip, index) => {
    start -= fadeInto(clip, index)
    const duration = clipDuration(clip)
    const block: Block = {
      id: clip.id,
      left: ms(start * pxPerSec),
      width: Math.max(MIN_BLOCK_PX, ms(duration * pxPerSec)),
      start: ms(start),
      duration: ms(duration),
    }
    start += duration
    return block
  })
}

/** Максимальная длительность перехода «в» клип: строго короче обоих соседей. */
export function maxFade(prev: Clip, clip: Clip): number {
  const cap = Math.min(clipDuration(prev), clipDuration(clip))
  return ms(Math.max(0, cap - 0.001))
}

function withoutTransition(clip: Clip): Clip {
  const next = { ...clip }
  delete next.transition
  return next
}

/** Убрать невозможные переходы: у первого клипа, нулевые и длиннее соседа. */
export function clampTransitions(clips: Clip[]): Clip[] {
  return clips.map((clip, index) => {
    if (index === 0) return clip.transition ? withoutTransition(clip) : clip
    const fade = fadeInto(clip, index)
    if (fade <= 0) return clip.transition ? withoutTransition(clip) : clip
    const cap = maxFade(clips[index - 1], clip)
    if (fade <= cap) return clip
    if (cap <= 0) return withoutTransition(clip)
    return { ...clip, transition: { kind: 'fade', duration: cap } }
  })
}

/**
 * Куда встанет переносимый клип, если отпустить указатель на этом времени шкалы.
 *
 * Считается через ту же moveClip, что выполняет саму правку: иначе показ и результат разъехались бы.
 * Возвращает номер позиции и время начала клипа на новом месте.
 */
/**
 * Тот же ли это список: та же длина и те же клипы в том же порядке.
 *
 * Нужно, когда перенос уже начался, а список успел обновиться (ответ сервера с подтянутыми
 * резами). Состав прежний — можно переносить в свежем списке и не терять правки сервера;
 * состав изменился — индексы переноса к нему уже не относятся.
 */
export function sameOrder(a: Clip[], b: Clip[]): boolean {
  return a.length === b.length && a.every((clip, i) => clip.id === b[i].id)
}

export function dropTarget(clips: Clip[], from: number, time: number): { to: number; start: number } | null {
  if (from < 0 || from >= clips.length) return null
  const found = clipAt(clips, time)
  const to = found ? found.index : clips.length - 1
  const moved = moveClip(clips, from, to)
  const index = moved.findIndex(c => c.id === clips[from].id)
  return { to, start: timelineStart(moved, index) }
}


/* Масштаб шкалы: от четырёх пикселей на секунду (часовая запись целиком в экране) до четырёхсот
 * (кадр различим поштучно). */
export const ZOOM_MIN = 4
export const ZOOM_MAX = 400

/**
 * Ползунок и масштаб связаны по логарифму, а не напрямую.
 *
 * Диапазон стократный: при линейной связи первая четверть ползунка проскакивала бы всю
 * осмысленную часть, а остальные три четверти двигали бы кадр туда-сюда. По логарифму один и тот
 * же сдвиг ручки везде меняет масштаб во столько же раз.
 */
export function zoomToPercent(pxPerSec: number): number {
  const clamped = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, pxPerSec))
  return Math.round((Math.log(clamped / ZOOM_MIN) / Math.log(ZOOM_MAX / ZOOM_MIN)) * 100)
}

export function percentToZoom(percent: number): number {
  const clamped = Math.max(0, Math.min(100, percent))
  return ms(ZOOM_MIN * (ZOOM_MAX / ZOOM_MIN) ** (clamped / 100))
}

