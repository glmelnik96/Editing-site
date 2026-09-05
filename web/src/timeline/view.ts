/**
 * Шкала монтажа: блоки клипов с кадрами и волной, перетаскивание, подрезка ручками, курсор.
 *
 * Модуль только рисует и ловит указатель. Любая правка уходит наверх через onChange уже готовым
 * списком клипов: считает её модель (model.ts), а не эта обвязка.
 */
import { escapeHtml } from '../html'
import { barsFor, sliceThumbs, type AssetData } from '../strip'
import { clipDuration, dropTarget, layout, MIN_BLOCK_PX, moveClip, ms, totalDuration, trimClip, type Clip } from './model'

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
const WAVE_HEIGHT = 22
const HANDLE_PX = 8
const CLICK_SLOP_PX = 4 // сдвиг меньше этого — это клик, а не перенос

function waveCanvas(bars: number[], width: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(width))
  canvas.height = WAVE_HEIGHT
  canvas.className = 'wave'
  const ctx = canvas.getContext('2d')
  if (ctx) {
    ctx.fillStyle = 'rgba(255,255,255,.55)'
    bars.forEach((value, x) => {
      const height = Math.max(1, (value / 255) * WAVE_HEIGHT)
      ctx.fillRect(x, WAVE_HEIGHT - height, 1, height)
    })
  }
  return canvas
}

/** Шкала: возвращает управление для редактора. */
export function mountTimeline(el: HTMLElement, handlers: TimelineHandlers) {
  el.innerHTML = `
    <div class="timeline">
      <div class="ruler" id="tl-ruler"></div>
      <div class="scrub" id="tl-scrub" title="Перемотка"></div>
      <div class="track" id="tl-track"><div class="drop-ghost" id="tl-drop" hidden></div><div class="playhead" id="tl-playhead"></div></div>
      <div class="tl-hint muted" id="tl-hint"></div>
    </div>`
  const view = el.querySelector('.timeline') as HTMLElement
  const ruler = el.querySelector('#tl-ruler') as HTMLElement
  const scrub = el.querySelector('#tl-scrub') as HTMLElement
  const track = el.querySelector('#tl-track') as HTMLElement
  const playhead = el.querySelector('#tl-playhead') as HTMLElement
  const ghost = el.querySelector('#tl-drop') as HTMLElement
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
    current = { ...current, ...input }
    const blocks = layout(current.clips, current.pxPerSec)
    const width = Math.max(200, totalDuration(current.clips) * current.pxPerSec)
    track.style.width = `${width}px`
    ruler.style.width = `${width}px`
    ruler.innerHTML = Array.from({ length: Math.ceil(width / (current.pxPerSec * 5)) + 1 }, (_, i) => {
      const seconds = i * 5
      return `<span class="tick" style="left:${seconds * current.pxPerSec}px">${seconds} с</span>`
    }).join('')

    track.querySelectorAll('.block').forEach(node => node.remove())
    blocks.forEach((block, index) => {
      const clip = current.clips[index]
      const node = document.createElement('div')
      node.className = `block${clip.id === selected ? ' selected' : ''}`
      node.style.left = `${block.left}px`
      node.style.width = `${block.width}px`
      node.style.height = `${TRACK_HEIGHT}px`
      node.dataset.id = clip.id
      node.dataset.index = String(index)
      node.innerHTML = blockHtml(clip, block.width)
      const info = current.data.get(clip.asset_id)
      node.appendChild(waveCanvas(barsFor(info?.peaks ?? null, { from: clip.in, to: clip.out }, Math.round(block.width)), block.width))
      track.appendChild(node)
    })
  }

  function finishDrag(clientX: number): void {
    if (!drag) return
    if (!drag.moved) {
      // Клик без переноса: ставим курсор туда, куда ткнули (блок уже выделён в pointerdown).
      // render() тут не будет — снимаем класс переноса вручную, иначе блок останется затемнённым.
      track.querySelector(`.block[data-id="${CSS.escape(drag.id)}"]`)?.classList.remove('dragging')
      handlers.onSeek(timeAt(clientX))
      ghost.hidden = true
      drag = null
      hint.textContent = ''
      return
    }
    const dx = clientX - drag.startX
    if (drag.kind === 'move') {
      // Та же функция, что рисовала призрак: показ и результат не могут разойтись.
      const preview = dropTarget(current.clips, drag.index, timeAt(clientX))
      if (preview && preview.to !== drag.index) handlers.onChange(moveClip(drag.clips, drag.index, preview.to))
      else render()
    } else {
      const clip = drag.clips[drag.index]
      const delta = dx / current.pxPerSec
      const duration = current.assets.get(clip.asset_id)?.duration ?? undefined
      const edges = drag.kind === 'in' ? { in: ms(clip.in + delta) } : { out: ms(clip.out + delta) }
      handlers.onChange(trimClip(drag.clips, clip.id, edges, { duration: duration ?? undefined }))
    }
    ghost.hidden = true
    drag = null
    hint.textContent = ''
  }

  track.addEventListener('pointerdown', event => {
    const target = event.target as HTMLElement
    const node = target.closest('.block') as HTMLElement | null
    if (!node) {
      handlers.onSeek(timeAt(event.clientX))
      return
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
    drag = { id, index, kind, startX: event.clientX, clips: current.clips, moved: false }
    track.setPointerCapture(event.pointerId)
    render()
    // Помечаем переносимый блок отдельным классом — render() пересобирает блоки заново, поэтому это после него.
    track.querySelector(`.block[data-id="${CSS.escape(id)}"]`)?.classList.add('dragging')
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

  const stop = (event: PointerEvent) => {
    if (drag) finishDrag(event.clientX)
  }
  track.addEventListener('pointerup', stop)
  track.addEventListener('pointercancel', stop)

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
      render({ pxPerSec: Math.max(4, Math.min(400, pxPerSec)) })
    },
    zoom(): number {
      return current.pxPerSec
    },
    select(id: string | null): void {
      selected = id
      render()
    },
    selected(): string | null {
      return selected
    },
  }
}
