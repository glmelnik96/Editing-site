/**
 * Экран конвертера: один готовый файл, формат, список скачиваний этого человека.
 *
 * Загрузка остаётся в записях. Ход задания дублирует полосу шапки, чтобы не искать её глазами.
 */
import { api, ApiError, isRetryable } from './api'
import { fmtDuration, fmtSize, listAssets, needsPolling, POLL_MS, type Asset } from './assets'
import { escapeHtml } from './html'
import { cancelJob, loadJob, type JobView } from './project'

const CONVERT_RUNNING = new Set(['queued', 'running'])
const CONVERT_JOB_TEXT: Record<string, string> = {
  queued: 'в очереди',
  running: 'конвертирую',
  done: 'готово',
  failed: 'не вышло',
  canceled: 'отменено',
}
const READY = new Set(['ready', 'proxy_ready'])

export type ConversionCard = {
  id: string
  original_name: string
  format: string
  size: number
  duration: number
  expires_at: string
  download: string
}

export function convertFormatsFor(kind: string): string[] {
  const audio = ['mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg']
  return kind === 'audio' ? audio : [...audio, 'mp4', 'webm']
}

export function convertHint(): string {
  return 'извлечь звук займёт секунды; mp4 и webm — примерно как черновик сборки этой длительности'
}

export function convertJobText(status: string, progress: number): string {
  const pct = Math.round(Math.min(1, Math.max(0, progress)) * 100)
  if (status === 'running') return `конвертирую, ${pct} %`
  return CONVERT_JOB_TEXT[status] ?? status
}

function until(iso: string): string {
  return iso.replace('T', ' ').slice(0, 16)
}

export function emptyConvertHtml(): string {
  return `<p class="lead" style="margin:0">Сначала загрузите запись</p>
    <a class="btn btn-key" href="#/files">К записям</a>`
}

export function formatChipsHtml(kind: string, selected: string, locked: boolean): string {
  return convertFormatsFor(kind)
    .map(
      fmt =>
        `<button type="button" class="btn ${fmt === selected ? 'btn-key' : 'btn-ghost'}" data-format="${fmt}"${
          locked ? ' disabled' : ''
        }>${fmt}</button>`,
    )
    .join('')
}

export function conversionsListHtml(items: ConversionCard[]): string {
  if (!items.length) return ''
  return `<h2 class="display-m" style="margin:0">Готовые файлы</h2>
    <ul class="versions">${items
      .map(
        c => `<li>
      <span>${escapeHtml(c.original_name)} · ${escapeHtml(c.format)} · ${fmtDuration(c.duration)} · ${fmtSize(c.size)} · до ${until(c.expires_at)}</span>
      <span class="render-actions">
        <a href="${escapeHtml(c.download)}" download>Скачать</a>
        <button type="button" data-drop-conversion="${escapeHtml(c.id)}">Удалить</button>
      </span></li>`,
      )
      .join('')}</ul>`
}

export function startConvert(
  assetId: string,
  format: string,
): Promise<{ job_id: string; conversion_id: string }> {
  return api<{ job_id: string; conversion_id: string }>(
    `/api/v1/assets/${encodeURIComponent(assetId)}/convert`,
    { method: 'POST', body: JSON.stringify({ format }) },
  )
}

export function listMyConversions(): Promise<{ conversions: ConversionCard[] }> {
  return api<{ conversions: ConversionCard[] }>('/api/v1/conversions')
}

export function deleteConversion(id: string): Promise<void> {
  return api<void>(`/api/v1/conversions/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function mountConvert(el: HTMLElement) {
  el.innerHTML = `
    <div class="screen stack">
      <h1 class="display-l" style="margin:0">Конвертер</h1>
      <div id="cv-body" class="stack"></div>
      <pre id="cv-error" hidden></pre>
    </div>`

  const body = el.querySelector('#cv-body') as HTMLElement
  const errorBox = el.querySelector('#cv-error') as HTMLPreElement
  const pending = new Map<string, string>()
  const jobs = new Map<string, JobView>()
  let assets: Asset[] = []
  let selectedId = ''
  let selectedFormat = 'mp3'
  let timer: number | undefined
  let stopped = false

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }
  const clearError = () => {
    errorBox.hidden = true
    errorBox.textContent = ''
  }

  function readyList(): Asset[] {
    return assets.filter(a => READY.has(a.status))
  }

  function current(): Asset | undefined {
    return readyList().find(a => a.id === selectedId)
  }

  function jobHtml(assetId: string): string {
    const job = jobs.get(assetId)
    if (!job || job.status === 'done') return ''
    const running = CONVERT_RUNNING.has(job.status)
    const pct = Math.round(Math.min(1, Math.max(0, job.progress)) * 100)
    const bar = running
      ? `<div class="progress"><i style="width:${pct}%"></i></div>
        <button type="button" class="btn btn-ghost" data-cancel-convert>Отменить</button>`
      : ''
    return `<div class="stack" style="gap:4px">
      <span class="meta">${escapeHtml(convertJobText(job.status, job.progress))}</span>
      ${bar}
    </div>`
  }

  async function pollJobs(): Promise<void> {
    for (const [assetId, jobId] of [...pending]) {
      let job: JobView
      try {
        job = await loadJob(jobId)
      } catch (e) {
        showError(e)
        if (!isRetryable(e)) pending.delete(assetId)
        continue
      }
      jobs.set(assetId, job)
      if (CONVERT_RUNNING.has(job.status)) continue
      pending.delete(assetId)
      if (job.status === 'failed') showError(job.error || 'Конвертация не удалась')
    }
  }

  function draw(conversions: ConversionCard[]): void {
    const ready = readyList()
    if (!ready.length) {
      body.innerHTML = `${emptyConvertHtml()}${conversionsListHtml(conversions)}`
      return
    }
    if (!ready.some(a => a.id === selectedId)) selectedId = ready[0].id
    const asset = current()
    if (!asset) return
    const formats = convertFormatsFor(asset.kind)
    if (!formats.includes(selectedFormat)) selectedFormat = formats[0]
    const locked = Boolean(jobs.get(asset.id) && CONVERT_RUNNING.has(jobs.get(asset.id)!.status))
    const options = ready
      .map(
        a =>
          `<option value="${escapeHtml(a.id)}"${a.id === selectedId ? ' selected' : ''}>${escapeHtml(a.original_name)} · ${fmtDuration(a.duration)}</option>`,
      )
      .join('')
    body.innerHTML = `
      <label class="stack" style="gap:6px">
        <span class="meta">Файл</span>
        <select id="cv-file" class="field">${options}</select>
      </label>
      <p class="meta" style="margin:0"><a href="#/files">Нет файла? Загрузить в записях</a></p>
      <div class="stack" style="gap:8px">
        <span class="meta">Формат</span>
        <span class="row" style="margin:0">${formatChipsHtml(asset.kind, selectedFormat, locked)}</span>
        <p class="meta" style="margin:0">${escapeHtml(convertHint())}</p>
        <button type="button" class="btn btn-key" id="cv-go"${locked ? ' disabled' : ''}>Конвертировать</button>
      </div>
      ${jobHtml(asset.id)}
      ${conversionsListHtml(conversions)}`
  }

  function wire(): void {
    body.querySelector('#cv-file')?.addEventListener('change', event => {
      selectedId = (event.target as HTMLSelectElement).value
      void refresh().catch(showError)
    })
    body.querySelectorAll<HTMLButtonElement>('button[data-format]').forEach(b =>
      b.addEventListener('click', () => {
        selectedFormat = b.dataset.format ?? selectedFormat
        void refresh().catch(showError)
      }),
    )
    body.querySelector('#cv-go')?.addEventListener('click', async () => {
      const asset = current()
      if (!asset) return
      const go = body.querySelector('#cv-go') as HTMLButtonElement
      go.disabled = true
      try {
        const { job_id } = await startConvert(asset.id, selectedFormat)
        if (stopped) return
        clearError()
        pending.set(asset.id, job_id)
        jobs.set(asset.id, {
          id: job_id,
          type: 'convert',
          status: 'queued',
          progress: 0,
          error: null,
        })
        await refresh()
      } catch (e) {
        go.disabled = false
        showError(e)
      }
    })
    body.querySelector('[data-cancel-convert]')?.addEventListener('click', async () => {
      const jobId = pending.get(selectedId)
      if (!jobId) return
      try {
        await cancelJob(jobId)
      } catch (e) {
        showError(e)
        return
      }
      await refresh().catch(showError)
    })
    body.querySelectorAll<HTMLButtonElement>('button[data-drop-conversion]').forEach(b =>
      b.addEventListener('click', async () => {
        if (!window.confirm('Удалить готовый файл? Он пропадёт без возможности восстановления.')) return
        try {
          await deleteConversion(b.dataset.dropConversion ?? '')
          await refresh()
        } catch (e) {
          showError(e)
        }
      }),
    )
  }

  async function refresh(): Promise<void> {
    if (stopped) return
    const listed = await listAssets()
    if (stopped) return
    assets = listed.assets
    await pollJobs()
    if (stopped) return
    const { conversions } = await listMyConversions()
    if (stopped) return
    draw(conversions)
    wire()
    window.clearTimeout(timer)
    const live = [...pending.keys()].some(id => {
      const job = jobs.get(id)
      return Boolean(job && CONVERT_RUNNING.has(job.status))
    })
    if (!stopped && (needsPolling(assets) || live)) {
      timer = window.setTimeout(() => void refresh().catch(showError), POLL_MS)
    }
  }

  void refresh().catch(showError)

  return {
    stop(): void {
      stopped = true
      window.clearTimeout(timer)
    },
  }
}
