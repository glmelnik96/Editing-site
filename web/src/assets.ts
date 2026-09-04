import { api, ApiError } from './api'
import { escapeHtml } from './html'
import { uploadFile } from './upload'

export type Asset = {
  id: string
  kind: string
  original_name: string
  size: number
  status: string
  duration: number | null
  error: string | null
  files: { proxy: string | null }
}

const STATUS: Record<string, string> = {
  uploaded: 'загружен, ждёт анализа',
  analyzing: 'анализ',
  ready: 'звук и полоска готовы, прокси в работе',
  proxy_ready: 'готов',
  failed: 'ошибка',
}
const FINAL = new Set(['ready', 'proxy_ready', 'failed'])
const POLL_MS = 3000

export function fmtSize(bytes: number): string {
  const units = ['Б', 'КБ', 'МБ', 'ГБ']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return i === 0 ? `${v} ${units[i]}` : `${v.toFixed(1)} ${units[i]}`
}

export function fmtDuration(sec: number | null): string {
  if (sec === null || !Number.isFinite(sec)) return '—'
  const s = Math.floor(sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  const mm = h ? String(m).padStart(2, '0') : String(m)
  return `${h ? h + ':' : ''}${mm}:${String(r).padStart(2, '0')}`
}

export function statusText(status: string): string {
  return STATUS[status] ?? status
}

export function needsPolling(assets: { status: string }[]): boolean {
  return assets.some(a => !FINAL.has(a.status))
}

function row(a: Asset): string {
  const cls = a.status === 'failed' ? ' class="status-failed"' : ''
  const err = a.error ? ` <span class="muted">${escapeHtml(a.error)}</span>` : ''
  return `<tr>
    <td>${escapeHtml(a.original_name)}</td><td>${escapeHtml(a.kind)}</td><td>${fmtSize(a.size)}</td>
    <td>${fmtDuration(a.duration)}</td><td${cls}>${escapeHtml(statusText(a.status))}${err}</td>
    <td><button data-delete="${escapeHtml(a.id)}" data-name="${escapeHtml(a.original_name)}">Удалить</button></td></tr>`
}

/**
 * Панель ассетов: загрузка файлов, список со статусами (опрос раз в 3 с, пока идёт обработка), удаление с подтверждением.
 * onChanged (если передан) вызывается после успешной загрузки и после успешного удаления — обновить квоту в шапке.
 */
export function mountAssets(el: HTMLElement, onChanged?: () => void): { refresh: () => Promise<void>; stop: () => void } {
  el.innerHTML = `
    <main class="card">
      <h2>Файлы</h2>
      <p class="muted">До 5 ГБ на файл. Загрузка продолжится с места разрыва, если выбрать тот же файл снова.</p>
      <input id="asset-files" type="file" multiple />
      <div id="asset-progress"></div>
      <table>
        <thead><tr><th>Имя</th><th>Вид</th><th>Размер</th><th>Длина</th><th>Статус</th><th></th></tr></thead>
        <tbody id="asset-rows"><tr><td colspan="6">Пока пусто</td></tr></tbody>
      </table>
      <pre id="assets-error" hidden></pre>
    </main>`
  const rows = el.querySelector('#asset-rows') as HTMLElement
  const progress = el.querySelector('#asset-progress') as HTMLElement
  const errorBox = el.querySelector('#assets-error') as HTMLPreElement
  let timer: number | undefined
  let stopped = false // панель заменена перерисовкой — старый опрос сервера дальше не идёт

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  const refresh = async () => {
    if (stopped) return
    const { assets } = await api<{ assets: Asset[] }>('/api/v1/assets')
    rows.innerHTML = assets.map(row).join('') || '<tr><td colspan="6">Пока пусто</td></tr>'
    rows.querySelectorAll<HTMLButtonElement>('button[data-delete]').forEach(b =>
      b.addEventListener('click', async () => {
        if (!window.confirm(`Удалить «${b.dataset.name}» без возможности восстановления?`)) return
        try {
          await api(`/api/v1/assets/${b.dataset.delete}`, { method: 'DELETE' })
        } catch (e) {
          showError(e)
          return
        }
        onChanged?.()
        try {
          await refresh()
        } catch (e) {
          showError(e)
        }
      }),
    )
    window.clearTimeout(timer)
    if (!stopped && needsPolling(assets)) timer = window.setTimeout(() => void refresh().catch(showError), POLL_MS)
  }

  /** Остановить опрос: вызывается перед перемонтированием панели при перерисовке настроек. */
  const stop = () => {
    stopped = true
    window.clearTimeout(timer)
  }

  const input = el.querySelector('#asset-files') as HTMLInputElement
  input.addEventListener('change', async () => {
    const files = Array.from(input.files ?? [])
    input.value = ''
    for (const file of files) {
      const line = document.createElement('div')
      line.innerHTML = `<span>${escapeHtml(file.name)}</span><div class="progress"><i style="width:0%"></i></div>`
      progress.appendChild(line)
      const bar = line.querySelector('i') as HTMLElement
      try {
        await uploadFile(file, { onProgress: (d, t) => (bar.style.width = `${Math.round((d / t) * 100)}%`) })
      } catch (e) {
        line.querySelector('span')!.textContent = `${file.name}: не загружен`
        showError(e)
        continue
      }
      line.remove()
      onChanged?.()
      try {
        await refresh()
      } catch (e) {
        showError(e)
      }
    }
  })

  void refresh().catch(showError)
  return { refresh, stop }
}
