/**
 * Экран записей: загрузка файлов и список того, что уже лежит.
 *
 * Обработка идёт на сервере минутами, поэтому список сам перечитывается, пока хоть одна запись
 * не доехала до конечного состояния. Опрос прекращается вместе с экраном: иначе он продолжил бы
 * стучаться и рисовать в разобранную разметку.
 */
import { ApiError } from './api'
import {
  deleteAsset,
  fmtDuration,
  fmtSize,
  frameHtml,
  listAssets,
  needsPolling,
  paintFrames,
  POLL_MS,
  rememberPick,
  statusText,
  type Asset,
} from './assets'
import { escapeHtml } from './html'
import { progressText } from './player'
import { type AssetData } from './strip'
import { uploadFile } from './upload'

/** Экран записей. `onChanged` зовётся после загрузки и удаления — обновить место в шапке. */
export function mountFiles(el: HTMLElement, onChanged?: () => void) {
  el.innerHTML = `
    <div class="screen stack">
      <h1 class="display-l" style="margin:0">Записи</h1>
      <label class="card dropzone" id="f-drop">
        <input id="f-input" type="file" multiple hidden />
        <span class="display-m">Перетащите запись сюда</span>
        <span class="lead">или нажмите, чтобы выбрать. До 5 ГБ на файл; прерванная загрузка
          продолжится с места разрыва, если выбрать тот же файл снова</span>
      </label>
      <div id="f-progress" class="stack"></div>
      <div id="f-list" class="stack"></div>
      <pre id="f-error" hidden></pre>
    </div>`

  const drop = el.querySelector('#f-drop') as HTMLElement
  const input = el.querySelector('#f-input') as HTMLInputElement
  const progress = el.querySelector('#f-progress') as HTMLElement
  const list = el.querySelector('#f-list') as HTMLElement
  const errorBox = el.querySelector('#f-error') as HTMLPreElement
  const frames = new Map<string, Promise<AssetData>>()
  let timer: number | undefined
  let stopped = false

  const alive = () => !stopped
  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  function card(a: Asset): string {
    // Подпись рядом с пилюлей нужна, только если добавляет знание: «анализ» дважды подряд —
    // это шум, а «анализ, 40 %» и текст ошибки сказать стоит.
    const work = progressText(a.status, a.progress ?? null)
    const note = a.error ? a.error : work === statusText(a.status) ? '' : work
    const state = a.status === 'failed' ? ' pill-bad' : ''
    const ready = a.status === 'ready' || a.status === 'proxy_ready'
    return `<article class="card row asset-card">
      ${frameHtml(a)}
      <div class="stack asset-name">
        <span>${escapeHtml(a.original_name)}</span>
        <span class="meta">${fmtDuration(a.duration)} · ${fmtSize(a.size)}</span>
      </div>
      <span class="pill${state}">${escapeHtml(statusText(a.status))}</span>
      ${note ? `<span class="meta">${escapeHtml(note)}</span>` : ''}
      <span class="row asset-actions">
        ${ready ? `<button class="btn btn-key" data-pick="${escapeHtml(a.id)}">В проект</button>` : ''}
        <button class="btn btn-ghost" data-drop-asset="${escapeHtml(a.id)}"
          data-name="${escapeHtml(a.original_name)}">Удалить</button>
      </span>
    </article>`
  }

  async function refresh(): Promise<void> {
    if (stopped) return
    const { assets } = await listAssets()
    if (stopped) return
    list.innerHTML = assets.length
      ? assets.map(card).join('')
      : '<p class="lead" style="margin:0">Загрузите первую запись — дальше из неё соберётся ролик</p>'
    paintFrames(list, frames, alive)
    wire()
    window.clearTimeout(timer)
    if (!stopped && needsPolling(assets)) {
      timer = window.setTimeout(() => void refresh().catch(showError), POLL_MS)
    }
  }

  function wire(): void {
    list.querySelectorAll<HTMLButtonElement>('button[data-pick]').forEach(b =>
      b.addEventListener('click', () => {
        rememberPick(b.dataset.pick ?? '')
        location.hash = '#/new'
      }),
    )
    list.querySelectorAll<HTMLButtonElement>('button[data-drop-asset]').forEach(b =>
      b.addEventListener('click', async () => {
        if (!window.confirm(`Удалить «${b.dataset.name}» без возможности восстановления?`)) return
        b.disabled = true
        try {
          await deleteAsset(b.dataset.dropAsset ?? '')
        } catch (e) {
          b.disabled = false
          showError(e)
          return
        }
        onChanged?.()
        await refresh().catch(showError)
      }),
    )
  }

  async function take(files: File[]): Promise<void> {
    for (const file of files) {
      const line = document.createElement('div')
      line.className = 'stack upload-line'
      line.innerHTML = `<span class="small">${escapeHtml(file.name)}</span>
        <div class="progress"><i style="width:0%"></i></div>`
      progress.appendChild(line)
      const bar = line.querySelector('i') as HTMLElement
      try {
        await uploadFile(file, { onProgress: (d, t) => (bar.style.width = `${Math.round((d / t) * 100)}%`) })
      } catch (e) {
        line.querySelector('span')!.textContent = `${file.name}: не загрузился`
        showError(e)
        continue
      }
      line.remove()
      onChanged?.()
      await refresh().catch(showError)
    }
  }

  input.addEventListener('change', () => {
    const files = Array.from(input.files ?? [])
    input.value = ''
    void take(files)
  })

  // Перетаскивание: браузер по умолчанию открывает брошенный файл вместо страницы.
  drop.addEventListener('dragover', event => {
    event.preventDefault()
    drop.classList.add('over')
  })
  drop.addEventListener('dragleave', () => drop.classList.remove('over'))
  drop.addEventListener('drop', event => {
    event.preventDefault()
    drop.classList.remove('over')
    void take(Array.from(event.dataTransfer?.files ?? []))
  })

  void refresh().catch(showError)

  return {
    stop(): void {
      stopped = true
      window.clearTimeout(timer)
    },
  }
}
