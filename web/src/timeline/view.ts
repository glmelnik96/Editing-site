/**
 * Шкала монтажа: блоки клипов с кадрами и волной, перетаскивание, подрезка ручками, курсор.
 *
 * Модуль только рисует и ловит указатель. Любая правка уходит наверх через onChange уже готовым
 * списком клипов: считает её модель (model.ts), а не эта обвязка.
 */
import { escapeHtml } from '../html'
import type { Sound } from '../project'
import { barsFor, sliceThumbs, type AssetData } from '../strip'
import { clipDuration, dropTarget, fadeInto, layout, MIN_BLOCK_PX, moveClip, ms, sameOrder, totalDuration, trimClip, ZOOM_MAX, ZOOM_MIN, type Clip } from './model'
import { moveSound, soundBlocks, soundLength, soundsEnd } from './sounds'
import { tileRange, tileWidth, visibleTiles, type Tile } from './tiles'

export type AssetInfo = { duration: number | null; files: { thumbs: string | null } }

export type TimelineHandlers = {
  onChange: (clips: Clip[]) => void
  /** Правка звуковой дорожки: готовый список звуков, как onChange для клипов. */
  onSoundsChange: (sounds: Sound[]) => void
  onSeek: (time: number) => void
  onSelect: (id: string | null) => void
}

export type RenderInput = {
  clips: Clip[]
  sounds: Sound[]
  assets: Map<string, AssetInfo>
  data: Map<string, AssetData>
  pxPerSec: number
}

const TRACK_HEIGHT = 72
// Волну читают, чтобы найти паузы: от её высоты прямо зависит, попадёт человек резом в тишину
// или в слово. Рисуем от середины в обе стороны — при той же высоте блока это вдвое больше
// размаха, чем полоска от низа.
const WAVE_HEIGHT = 44
const HANDLE_PX = 8
// Выбранный блок поднимается над соседями, чтобы его обводку не срезал следующий клип.
// Число живёт внутри слоя .blocks, наружу — к игле и призраку — оно не вылезает.
const SELECTED_Z = 999
const CLICK_SLOP_PX = 4 // сдвиг меньше этого — это клик, а не перенос
const SAY_MS = 6000 // сколько держать сказанное под шкалой

export function emptyTrackHint(clipCount: number): string {
  return clipCount === 0 ? 'Добавьте кусок из исходников' : ''
}

function waveCanvas(bars: number[], width: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(width))
  canvas.height = WAVE_HEIGHT
  canvas.className = 'wave'
  const ctx = canvas.getContext('2d')
  if (ctx) {
    ctx.fillStyle = 'rgba(255,255,255,.8)'
    const middle = WAVE_HEIGHT / 2
    bars.forEach((value, x) => {
      const half = Math.max(0.5, (value / 255) * middle)
      ctx.fillRect(x, middle - half, 1, half * 2)
    })
  }
  return canvas
}

/** Шкала: возвращает управление для редактора. */
export function mountTimeline(el: HTMLElement, handlers: TimelineHandlers) {
  el.innerHTML = `
    <div class="timeline" id="tl-view">
      <div class="ruler" id="tl-ruler"></div>
      <!-- Полоса перемотки и дорожка в одной колее: игла проходит их насквозь, и её хват
           оказывается в полосе — там, где по нему и надо попадать. -->
      <div class="lane" id="tl-lane">
        <div class="scrub" id="tl-scrub" title="Перемотка: тяните за хват или щёлкните по полосе"></div>
        <div class="track" id="tl-track"><div class="blocks" id="tl-blocks"></div><div class="drop-ghost" id="tl-drop" hidden></div></div>
        <!-- Звуковая дорожка — своя колея под клипами: звук лежит по своему времени и клипы не
             сдвигает. Пустая она видна всё равно: появляющаяся колея переставляла бы шкалу
             под руками, а подпись в ней объясняет, откуда туда класть. -->
        <div class="sound-track empty" id="tl-sounds"
          data-empty="Звуковая дорожка: озвучка и шумы поверх речи. Положите звук из «Исходников»"></div>
        <div class="playhead" id="tl-playhead"><i class="playhead-grip"></i></div>
      </div>
    </div>
    <div class="tl-hint muted" id="tl-hint"></div>`
  const view = el.querySelector('.timeline') as HTMLElement
  const ruler = el.querySelector('#tl-ruler') as HTMLElement
  const scrub = el.querySelector('#tl-scrub') as HTMLElement
  const lane = el.querySelector('#tl-lane') as HTMLElement
  const track = el.querySelector('#tl-track') as HTMLElement
  const blocksBox = el.querySelector('#tl-blocks') as HTMLElement
  const soundTrack = el.querySelector('#tl-sounds') as HTMLElement
  const playhead = el.querySelector('#tl-playhead') as HTMLElement
  const ghost = el.querySelector('#tl-drop') as HTMLElement
  let sayTimer = 0
  const hint = el.querySelector('#tl-hint') as HTMLElement

  let current: RenderInput = { clips: [], sounds: [], assets: new Map(), data: new Map(), pxPerSec: 40 }
  let selected: string | null = null
  let drag: {
    id: string
    index: number
    kind: 'move' | 'in' | 'out'
    startX: number
    clips: Clip[]
    moved: boolean
  } | null = null
  // Перенос звука. Отдельно от переноса клипа: у звука нет ни очереди, ни ручек, он просто
  // едет по своей колее, и смешивать два состояния значило бы проверять вид куска на каждом шаге.
  let soundDrag: {
    id: string
    startX: number
    at: number
    sounds: Sound[]
    moved: boolean
    node: HTMLElement
  } | null = null
  // Пришло, пока человек тянул блок. Во время переноса шкалу не пересобираем, иначе блок теряет
  // подсветку и уезжает не туда: автосохранение отвечает свежим документом ровно посреди
  // следующего переноса, а подгрузка кадров и волн приходит и того чаще.
  let pending: Partial<RenderInput> | null = null

  // rect уже сдвинут прокруткой .timeline (её предка); scrollLeft — на случай, если сам track когда-то станет скроллиться
  const timeAt = (clientX: number): number => {
    const rect = track.getBoundingClientRect()
    return Math.max(0, (clientX - rect.left + track.scrollLeft) / current.pxPerSec)
  }

  function blockHtml(clip: Clip): string {
    const marks =
      clip.snap_to_pauses && (!clip.in_verified || !clip.out_verified)
        ? '<span class="unverified" title="Граница не подтверждена паузой">!</span>'
        : ''
    return `<span class="label">${escapeHtml(clip.id)} · ${(clip.out - clip.in).toFixed(1)} с${marks}</span>
      <b class="handle handle-in"></b><b class="handle handle-out"></b>`
  }

  function render(input?: Partial<RenderInput>): void {
    if (drag || soundDrag) {
      pending = { ...(pending ?? {}), ...input }
      return
    }
    current = { ...current, ...input }
    const blocks = layout(current.clips, current.pxPerSec)
    // Шкала дотягивается и до звука, свисающего за конец ролика: сборка его хвост обрежет, но
    // увидеть и схватить его, чтобы вернуть назад, человек должен.
    const end = Math.max(totalDuration(current.clips), soundsEnd(current.sounds))
    const width = Math.max(200, end * current.pxPerSec)
    track.style.width = `${width}px`
    soundTrack.style.width = `${width}px`
    lane.style.width = `${width}px`
    ruler.style.width = `${width}px`
    ruler.innerHTML = Array.from({ length: Math.ceil(width / (current.pxPerSec * 5)) + 1 }, (_, i) => {
      const seconds = i * 5
      return `<span class="tick" style="left:${seconds * current.pxPerSec}px">${seconds} с</span>`
    }).join('')

    blocksBox.querySelectorAll('.block').forEach(node => node.remove())
    strips = []
    blocks.forEach((block, index) => {
      const clip = current.clips[index]
      const node = document.createElement('div')
      const fade = fadeInto(clip, index)
      node.className = `block${clip.id === selected ? ' selected' : ''}${fade > 0 ? ' has-fade' : ''}`
      node.style.left = `${block.left}px`
      node.style.width = `${block.width}px`
      node.style.height = `${TRACK_HEIGHT}px`
      node.style.zIndex = clip.id === selected ? String(SELECTED_Z) : String(index + 1)
      if (fade > 0) node.style.setProperty('--fade-px', `${Math.max(6, ms(fade * current.pxPerSec))}px`)
      node.dataset.id = clip.id
      node.dataset.index = String(index)
      node.innerHTML = blockHtml(clip)
      strips.push(strip(node, clip.asset_id, clip.in, clip.out, block.left, block.width, true))
      blocksBox.appendChild(node)
    })
    soundTrack.querySelectorAll('.sound-block').forEach(node => node.remove())
    soundTrack.classList.toggle('empty', current.sounds.length === 0)
    soundBlocks(current.sounds, current.pxPerSec).forEach((block, index) => {
      const sound = current.sounds[index]
      const node = document.createElement('div')
      node.className = `sound-block${sound.id === selected ? ' selected' : ''}`
      node.style.left = `${block.left}px`
      node.style.width = `${block.width}px`
      node.dataset.id = sound.id
      node.innerHTML = `<span class="label">${escapeHtml(sound.id)} · ${soundLength(sound).toFixed(1)} с</span>`
      strips.push(strip(node, sound.asset_id, sound.in, sound.out, block.left, block.width, false))
      soundTrack.appendChild(node)
    })
    if (!drag) hint.textContent = emptyTrackHint(current.clips.length)
    paintTiles()
  }

  /** Полоса блока: что под ним лежит и где он на шкале. Плитки рисуются по ней лениво. */
  type Strip = {
    box: HTMLElement
    assetId: string
    from: number
    to: number
    left: number
    width: number
    frames: boolean
  }
  let strips: Strip[] = []
  let paintQueued = false

  /** Завести полосу блока: пустой слой плиток первым ребёнком, чтобы подпись и ручки были поверх. */
  function strip(
    node: HTMLElement, assetId: string, from: number, to: number, left: number, width: number, frames: boolean,
  ): Strip {
    const box = document.createElement('div')
    box.className = 'wave-tiles'
    node.prepend(box)
    return { box, assetId, from, to, left, width, frames }
  }

  /** Одна плитка: кадры своего отрезка и волна шириной с плитку — далеко от предела холста. */
  function tileNode(s: Strip, tile: Tile): HTMLElement {
    const node = document.createElement('div')
    node.className = 'wave-tile'
    node.dataset.i = String(tile.index)
    node.style.left = `${tile.x0}px`
    node.style.width = `${tile.x1 - tile.x0}px`
    const info = current.data.get(s.assetId)
    const sprite = current.assets.get(s.assetId)?.files.thumbs
    if (s.frames && sprite && info?.thumbs) {
      // Кадры считаются на весь блок разом — это просто числа, — а в плитку попадают свои.
      node.innerHTML = sliceThumbs(info.thumbs, { from: s.from, to: s.to }, s.width)
        .filter(f => f.left >= tile.x0 && f.left < tile.x1)
        .map(f => {
          const bg = f.background
          return `<i class="frame" style="left:${f.left - tile.x0}px;background-image:url('${escapeHtml(sprite)}');
            background-position:${bg.x}px ${bg.y}px;background-size:${bg.width}px ${bg.height}px"></i>`
        })
        .join('')
    }
    const range = tileRange(tile, s.width, s.from, s.to)
    const width = tile.x1 - tile.x0
    node.appendChild(waveCanvas(barsFor(info?.peaks ?? null, range, Math.round(width)), width))
    return node
  }

  /**
   * Дорисовать видимые плитки и убрать далёкие.
   *
   * Запас — экран в каждую сторону: прокрутка открывает уже готовое, а не пустоту, пока рисуется.
   */
  function paintTiles(): void {
    const margin = view.clientWidth
    const from = view.scrollLeft - margin
    const to = view.scrollLeft + view.clientWidth + margin
    for (const s of strips) {
      const info = current.data.get(s.assetId)
      const size = tileWidth(s.frames ? (info?.thumbs?.width ?? null) : null)
      const wanted = visibleTiles(s.left, s.width, from, to, size)
      const keep = new Set(wanted.map(tile => String(tile.index)))
      s.box.querySelectorAll<HTMLElement>('.wave-tile').forEach(node => {
        if (!keep.has(node.dataset.i ?? '')) node.remove()
      })
      const have = new Set(Array.from(s.box.querySelectorAll<HTMLElement>('.wave-tile'), node => node.dataset.i))
      for (const tile of wanted) {
        if (!have.has(String(tile.index))) s.box.appendChild(tileNode(s, tile))
      }
    }
  }

  /** Прокрутка и смена размера шкалы присылают десятки событий в секунду — рисуем раз в кадр. */
  function schedulePaint(): void {
    if (paintQueued) return
    paintQueued = true
    requestAnimationFrame(() => {
      paintQueued = false
      paintTiles()
    })
  }

  view.addEventListener('scroll', schedulePaint, { passive: true })
  // Шкала шире или уже без прокрутки — сворачивание левой панели, окно браузера: видимое меняется.
  new ResizeObserver(schedulePaint).observe(view)

  /** Догнать то, что приходило во время переноса. Зовётся, когда drag уже снят. */
  function flushPending(): void {
    const queued = pending
    pending = null
    render(queued ?? undefined)
  }

  /**
   * Список, к которому применяем правку: свежий, если он остался тем же по составу и порядку.
   * Так не теряются резы, подтянутые сервером, пока человек тянул блок; изменился состав —
   * берём тот список, с которым перенос начинали, иначе индексы уедут.
   */
  function targetClips(started: Clip[]): Clip[] {
    const fresh = pending?.clips
    return fresh && sameOrder(started, fresh) ? fresh : started
  }

  function finishDrag(clientX: number): void {
    if (!drag) return
    const active = drag
    const at = timeAt(clientX) // считаем в масштабе переноса: pending мог принести другой зум
    drag = null
    ghost.hidden = true
    hint.textContent = ''
    if (!active.moved) {
      // Клик без переноса: ставим курсор туда, куда ткнули (блок уже выделён в pointerdown).
      handlers.onSeek(at)
      flushPending() // снимет и класс переноса: блоки пересобираются заново
      return
    }
    let next: Clip[] | null = null
    if (active.kind === 'move') {
      // Та же функция, что рисовала призрак: показ и результат не могут разойтись.
      const preview = dropTarget(current.clips, active.index, at)
      if (preview && preview.to !== active.index) next = moveClip(targetClips(active.clips), active.index, preview.to)
    } else {
      const clip = active.clips[active.index]
      const delta = (clientX - active.startX) / current.pxPerSec
      const duration = current.assets.get(clip.asset_id)?.duration ?? undefined
      const edges = active.kind === 'in' ? { in: ms(clip.in + delta) } : { out: ms(clip.out + delta) }
      next = trimClip(targetClips(active.clips), clip.id, edges, { duration: duration ?? undefined })
    }
    flushPending()
    if (next) handlers.onChange(next)
  }

  /**
   * Показать выделение, не пересобирая шкалу.
   *
   * Раньше это делал render(): он сносил все блоки и строил заново — то есть уничтожал блок прямо
   * под пальцем в момент нажатия, ещё до захвата указателя. Перенос от этого срабатывал через раз,
   * а на длинной шкале каждое нажатие впустую перерисовывало кадры и звуковые волны всех клипов.
   */
  function markSelected(): void {
    blocksBox.querySelectorAll<HTMLElement>('.block').forEach(node => {
      const mine = node.dataset.id === selected
      node.classList.toggle('selected', mine)
      node.style.zIndex = mine ? String(SELECTED_Z) : String(Number(node.dataset.index ?? 0) + 1)
    })
    soundTrack.querySelectorAll<HTMLElement>('.sound-block').forEach(node => {
      node.classList.toggle('selected', node.dataset.id === selected)
    })
  }

  /** Перенос отменён системой (жест перехватил браузер): возвращаем всё как было, правки нет. */
  function abortDrag(): void {
    if (!drag) return
    drag = null
    ghost.hidden = true
    hint.textContent = ''
    flushPending()
  }

  track.addEventListener('pointerdown', event => {
    const target = event.target as HTMLElement
    const node = target.closest('.block') as HTMLElement | null
    if (!node) {
      handlers.onSeek(timeAt(event.clientX))
      return
    }
    // Захват берём первым делом: дальше мы трогаем DOM и классы, и терять указатель на полпути
    // нельзя — именно поэтому перенос срабатывал через раз. Отказ гасим: указателя может уже не
    // быть (жест перехватила система), и ронять из-за этого выбор клипа незачем — без захвата
    // перенос просто потеряет курсор за краем блока, а выделение и подрезка работают и так.
    let captured = true
    try {
      track.setPointerCapture(event.pointerId)
    } catch {
      captured = false
    }
    const id = node.dataset.id ?? ''
    const index = Number(node.dataset.index ?? 0)
    selected = id
    handlers.onSelect(id)
    const rect = node.getBoundingClientRect()
    const kind: 'move' | 'in' | 'out' = target.classList.contains('handle-in')
      ? 'in'
      : target.classList.contains('handle-out')
        ? 'out'
        : event.clientX - rect.left < HANDLE_PX
          ? 'in'
          : rect.right - event.clientX < HANDLE_PX
            ? 'out'
            : 'move'
    // Выделение показываем до начала переноса: с этого момента шкала заморожена и render()
    // только копит пришедшее.
    markSelected()
    // Без захвата перенос начинать нельзя: отпускание за пределами шкалы до нас не дойдёт, drag
    // останется висеть, а render() при живом drag только копит правки — шкала замерла бы навсегда.
    // Выбор клипа при этом уже случился, так что нажатие не пропало впустую.
    if (!captured) return
    drag = { id, index, kind, startX: event.clientX, clips: current.clips, moved: false }
    node.classList.add('dragging')
  })

  track.addEventListener('pointermove', event => {
    if (!drag) return
    drag.moved = drag.moved || Math.abs(event.clientX - drag.startX) > CLICK_SLOP_PX
    const delta = (event.clientX - drag.startX) / current.pxPerSec
    const clip = drag.clips[drag.index]
    if (drag.kind === 'move') {
      // Призрак только при настоящем переносе: клик мимо блока перематывает курсор, и мигать здесь незачем.
      const preview = drag.moved ? dropTarget(current.clips, drag.index, timeAt(event.clientX)) : null
      if (preview) {
        const width = clipDuration(drag.clips[drag.index]) * current.pxPerSec
        ghost.hidden = false
        ghost.style.left = `${preview.start * current.pxPerSec}px`
        ghost.style.width = `${Math.max(MIN_BLOCK_PX, width)}px`
        hint.textContent = `«${clip.id}» встанет на ${preview.to + 1}-е место из ${current.clips.length}`
      }
    } else {
      const value = drag.kind === 'in' ? clip.in + delta : clip.out + delta
      hint.textContent = `${drag.kind === 'in' ? 'начало' : 'конец'}: ${Math.max(0, value).toFixed(2)} с`
    }
  })

  track.addEventListener('pointerup', event => finishDrag(event.clientX))
  // Отмена — это не «отпустил здесь»: раньше прерванный жест применял перенос по последней
  // точке, и клип вставал не туда, куда его вели.
  track.addEventListener('pointercancel', abortDrag)
  // Страховка: если захват потерян, а pointerup до нас не дошёл, шкала осталась бы замороженной.
  track.addEventListener('lostpointercapture', abortDrag)

  // Звуковая дорожка: щелчок по звуку выбирает его, протаскивание перекладывает на новое место,
  // щелчок по пустой колее перематывает — как по пустому месту дорожки клипов.
  soundTrack.addEventListener('pointerdown', event => {
    const target = event.target as HTMLElement
    const node = target.closest('.sound-block') as HTMLElement | null
    if (!node) {
      handlers.onSeek(timeAt(event.clientX))
      return
    }
    // Захват первым делом и по тем же причинам, что у клипа: без него отпускание за краем
    // шкалы до нас не дойдёт, и замороженная на время переноса шкала так и осталась бы стоять.
    let captured = true
    try {
      soundTrack.setPointerCapture(event.pointerId)
    } catch {
      captured = false
    }
    const id = node.dataset.id ?? ''
    selected = id
    handlers.onSelect(id)
    markSelected()
    const sound = current.sounds.find(item => item.id === id)
    if (!captured || !sound) return
    soundDrag = { id, startX: event.clientX, at: sound.at, sounds: current.sounds, moved: false, node }
    node.classList.add('dragging')
  })

  soundTrack.addEventListener('pointermove', event => {
    if (!soundDrag) return
    soundDrag.moved = soundDrag.moved || Math.abs(event.clientX - soundDrag.startX) > CLICK_SLOP_PX
    if (!soundDrag.moved) return
    const at = Math.max(0, soundDrag.at + (event.clientX - soundDrag.startX) / current.pxPerSec)
    soundDrag.node.style.left = `${at * current.pxPerSec}px`
    hint.textContent = `«${soundDrag.id}» ляжет на ${at.toFixed(2)} с`
  })

  /** Конец переноса звука. apply=false — жест отменила система: правки нет, всё как было. */
  function finishSoundDrag(clientX: number, apply: boolean): void {
    if (!soundDrag) return
    const active = soundDrag
    soundDrag = null
    hint.textContent = ''
    if (!active.moved) {
      if (apply) handlers.onSeek(timeAt(clientX))
      flushPending()
      return
    }
    // Свежий список, если пока тянули пришёл тот же набор звуков: так не теряется то, что
    // успел нормализовать сервер. Изменился состав — берём список, с которым начинали.
    const fresh = pending?.sounds
    const same =
      fresh !== undefined &&
      fresh.length === active.sounds.length &&
      fresh.every((item, i) => item.id === active.sounds[i].id)
    const base = same && fresh ? fresh : active.sounds
    const at = active.at + (clientX - active.startX) / current.pxPerSec
    flushPending()
    if (apply) handlers.onSoundsChange(moveSound(base, active.id, at))
  }

  soundTrack.addEventListener('pointerup', event => finishSoundDrag(event.clientX, true))
  soundTrack.addEventListener('pointercancel', () => finishSoundDrag(0, false))
  soundTrack.addEventListener('lostpointercapture', () => finishSoundDrag(0, false))

  // Полоса перемотки под линейкой и сама линейка: перетаскивание указателем двигает курсор.
  let scrubbing = false
  const startScrub = (event: PointerEvent) => {
    scrubbing = true
    // Захват гасим так же, как у переноса клипа: указателя может уже не быть, и ронять из-за
    // этого перемотку незачем — щелчок по полосе сработает и без него.
    try {
      scrub.setPointerCapture(event.pointerId)
    } catch {
      /* указатель уже не активен */
    }
    handlers.onSeek(timeAt(event.clientX))
  }
  scrub.addEventListener('pointerdown', startScrub)
  ruler.addEventListener('pointerdown', startScrub)
  scrub.addEventListener('pointermove', event => {
    if (scrubbing) handlers.onSeek(timeAt(event.clientX))
  })
  const endScrub = () => {
    scrubbing = false
  }
  scrub.addEventListener('pointerup', endScrub)
  scrub.addEventListener('pointercancel', endScrub)

  return {
    render,
    /**
     * Сказать что-то под шкалой.
     *
     * Отказы про клипы и курсор жили в шапке страницы, в полуэкране от места, где человек их
     * вызвал: нажал «разрезать» у края клипа — объяснение вспыхнуло вверху и погасло, а вывод
     * один, что клавиша не работает. Полоса подсказки под шкалой для этого и есть.
     *
     * Во время переноса молчим: там она занята номером будущего места, и это важнее.
     */
    say(text: string): void {
      if (drag) return
      window.clearTimeout(sayTimer)
      hint.textContent = text
      if (text) {
        sayTimer = window.setTimeout(() => {
          if (!drag) hint.textContent = emptyTrackHint(current.clips.length)
        }, SAY_MS)
      }
    },
    setPlayhead(time: number): void {
      // Двигаем курсор и подтягиваем прокрутку .timeline, если он ушёл за видимую часть.
      const left = time * current.pxPerSec
      playhead.style.left = `${left}px`
      const margin = 40
      if (left < view.scrollLeft + margin) view.scrollLeft = Math.max(0, left - margin)
      else if (left > view.scrollLeft + view.clientWidth - margin) {
        view.scrollLeft = left - view.clientWidth + margin
      }
    },
    setZoom(pxPerSec: number): void {
      render({ pxPerSec: Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, pxPerSec)) })
    },
    zoom(): number {
      return current.pxPerSec
    },
    select(id: string | null): void {
      selected = id
      markSelected()
    },
    selected(): string | null {
      return selected
    },
  }
}
