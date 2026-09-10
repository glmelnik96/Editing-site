/**
 * Экран конвертера: один готовый файл, формат, список скачиваний этого человека.
 *
 * Загрузка остаётся в записях. Ход и отмена живут в списке под шапкой — как у панели сборки:
 * своя полоса с собственной кнопкой «Отменить» рядом с той же строкой в шапке показывала одно
 * задание дважды и спрашивала, какую из двух отмен нажимать.
 */
import { api, ApiError, isRetryable } from './api'
import {
  downloadFileName,
  fmtDuration,
  fmtSize,
  fmtWhen,
  listAssets,
  needsPolling,
  POLL_MS,
  withoutExt,
  type Asset,
} from './assets'
import { escapeHtml } from './html'
import { listJobs, loadJob, type JobListItem, type JobView } from './project'

const CONVERT_RUNNING = new Set(['queued', 'running'])
const CONVERT_JOB_TEXT: Record<string, string> = {
  queued: 'в очереди',
  running: 'конвертирую',
  done: 'готово',
  failed: 'не вышло',
  canceled: 'отменено',
}
const READY = new Set(['ready', 'proxy_ready'])
const CONVERTIBLE = new Set(['video', 'audio'])

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

/** Готовое видео или звук: субтитры в конвертер не кладём. */
export function convertibleAsset(asset: { kind: string; status: string }): boolean {
  return CONVERTIBLE.has(asset.kind) && READY.has(asset.status)
}

export function convertHint(): string {
  return 'извлечь звук займёт секунды; mp4 и webm — примерно как черновик сборки этой длительности'
}

export function convertJobText(status: string, progress: number): string {
  const pct = Math.round(Math.min(1, Math.max(0, progress)) * 100)
  if (status === 'running') return `конвертирую, ${pct} %`
  return CONVERT_JOB_TEXT[status] ?? status
}

/** Идущие convert-задания с GET /jobs: после перезагрузки экрана pending пуст. */
export function runningConvertsFromJobs(items: JobListItem[]): Array<{ assetId: string; job: JobView }> {
  return items
    .filter(job => job.type === 'convert' && CONVERT_RUNNING.has(job.status))
    .map(job => ({
      assetId: job.target_id,
      job: {
        id: job.id,
        type: job.type,
        status: job.status,
        progress: job.progress,
        error: job.error,
      },
    }))
}

/** Если идёт конвертация другого файла — показываем его, иначе оставляем выбор. */
export function pickConvertFile(selectedId: string, readyIds: string[], runningIds: string[]): string {
  const ready = new Set(readyIds)
  const live = runningIds.filter(id => ready.has(id))
  if (live.includes(selectedId)) return selectedId
  if (live[0]) return live[0]
  if (ready.has(selectedId)) return selectedId
  return readyIds[0] ?? ''
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
      <span>${escapeHtml(c.original_name)} · ${escapeHtml(c.format)} · ${fmtDuration(c.duration)} · ${fmtSize(c.size)} · до ${fmtWhen(c.expires_at)}</span>
      <span class="render-actions">
        <a href="${escapeHtml(c.download)}" download="${escapeHtml(downloadFileName(withoutExt(c.original_name), c.format))}">Скачать</a>
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
    return assets.filter(convertibleAsset)
  }

  function current(): Asset | undefined {
    return readyList().find(a => a.id === selectedId)
  }

  function jobHtml(assetId: string): string {
    const job = jobs.get(assetId)
    if (!job || job.status === 'done') return ''
    const where = CONVERT_RUNNING.has(job.status) ? ' — ход и отмена вверху' : ''
    return `<div class="stack" style="gap:4px">
      <span class="meta">${escapeHtml(convertJobText(job.status, job.progress))}${where}</span>
    </div>`
  }

  async function pollJobs(): Promise<void> {
    for (const [assetId, jobId] of [...pending]) {
      let job: JobView
      try {
        job = await loadJob(jobId)
      } catch (e) {
        showError(e)
        if (!isRetryable(e)) {
          // Заодно снимаем последнее известное состояние: без этого запись оставалась «в очереди»
          // навсегда, форматы и «Конвертировать» не разблокировались, а отмена била в пустоту.
          pending.delete(assetId)
          jobs.delete(assetId)
        }
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
    const runningIds = [...jobs.entries()]
      .filter(([, job]) => CONVERT_RUNNING.has(job.status))
      .map(([id]) => id)
    selectedId = pickConvertFile(
      selectedId,
      ready.map(a => a.id),
      runningIds,
    )
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

  function convertIsLive(): boolean {
    return [...pending.keys()].some(id => {
      const job = jobs.get(id)
      return Boolean(job && CONVERT_RUNNING.has(job.status))
    })
  }

  async function refresh(): Promise<void> {
    if (stopped) return
    try {
      const listed = await listAssets()
      if (stopped) return
      assets = listed.assets
      const { jobs: listedJobs } = await listJobs()
      if (stopped) return
      for (const row of runningConvertsFromJobs(listedJobs)) {
        pending.set(row.assetId, row.job.id)
        jobs.set(row.assetId, row.job)
      }
      await pollJobs()
      if (stopped) return
      const { conversions } = await listMyConversions()
      if (stopped) return
      draw(conversions)
      wire()
    } finally {
      window.clearTimeout(timer)
      // Первый заход упал (сервер перезапускался, сеть моргнула) — список записей пуст, живых
      // заданий нет, и по прежнему условию опрос больше не заводился: экран оставался пустым
      // навсегда. Поэтому пока ничего не показано, пробуем снова.
      const retry = !assets.length
      if (!stopped && (retry || needsPolling(assets) || convertIsLive())) {
        timer = window.setTimeout(() => void refresh().catch(showError), POLL_MS)
      }
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
