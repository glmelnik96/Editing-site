/**
 * Панель исходника: выбор готового файла, плеер прокси, выделение куска и кнопка «в шкалу».
 *
 * Выделение хранится числами, а не в DOM: кнопка отдаёт наверх готовый диапазон, а редактор
 * решает, что с ним делать.
 */
import { escapeHtml } from './html'
import type { Asset } from './assets'
import { fmtDuration } from './assets'

export type SourceHandlers = {
  onAdd: (asset: Asset, range: { from: number; to: number }) => void
}

const READY = new Set(['ready', 'proxy_ready'])

export function mountSource(el: HTMLElement, handlers: SourceHandlers) {
  el.innerHTML = `
    <main class="card">
      <h3>Исходник</h3>
      <select id="src-pick"><option value="">— выберите файл —</option></select>
      <div id="src-player"></div>
      <div class="row">
        <button id="src-mark-in" type="button">Начало</button>
        <button id="src-mark-out" type="button">Конец</button>
        <span id="src-range" class="muted">весь файл</span>
      </div>
      <button id="src-add" type="button" disabled>Добавить в шкалу</button>
      <p class="muted" id="src-note"></p>
    </main>`
  const pick = el.querySelector('#src-pick') as HTMLSelectElement
  const playerBox = el.querySelector('#src-player') as HTMLElement
  const rangeLabel = el.querySelector('#src-range') as HTMLElement
  const addButton = el.querySelector('#src-add') as HTMLButtonElement
  const note = el.querySelector('#src-note') as HTMLElement

  let assets: Asset[] = []
  let current: Asset | null = null
  let from = 0
  let to = 0

  const video = (): HTMLMediaElement | null => playerBox.querySelector('video, audio')

  function refreshRange(): void {
    rangeLabel.textContent = current ? `${fmtDuration(from)} — ${fmtDuration(to)}` : 'весь файл'
    addButton.disabled = !current || to - from < 0.1
  }

  function choose(asset: Asset | null): void {
    current = asset
    from = 0
    to = asset?.duration ?? 0
    playerBox.innerHTML = asset?.files.proxy
      ? `<video class="player" controls preload="metadata" src="${escapeHtml(asset.files.proxy)}"></video>`
      : ''
    note.textContent = asset && !asset.files.proxy ? 'Прокси ещё готовится: выделять можно будет после обработки.' : ''
    refreshRange()
  }

  pick.addEventListener('change', () => {
    choose(assets.find(a => a.id === pick.value) ?? null)
  })

  el.querySelector('#src-mark-in')!.addEventListener('click', () => {
    const player = video()
    if (!player || !current) return
    from = Math.min(player.currentTime, to - 0.1)
    refreshRange()
  })

  el.querySelector('#src-mark-out')!.addEventListener('click', () => {
    const player = video()
    if (!player || !current) return
    to = Math.max(player.currentTime, from + 0.1)
    refreshRange()
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
