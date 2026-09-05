/**
 * Панель исходника: выбор готового файла, плеер прокси, выделение куска и кнопка «в шкалу».
 *
 * Выделение хранится числами, а не в DOM: кнопка отдаёт наверх готовый диапазон, а редактор
 * решает, что с ним делать.
 */
import { escapeHtml } from './html'
import type { Asset } from './assets'
import { formatTimecode, parseTimecode } from './timecode'

export type SourceHandlers = {
  onAdd: (asset: Asset, range: { from: number; to: number }) => void
}

const READY = new Set(['ready', 'proxy_ready'])
const MIN_PIECE = 0.1

export function mountSource(el: HTMLElement, handlers: SourceHandlers) {
  el.innerHTML = `
    <main class="card">
      <h3>Исходник</h3>
      <select id="src-pick"><option value="">— выберите файл —</option></select>
      <div id="src-player"></div>
      <div class="src-strip" id="src-strip" title="Клик — перемотка, ручки — границы куска">
        <div class="src-sel" id="src-sel"></div>
        <b class="src-handle src-handle-in" id="src-h-in"></b>
        <b class="src-handle src-handle-out" id="src-h-out"></b>
        <i class="src-cursor" id="src-cursor"></i>
      </div>
      <div class="row">
        <button id="src-mark-in" type="button" title="Взять начало с плеера">Начало</button>
        <input id="src-in" class="tc" inputmode="decimal" placeholder="0:00.0" />
        <button id="src-mark-out" type="button" title="Взять конец с плеера">Конец</button>
        <input id="src-out" class="tc" inputmode="decimal" placeholder="0:00.0" />
      </div>
      <div class="row">
        <span id="src-range" class="muted">весь файл</span>
        <button id="src-add" type="button" disabled>Добавить в шкалу</button>
      </div>
      <p class="muted" id="src-note"></p>
    </main>`
  const pick = el.querySelector('#src-pick') as HTMLSelectElement
  const playerBox = el.querySelector('#src-player') as HTMLElement
  const strip = el.querySelector('#src-strip') as HTMLElement
  const sel = el.querySelector('#src-sel') as HTMLElement
  const handleIn = el.querySelector('#src-h-in') as HTMLElement
  const handleOut = el.querySelector('#src-h-out') as HTMLElement
  const cursor = el.querySelector('#src-cursor') as HTMLElement
  const inputIn = el.querySelector('#src-in') as HTMLInputElement
  const inputOut = el.querySelector('#src-out') as HTMLInputElement
  const rangeLabel = el.querySelector('#src-range') as HTMLElement
  const addButton = el.querySelector('#src-add') as HTMLButtonElement
  const note = el.querySelector('#src-note') as HTMLElement

  let assets: Asset[] = []
  let current: Asset | null = null
  let from = 0
  let to = 0

  const video = (): HTMLMediaElement | null => playerBox.querySelector('video, audio')

  /** Полоса, поля и подпись показывают одно и то же состояние: from, to и длительность файла. */
  function refreshRange(): void {
    const total = current?.duration ?? 0
    const pct = (value: number) => (total > 0 ? `${(value / total) * 100}%` : '0%')
    sel.style.left = pct(from)
    sel.style.width = total > 0 ? `${((to - from) / total) * 100}%` : '0%'
    handleIn.style.left = pct(from)
    handleOut.style.left = pct(to)
    strip.classList.toggle('empty', !current)
    if (document.activeElement !== inputIn) inputIn.value = formatTimecode(from)
    if (document.activeElement !== inputOut) inputOut.value = formatTimecode(to)
    inputIn.classList.remove('bad')
    inputOut.classList.remove('bad')
    rangeLabel.textContent = current
      ? `кусок ${formatTimecode(to - from)} из ${formatTimecode(total)}`
      : 'весь файл'
    addButton.disabled = !current || to - from < MIN_PIECE
  }

  /** Ставит границу, не давая ей вывернуться наизнанку или выйти за длительность файла. */
  function setEdge(edge: 'in' | 'out', value: number): void {
    const total = current?.duration ?? 0
    if (edge === 'in') from = Math.max(0, Math.min(value, to - MIN_PIECE))
    else to = Math.min(total, Math.max(value, from + MIN_PIECE))
    refreshRange()
  }

  function readInput(input: HTMLInputElement, edge: 'in' | 'out'): void {
    if (!current) return
    const parsed = parseTimecode(input.value)
    if (parsed === null) {
      // Непонятный ввод не двигает границу: подсвечиваем поле и оставляем прежнее значение.
      input.classList.add('bad')
      return
    }
    setEdge(edge, parsed)
  }

  for (const [input, edge] of [[inputIn, 'in'], [inputOut, 'out']] as const) {
    input.addEventListener('change', () => readInput(input, edge))
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault()
        readInput(input, edge)
      }
    })
  }

  const timeAtStrip = (clientX: number): number => {
    const rect = strip.getBoundingClientRect()
    const total = current?.duration ?? 0
    if (rect.width <= 0 || total <= 0) return 0
    return Math.max(0, Math.min(total, ((clientX - rect.left) / rect.width) * total))
  }

  let dragEdge: 'in' | 'out' | null = null
  strip.addEventListener('pointerdown', event => {
    if (!current) return
    const target = event.target as HTMLElement
    strip.setPointerCapture(event.pointerId)
    if (target === handleIn || target === handleOut) {
      dragEdge = target === handleIn ? 'in' : 'out'
      return
    }
    const player = video()
    if (player) player.currentTime = timeAtStrip(event.clientX)
  })
  strip.addEventListener('pointermove', event => {
    if (dragEdge) setEdge(dragEdge, timeAtStrip(event.clientX))
  })
  const endStripDrag = () => {
    dragEdge = null
  }
  strip.addEventListener('pointerup', endStripDrag)
  strip.addEventListener('pointercancel', endStripDrag)

  function choose(asset: Asset | null): void {
    current = asset
    from = 0
    to = asset?.duration ?? 0
    playerBox.innerHTML = asset?.files.proxy
      ? `<video class="player" controls preload="metadata" src="${escapeHtml(asset.files.proxy)}"></video>`
      : ''
    note.textContent = asset && !asset.files.proxy ? 'Прокси ещё готовится: выделять можно будет после обработки.' : ''
    const player = video()
    if (player) {
      player.addEventListener('timeupdate', () => {
        const total = current?.duration ?? 0
        cursor.style.left = total > 0 ? `${(player.currentTime / total) * 100}%` : '0%'
      })
    }
    refreshRange()
  }

  pick.addEventListener('change', () => {
    choose(assets.find(a => a.id === pick.value) ?? null)
  })

  el.querySelector('#src-mark-in')!.addEventListener('click', () => {
    const player = video()
    if (player && current) setEdge('in', player.currentTime)
  })
  el.querySelector('#src-mark-out')!.addEventListener('click', () => {
    const player = video()
    if (player && current) setEdge('out', player.currentTime)
  })

  addButton.addEventListener('click', () => {
    if (!current) return
    handlers.onAdd(current, { from, to })
  })

  return {
    /** Список файлов: в шкалу годятся только готовые видео. */
    setAssets(list: Asset[]): void {
      assets = list.filter(a => a.kind === 'video' && READY.has(a.status))
      const keep = current?.id ?? ''
      pick.innerHTML =
        '<option value="">— выберите файл —</option>' +
        assets
          .map(a => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.original_name)}</option>`)
          .join('')
      if (assets.some(a => a.id === keep)) pick.value = keep
      else choose(null)
    },
    current(): Asset | null {
      return current
    },
  }
}
