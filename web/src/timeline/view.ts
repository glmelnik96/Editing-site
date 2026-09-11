/**
 * Шкала монтажа: блоки клипов с кадрами и волной, перетаскивание, подрезка ручками, курсор.
 *
 * Модуль только рисует и ловит указатель. Любая правка уходит наверх через onChange уже готовым
 * списком клипов: считает её модель (model.ts), а не эта обвязка.
 */
import { fmtDuration } from '../assets'
import { escapeHtml } from '../html'
import { pieceLabel } from '../names'
import type { Overlay, Sound } from '../project'
import { barsFor, sliceThumbs, type AssetData, type ThumbsMeta } from '../strip'
import { clipDuration, dropTarget, fadeInto, layout, MIN_BLOCK_PX, moveClip, ms, rulerTicks, sameOrder, totalDuration, trimClip, zoomFloor, ZOOM_MAX, type Clip } from './model'
import { laneBlocks, laneEnd, laneSpan, moveItem, type Placed } from './sounds'
import { tileRange, tileWidth, visibleTiles, type Tile } from './tiles'

export type AssetInfo = {
  kind?: string
  /** Имя записи для подписи блока. */
  name?: string
  /** Есть ли у записи звук: без него у клипа нет блока на дорожке звука. */
  hasAudio?: boolean | null
  duration: number | null
  files: { thumbs: string | null }
}

export type TimelineHandlers = {
  onChange: (clips: Clip[]) => void
  /** Правка звуковой дорожки: готовый список звуков, как onChange для клипов. */
  onSoundsChange: (sounds: Sound[]) => void
  /** Правка наложений: то же для колеи над клипами. */
  onOverlaysChange: (overlays: Overlay[]) => void
  onSeek: (time: number) => void
  onSelect: (id: string | null) => void
}

export type RenderInput = {
  clips: Clip[]
  sounds: Sound[]
  overlays: Overlay[]
  assets: Map<string, AssetInfo>
  data: Map<string, AssetData>
  pxPerSec: number
}

const TRACK_HEIGHT = 72
// Высота клетки кадра: у клипа как в спрайте, у куска колеи — под её низкий блок.
const CLIP_FRAME_H = 90
const LANE_FRAME_H = 44
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
const TICK_LABEL_PX = 56 // место под подпись деления вроде «1:20:00»

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
        <!-- Наложения — колея над клипами: картинка или видео поверх основы лежат по своему
             времени и клипы не сдвигают. Пустая видна всегда, как и звуковая. -->
        <div class="overlay-track empty" id="tl-overlays"
          data-empty="Наложения: картинка или видео поверх основы. В «Исходниках» — кнопка «Поверх видео»"><i class="lane-tag" title="Вторая дорожка видео: наложения">V2</i></div>
        <div class="track" id="tl-track"><i class="lane-tag" title="Первая дорожка видео: клипы">V1</i><div class="blocks" id="tl-blocks"></div><div class="drop-ghost" id="tl-drop" hidden></div></div>
        <!-- Звук клипов — своя колея под картинкой, привязанная к ней: блок звука повторяет блок
             клипа, выбирается и двигается вместе с ним. Так на шкале две дорожки видео и две
             звука, а волну речи читают на своей высоте, не деля место с кадрами. -->
        <div class="track-audio" id="tl-clip-audio"><i class="lane-tag" title="Первая дорожка звука: звук клипов">A1</i><div class="blocks" id="tl-audio-blocks"></div></div>
        <!-- Звуковая дорожка — своя колея под клипами: звук лежит по своему времени и клипы не
             сдвигает. Пустая она видна всё равно: появляющаяся колея переставляла бы шкалу
             под руками, а подпись в ней объясняет, откуда туда класть. -->
        <div class="sound-track empty" id="tl-sounds"
          data-empty="Звуковая дорожка: озвучка, шумы и музыка поверх речи. Положите звук из «Исходников»"><i class="lane-tag" title="Вторая дорожка звука: звуки со своим местом">A2</i></div>
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
  const audioTrack = el.querySelector('#tl-clip-audio') as HTMLElement
  const audioBox = el.querySelector('#tl-audio-blocks') as HTMLElement
  const soundTrack = el.querySelector('#tl-sounds') as HTMLElement
  const overlayTrack = el.querySelector('#tl-overlays') as HTMLElement
  const playhead = el.querySelector('#tl-playhead') as HTMLElement
  const ghost = el.querySelector('#tl-drop') as HTMLElement
  let rulerWidth = 200 // ширина шкалы в пикселях после последней перерисовки — для делений
  let sayTimer = 0
  // До какого момента под шкалой стоит сказанное: перерисовка его не трогает.
  let sayUntil = 0
  const hint = el.querySelector('#tl-hint') as HTMLElement

  let current: RenderInput = {
    clips: [],
    sounds: [],
    overlays: [],
    assets: new Map(),
    data: new Map(),
    pxPerSec: 40,
  }
  let selected: string | null = null
  let drag: {
    id: string
    index: number
    kind: 'move' | 'in' | 'out'
    startX: number
    clips: Clip[]
    moved: boolean
  } | null = null
  // Перенос куска свободной колеи. Отдельно от переноса клипа: у куска нет ни очереди, ни ручек,
  // он просто едет по своей колее, и смешивать два состояния значило бы проверять вид куска на
  // каждом шаге. Указатель один, поэтому и перенос один на обе колеи.
  let laneDrag: {
    id: string
    startX: number
    at: number
    items: Placed[]
    moved: boolean
    node: HTMLElement
    lane: Lane
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
    const label = pieceLabel(current.assets.get(clip.asset_id)?.name, clip.id, `${(clip.out - clip.in).toFixed(1)} с`)
    return `<span class="label" title="${escapeHtml(clip.id)}">${escapeHtml(label)}${marks}</span>
      <b class="handle handle-in"></b><b class="handle handle-out"></b>`
  }

  /** Где кончается содержимое шкалы: последний клип или свисающий за него звук либо наложение. */
  function contentEnd(input: RenderInput): number {
    const total = totalDuration(input.clips)
    return Math.max(total, laneEnd(input.sounds, total), laneEnd(input.overlays, total))
  }

  /** Нижняя граница масштаба сейчас: у длинного ролика — весь ролик в видимой части шкалы. */
  function zoomMin(): number {
    return zoomFloor(contentEnd(current), view.clientWidth)
  }

  function render(input?: Partial<RenderInput>): void {
    if (drag || laneDrag) {
      pending = { ...(pending ?? {}), ...input }
      return
    }
    current = { ...current, ...input }
    const blocks = layout(current.clips, current.pxPerSec)
    // Шкала дотягивается и до звука, свисающего за конец ролика: сборка его хвост обрежет, но
    // увидеть и схватить его, чтобы вернуть назад, человек должен.
    const end = contentEnd(current)
    const width = Math.max(200, end * current.pxPerSec)
    track.style.width = `${width}px`
    soundTrack.style.width = `${width}px`
    overlayTrack.style.width = `${width}px`
    audioTrack.style.width = `${width}px`
    lane.style.width = `${width}px`
    ruler.style.width = `${width}px`
    rulerWidth = width

    blocksBox.querySelectorAll('.block').forEach(node => node.remove())
    audioBox.querySelectorAll('.block').forEach(node => node.remove())
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
      // Кадры — на дорожке видео, волна — на дорожке звука под ней.
      strips.push(strip(node, clip.asset_id, clip.in, clip.out, block.left, block.width, true, false))
      blocksBox.appendChild(node)
      const asset = current.assets.get(clip.asset_id)
      // Пока запись не приехала, звук считаем есть: блок, который то появляется, то исчезает, хуже.
      const hasAudio = asset ? asset.kind !== 'image' && asset.hasAudio !== false : true
      if (!hasAudio) return
      const audio = document.createElement('div')
      audio.className = `block audio-block${clip.id === selected ? ' selected' : ''}`
      audio.style.left = `${block.left}px`
      audio.style.width = `${block.width}px`
      audio.style.height = `${LANE_FRAME_H}px`
      audio.style.zIndex = clip.id === selected ? String(SELECTED_Z) : String(index + 1)
      audio.dataset.id = clip.id
      audio.dataset.index = String(index)
      strips.push(strip(audio, clip.asset_id, clip.in, clip.out, block.left, block.width, false, true, LANE_FRAME_H))
      audioBox.appendChild(audio)
    })
    soundLane.paint(current.sounds)
    overlayLane.paint(current.overlays)
    // Сказанное под шкалой перерисовка не стирает: автосохранение отвечает через полсекунды после
    // правки, и отказ на следующую правку исчезал, не успев быть прочитанным.
    if (!drag && Date.now() >= sayUntil) hint.textContent = emptyTrackHint(current.clips.length)
    // Деления — после блоков: пока старые блоки на месте, прокрутка ещё считает шкалу широкой.
    paintTicks()
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
    wave: boolean
    frameHeight: number
  }
  let strips: Strip[] = []
  let paintQueued = false

  /** Завести полосу блока: пустой слой плиток первым ребёнком, чтобы подпись и ручки были поверх. */
  function strip(
    node: HTMLElement, assetId: string, from: number, to: number, left: number, width: number,
    frames: boolean, wave = true, frameHeight = CLIP_FRAME_H,
  ): Strip {
    const box = document.createElement('div')
    box.className = 'wave-tiles'
    node.prepend(box)
    return { box, assetId, from, to, left, width, frames, wave, frameHeight }
  }

  /**
   * Раскладка спрайта под высоту клетки блока: у клипа кадр 90 px, у куска колеи — 44.
   * Смещения и размеры фона считаются от этих же чисел, поэтому спрайт просто масштабируется,
   * а не режется по высоте.
   */
  function cellMeta(s: Strip, meta: ThumbsMeta): ThumbsMeta {
    const scale = s.frameHeight / meta.height
    return { ...meta, width: Math.max(1, Math.round(meta.width * scale)), height: s.frameHeight }
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
      const meta = cellMeta(s, info.thumbs)
      node.innerHTML = sliceThumbs(meta, { from: s.from, to: s.to }, s.width)
        .filter(f => f.left >= tile.x0 && f.left < tile.x1)
        .map(f => {
          const bg = f.background
          return `<i class="frame" style="left:${f.left - tile.x0}px;width:${meta.width}px;height:${meta.height}px;
            background-image:url('${escapeHtml(sprite)}');background-position:${bg.x}px ${bg.y}px;
            background-size:${bg.width}px ${bg.height}px"></i>`
        })
        .join('')
    }
    if (s.wave) {
      const range = tileRange(tile, s.width, s.from, s.to)
      const width = tile.x1 - tile.x0
      node.appendChild(waveCanvas(barsFor(info?.peaks ?? null, range, Math.round(width)), width))
    }
    return node
  }

  /**
   * Деления линейки — только в видимой части шкалы, как на часах и с шагом под масштаб (rulerTicks).
   * Перерисовываются вместе с плитками: при прокрутке, смене размера и после render().
   */
  function paintTicks(): void {
    ruler.innerHTML = rulerTicks(current.pxPerSec, rulerWidth, view.scrollLeft, view.clientWidth, TICK_LABEL_PX)
      .map(tick => `<span class="tick" style="left:${tick.left}px">${fmtDuration(tick.seconds)}</span>`)
      .join('')
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
      const size = tileWidth(s.frames && info?.thumbs ? cellMeta(s, info.thumbs).width : null)
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
      paintTicks()
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
    for (const box of [blocksBox, audioBox]) {
      box.querySelectorAll<HTMLElement>('.block').forEach(node => {
        const mine = node.dataset.id === selected
        node.classList.toggle('selected', mine)
        node.style.zIndex = mine ? String(SELECTED_Z) : String(Number(node.dataset.index ?? 0) + 1)
      })
    }
    for (const laneTrack of [soundTrack, overlayTrack]) {
      laneTrack.querySelectorAll<HTMLElement>('.lane-block').forEach(node => {
        node.classList.toggle('selected', node.dataset.id === selected)
      })
    }
  }

  /** Перенос отменён системой (жест перехватил браузер): возвращаем всё как было, правки нет. */
  function abortDrag(): void {
    if (!drag) return
    drag = null
    ghost.hidden = true
    hint.textContent = ''
    flushPending()
  }

  /**
   * Нажатие на клип — на дорожке видео или на его звуке под ней: выбор, а дальше перенос или
   * подрезка. Звуковой блок привязан к клипу и повторяет его геометрию, поэтому обработчик
   * один; ручек подрезки у звука нет, и с него клип только выбирают и двигают.
   */
  function clipPointerDown(event: PointerEvent, surface: HTMLElement): void {
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
      surface.setPointerCapture(event.pointerId)
    } catch {
      captured = false
    }
    const id = node.dataset.id ?? ''
    const index = Number(node.dataset.index ?? 0)
    selected = id
    handlers.onSelect(id)
    const rect = node.getBoundingClientRect()
    const trims = surface === track
    const kind: 'move' | 'in' | 'out' = !trims
      ? 'move'
      : target.classList.contains('handle-in')
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
  }

  function clipPointerMove(event: PointerEvent): void {
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
  }

  for (const surface of [track, audioTrack]) {
    surface.addEventListener('pointerdown', event => clipPointerDown(event, surface))
    surface.addEventListener('pointermove', clipPointerMove)
    surface.addEventListener('pointerup', event => finishDrag(event.clientX))
    // Отмена — это не «отпустил здесь»: раньше прерванный жест применял перенос по последней
    // точке, и клип вставал не туда, куда его вели.
    surface.addEventListener('pointercancel', abortDrag)
    // Страховка: если захват потерян, а pointerup до нас не дошёл, шкала осталась бы замороженной.
    surface.addEventListener('lostpointercapture', abortDrag)
  }

  /**
   * Свободная колея: куски со своим временем на шкале, перенос мышью, общее с клипами выделение.
   *
   * Звуки под клипами и наложения над ними — одна механика. Различаются содержимым блока (волна
   * или кадры), классом для цвета и тем, куда уходит правка. Раньше это был код звуковой дорожки;
   * копировать его второй раз нельзя — расхождения в переносе ловились бы только руками.
   */
  type LaneItem = Placed & { asset_id: string }
  type Lane = {
    track: HTMLElement
    block: string
    frames: boolean
    wave: boolean
    items: () => LaneItem[]
    pending: () => LaneItem[] | undefined
    emit: (items: LaneItem[]) => void
  }

  function freeLane(lane: Lane): { paint: (items: LaneItem[]) => void } {
    const laneTrack = lane.track

    // Щелчок по куску выбирает его, протаскивание перекладывает на новое место, щелчок по пустой
    // колее перематывает — как по пустому месту дорожки клипов.
    laneTrack.addEventListener('pointerdown', event => {
      const target = event.target as HTMLElement
      const node = target.closest('.lane-block') as HTMLElement | null
      if (!node) {
        handlers.onSeek(timeAt(event.clientX))
        return
      }
      // Захват первым делом и по тем же причинам, что у клипа: без него отпускание за краем
      // шкалы до нас не дойдёт, и замороженная на время переноса шкала так и осталась бы стоять.
      let captured = true
      try {
        laneTrack.setPointerCapture(event.pointerId)
      } catch {
        captured = false
      }
      const id = node.dataset.id ?? ''
      selected = id
      handlers.onSelect(id)
      markSelected()
      const items = lane.items()
      const item = items.find(entry => entry.id === id)
      if (!captured || !item) return
      laneDrag = { id, startX: event.clientX, at: item.at, items, moved: false, node, lane }
      node.classList.add('dragging')
    })

    laneTrack.addEventListener('pointermove', event => {
      if (!laneDrag || laneDrag.lane !== lane) return
      laneDrag.moved = laneDrag.moved || Math.abs(event.clientX - laneDrag.startX) > CLICK_SLOP_PX
      if (!laneDrag.moved) return
      const at = Math.max(0, laneDrag.at + (event.clientX - laneDrag.startX) / current.pxPerSec)
      laneDrag.node.style.left = `${at * current.pxPerSec}px`
      hint.textContent = `«${laneDrag.id}» ляжет на ${at.toFixed(2)} с`
    })

    /** Конец переноса. apply=false — жест отменила система: правки нет, всё как было. */
    function finish(clientX: number, apply: boolean): void {
      if (!laneDrag || laneDrag.lane !== lane) return
      const active = laneDrag
      laneDrag = null
      hint.textContent = ''
      if (!active.moved) {
        if (apply) handlers.onSeek(timeAt(clientX))
        flushPending()
        return
      }
      // Свежий список, если пока тянули пришёл тот же набор: так не теряется то, что успел
      // нормализовать сервер. Изменился состав — берём список, с которым начинали.
      const fresh = lane.pending()
      const same =
        fresh !== undefined &&
        fresh.length === active.items.length &&
        fresh.every((item, i) => item.id === active.items[i].id)
      const base = (same && fresh ? fresh : active.items) as LaneItem[]
      const at = active.at + (clientX - active.startX) / current.pxPerSec
      flushPending()
      if (apply) lane.emit(moveItem(base, active.id, at))
    }

    laneTrack.addEventListener('pointerup', event => finish(event.clientX, true))
    laneTrack.addEventListener('pointercancel', () => finish(0, false))
    laneTrack.addEventListener('lostpointercapture', () => finish(0, false))

    return {
      paint(items: LaneItem[]): void {
        laneTrack.querySelectorAll('.lane-block').forEach(node => node.remove())
        laneTrack.classList.toggle('empty', items.length === 0)
        const total = totalDuration(current.clips)
        laneBlocks(items, current.pxPerSec, total).forEach((block, index) => {
          const item = items[index]
          const node = document.createElement('div')
          node.className = `lane-block ${lane.block}${item.id === selected ? ' selected' : ''}`
          node.style.left = `${block.left}px`
          node.style.width = `${block.width}px`
          node.dataset.id = item.id
          const length = item.loop ? 'по кругу' : `${laneSpan(item, total).toFixed(1)} с`
          const label = pieceLabel(current.assets.get(item.asset_id)?.name, item.id, length)
          node.innerHTML = `<span class="label" title="${escapeHtml(item.id)}">${escapeHtml(label)}</span>`
          strips.push(
            strip(node, item.asset_id, item.in, item.out, block.left, block.width, lane.frames, lane.wave, LANE_FRAME_H),
          )
          laneTrack.appendChild(node)
        })
      },
    }
  }

  const soundLane = freeLane({
    track: soundTrack,
    block: 'sound-block',
    frames: false,
    wave: true,
    items: () => current.sounds,
    pending: () => pending?.sounds,
    emit: items => handlers.onSoundsChange(items as Sound[]),
  })
  const overlayLane = freeLane({
    track: overlayTrack,
    block: 'overlay-block',
    frames: true,
    wave: false,
    items: () => current.overlays,
    pending: () => pending?.overlays,
    emit: items => handlers.onOverlaysChange(items as Overlay[]),
  })

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
      sayUntil = text ? Date.now() + SAY_MS : 0
      if (text) {
        sayTimer = window.setTimeout(() => {
          sayUntil = 0
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
      render({ pxPerSec: Math.max(zoomMin(), Math.min(ZOOM_MAX, pxPerSec)) })
    },
    zoom(): number {
      return current.pxPerSec
    },
    /** Нижняя граница масштаба для текущего ролика и ширины окна — ноль ползунка. */
    zoomMin,
    select(id: string | null): void {
      selected = id
      markSelected()
    },
    selected(): string | null {
      return selected
    },
  }
}
