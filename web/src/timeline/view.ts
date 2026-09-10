/**
 * Шкала монтажа: блоки клипов с кадрами и волной, перетаскивание, подрезка ручками, курсор.
 *
 * Модуль только рисует и ловит указатель. Любая правка уходит наверх через onChange уже готовым
 * списком клипов: считает её модель (model.ts), а не эта обвязка.
 */
import { escapeHtml } from '../html'
import { barsFor, sliceThumbs, type AssetData } from '../strip'
import { clipDuration, dropTarget, fadeInto, layout, MIN_BLOCK_PX, moveClip, ms, sameOrder, totalDuration, trimClip, ZOOM_MAX, ZOOM_MIN, type Clip } from './model'

export type AssetInfo = { duration: number | null; files: { thumbs: string | null } }

export type TimelineHandlers = {
  onChange: (clips: Clip[]) => void
  onSeek: (time: number) => void
  onSelect: (id: string | null) => void
}

export type RenderInput = {
  clips: Clip[]
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
      <div class="scrub" id="tl-scrub" title="Перемотка"></div>
      <div class="track" id="tl-track"><div class="blocks" id="tl-blocks"></div><div class="drop-ghost" id="tl-drop" hidden></div><div class="playhead" id="tl-playhead"></div></div>
    </div>
    <div class="tl-hint muted" id="tl-hint"></div>`
  const view = el.querySelector('.timeline') as HTMLElement
  const ruler = el.querySelector('#tl-ruler') as HTMLElement
  const scrub = el.querySelector('#tl-scrub') as HTMLElement
  const track = el.querySelector('#tl-track') as HTMLElement
  const blocksBox = el.querySelector('#tl-blocks') as HTMLElement
  const playhead = el.querySelector('#tl-playhead') as HTMLElement
  const ghost = el.querySelector('#tl-drop') as HTMLElement
  let sayTimer = 0
  const hint = el.querySelector('#tl-hint') as HTMLElement

  let current: RenderInput = { clips: [], assets: new Map(), data: new Map(), pxPerSec: 40 }
  let selected: string | null = null
  let drag: {
    id: string
    index: number
    kind: 'move' | 'in' | 'out'
    startX: number
    clips: Clip[]
    moved: boolean
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

  function blockHtml(clip: Clip, width: number): string {
    const asset = current.assets.get(clip.asset_id)
    const info = current.data.get(clip.asset_id)
    const frames = sliceThumbs(info?.thumbs ?? null, { from: clip.in, to: clip.out }, width)
    const sprite = asset?.files.thumbs
    const cells = sprite
      ? frames
          .map(f => {
            const bg = f.background
            return `<i class="frame" style="left:${f.left}px;background-image:url('${escapeHtml(sprite)}');
              background-position:${bg.x}px ${bg.y}px;background-size:${bg.width}px ${bg.height}px"></i>`
          })
          .join('')
      : ''
    const marks =
      clip.snap_to_pauses && (!clip.in_verified || !clip.out_verified)
        ? '<span class="unverified" title="Граница не подтверждена паузой">!</span>'
        : ''
    return `${cells}<span class="label">${escapeHtml(clip.id)} · ${(clip.out - clip.in).toFixed(1)} с${marks}</span>
      <b class="handle handle-in"></b><b class="handle handle-out"></b>`
  }

  function render(input?: Partial<RenderInput>): void {
    if (drag) {
      pending = { ...(pending ?? {}), ...input }
      return
    }
    current = { ...current, ...input }
    const blocks = layout(current.clips, current.pxPerSec)
    const width = Math.max(200, totalDuration(current.clips) * current.pxPerSec)
    track.style.width = `${width}px`
    ruler.style.width = `${width}px`
    ruler.innerHTML = Array.from({ length: Math.ceil(width / (current.pxPerSec * 5)) + 1 }, (_, i) => {
      const seconds = i * 5
      return `<span class="tick" style="left:${seconds * current.pxPerSec}px">${seconds} с</span>`
    }).join('')

    blocksBox.querySelectorAll('.block').forEach(node => node.remove())
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
      node.innerHTML = blockHtml(clip, block.width)
      const info = current.data.get(clip.asset_id)
      node.appendChild(waveCanvas(barsFor(info?.peaks ?? null, { from: clip.in, to: clip.out }, Math.round(block.width)), block.width))
      blocksBox.appendChild(node)
    })
    if (!drag) hint.textContent = emptyTrackHint(current.clips.length)
  }

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

  // Полоса перемотки под линейкой и сама линейка: перетаскивание указателем двигает курсор.
  let scrubbing = false
  const startScrub = (event: PointerEvent) => {
    scrubbing = true
    scrub.setPointerCapture(event.pointerId)
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
