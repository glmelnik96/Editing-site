/**
 * Блок музыки под исходниками: выбор трека, микс, дакинг.
 *
 * Правка уходит наверх готовым объектом music: редактор кладёт его в документ и сохраняет.
 */
import { escapeHtml } from './html'
import type { Asset } from './assets'
import type { Music } from './project'

export type MusicHandlers = {
  onChange: (music: Music | null) => void
}

const READY = new Set(['ready', 'proxy_ready'])

export function defaultMusic(assetId: string): Music {
  return {
    asset_id: assetId,
    volume: 0.25,
    speech_volume: 1,
    fade_in: 0,
    fade_out: 0,
    loop: true,
    duck: true,
  }
}

export function musicAssets(list: Asset[]): Asset[] {
  return list.filter(a => (a.kind === 'audio' || a.kind === 'video') && READY.has(a.status))
}

export function mountMusic(el: HTMLElement, handlers: MusicHandlers) {
  el.innerHTML = `
    <main class="card music-block">
      <h3>Музыка</h3>
      <select id="mus-pick"><option value="">— нет —</option></select>
      <label class="music-row">Речь
        <input id="mus-speech" type="range" min="0" max="1" step="0.01" />
      </label>
      <label class="music-row">Музыка
        <input id="mus-volume" type="range" min="0" max="1" step="0.01" />
      </label>
      <div class="row">
        <label>Fade in <input id="mus-fade-in" class="tc" type="number" min="0" step="0.1" /></label>
        <label>Fade out <input id="mus-fade-out" class="tc" type="number" min="0" step="0.1" /></label>
      </div>
      <label class="music-row"><input id="mus-loop" type="checkbox" /> по кругу</label>
      <label class="music-row"><input id="mus-duck" type="checkbox" /> приглушать под речь</label>
      <button id="mus-clear" type="button">Убрать музыку</button>
    </main>`

  const pick = el.querySelector('#mus-pick') as HTMLSelectElement
  const speech = el.querySelector('#mus-speech') as HTMLInputElement
  const volume = el.querySelector('#mus-volume') as HTMLInputElement
  const fadeIn = el.querySelector('#mus-fade-in') as HTMLInputElement
  const fadeOut = el.querySelector('#mus-fade-out') as HTMLInputElement
  const loop = el.querySelector('#mus-loop') as HTMLInputElement
  const duck = el.querySelector('#mus-duck') as HTMLInputElement
  const clear = el.querySelector('#mus-clear') as HTMLButtonElement

  let assets: Asset[] = []
  let current: Music | null = null
  let painting = false

  function paint(): void {
    painting = true
    pick.value = current?.asset_id ?? ''
    speech.value = String(current?.speech_volume ?? 1)
    volume.value = String(current?.volume ?? 0.25)
    fadeIn.value = String(current?.fade_in ?? 0)
    fadeOut.value = String(current?.fade_out ?? 0)
    loop.checked = current?.loop ?? true
    duck.checked = current?.duck ?? false
    const on = current !== null
    speech.disabled = !on
    volume.disabled = !on
    fadeIn.disabled = !on
    fadeOut.disabled = !on
    loop.disabled = !on
    duck.disabled = !on
    clear.disabled = !on
    painting = false
  }

  function emit(next: Music | null): void {
    current = next
    paint()
    handlers.onChange(current)
  }

  function patch(partial: Partial<Music>): void {
    if (!current) return
    emit({ ...current, ...partial })
  }

  pick.addEventListener('change', () => {
    if (painting) return
    const id = pick.value
    if (!id) {
      emit(null)
      return
    }
    if (!current) emit(defaultMusic(id))
    else patch({ asset_id: id })
  })
  speech.addEventListener('input', () => {
    if (!painting) patch({ speech_volume: Number(speech.value) })
  })
  volume.addEventListener('input', () => {
    if (!painting) patch({ volume: Number(volume.value) })
  })
  fadeIn.addEventListener('change', () => {
    if (!painting) patch({ fade_in: Math.max(0, Number(fadeIn.value) || 0) })
  })
  fadeOut.addEventListener('change', () => {
    if (!painting) patch({ fade_out: Math.max(0, Number(fadeOut.value) || 0) })
  })
  loop.addEventListener('change', () => {
    if (!painting) patch({ loop: loop.checked })
  })
  duck.addEventListener('change', () => {
    if (!painting) patch({ duck: duck.checked })
  })
  clear.addEventListener('click', () => emit(null))

  paint()

  return {
    setAssets(list: Asset[]): void {
      assets = musicAssets(list)
      const keep = pick.value
      pick.innerHTML =
        '<option value="">— нет —</option>' +
        assets
          .map(a => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.original_name)}</option>`)
          .join('')
      if (current && assets.some(a => a.id === current?.asset_id)) pick.value = current.asset_id
      else if (assets.some(a => a.id === keep)) pick.value = keep
    },
    setMusic(music: Music | null): void {
      current = music
      paint()
    },
  }
}
