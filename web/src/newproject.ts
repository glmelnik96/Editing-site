/**
 * Экран нового проекта: выбрать запись и войти в монтаж.
 *
 * Проект создаётся сразу с одним клипом на всю запись — человек попадает в редактор к готовой
 * шкале, а не к пустоте, которую надо ещё чем-то наполнить.
 */
import { ApiError } from './api'
import { fmtDuration, frameHtml, isReady, listAssets, paintFrames, takePick, type Asset } from './assets'
import { escapeHtml } from './html'
import { createProject, saveRequest } from './project'
import { type AssetData } from './strip'

export function mountNewProject(el: HTMLElement) {
  el.innerHTML = `
    <div class="screen stack">
      <h1 class="display-l" style="margin:0">Новый проект</h1>
      <input id="np-name" class="field" maxlength="200" placeholder="Название (можно не заполнять)" />
      <div id="np-body" class="stack"></div>
      <pre id="np-error" hidden></pre>
    </div>`

  const nameField = el.querySelector('#np-name') as HTMLInputElement
  const body = el.querySelector('#np-body') as HTMLElement
  const errorBox = el.querySelector('#np-error') as HTMLPreElement
  const frames = new Map<string, Promise<AssetData>>()
  let stopped = false
  let assets: Asset[] = []
  let picked: string | null = null

  const alive = () => !stopped
  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  function tile(a: Asset): string {
    const on = a.id === picked ? ' picked' : ''
    return `<button type="button" class="card tile${on}" data-tile="${escapeHtml(a.id)}">
      ${frameHtml(a)}
      <span class="stack tile-name">
        <span>${escapeHtml(a.original_name)}</span>
        <span class="meta">${fmtDuration(a.duration)}</span>
      </span>
    </button>`
  }

  function draw(): void {
    if (!assets.length) {
      body.innerHTML = `
        <p class="lead" style="margin:0">Сначала загрузите запись — из неё и соберётся ролик</p>
        <a class="btn btn-key" href="#/files">К записям</a>`
      return
    }
    body.innerHTML = `
      <p class="lead" style="margin:0">Выберите запись</p>
      <div class="tiles">${assets.map(tile).join('')}</div>
      <div class="row">
        <button id="np-go" class="btn btn-key"${picked ? '' : ' disabled'}>Начать монтаж</button>
        <a class="btn btn-ghost" href="#/files">Загрузить ещё</a>
      </div>`
    paintFrames(body, frames, alive)
    body.querySelectorAll<HTMLButtonElement>('button[data-tile]').forEach(b =>
      b.addEventListener('click', () => {
        picked = b.dataset.tile ?? null
        draw()
      }),
    )
    body.querySelector('#np-go')?.addEventListener('click', () => void start())
  }

  async function start(): Promise<void> {
    const asset = assets.find(a => a.id === picked)
    if (!asset) return
    const go = body.querySelector<HTMLButtonElement>('#np-go')
    if (go) go.disabled = true
    const name = nameField.value.trim() || asset.original_name
    try {
      const project = await createProject(name)
      // Сразу кладём запись на шкалу: пустой проект заставил бы человека повторить тот же выбор
      // ещё раз, уже внутри редактора.
      const doc = {
        ...project.doc,
        clips: [
          {
            id: 'c1',
            asset_id: asset.id,
            in: 0,
            out: asset.duration ?? 0,
            snap_to_pauses: false,
            in_verified: false,
            out_verified: false,
          },
        ],
      }
      const saved = await saveRequest({ ...project, doc })
      location.hash = `#/p/${saved.id}`
    } catch (e) {
      if (go) go.disabled = false
      showError(e)
    }
  }

  void listAssets()
    .then(({ assets: all }) => {
      if (stopped) return
      assets = all.filter(isReady)
      const remembered = takePick()
      picked = remembered && assets.some(a => a.id === remembered) ? remembered : null
      draw()
    })
    .catch(showError)

  return {
    stop(): void {
      stopped = true
    },
  }
}
