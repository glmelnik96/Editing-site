/**
 * Экран записей: загрузка файлов и список того, что уже лежит.
 *
 * Обработка идёт на сервере минутами, поэтому список сам перечитывается, пока хоть одна запись
 * не доехала до конечного состояния. Опрос прекращается вместе с экраном: иначе он продолжил бы
 * стучаться и рисовать в разобранную разметку.
 */
import { api, ApiError, isRetryable } from './api'
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
import { cancelJob, loadJob, type JobView } from './project'
import { type AssetData } from './strip'
import { uploadFile } from './upload'

const CONVERT_RUNNING = new Set(['queued', 'running'])
const CONVERT_JOB_TEXT: Record<string, string> = {
  queued: 'в очереди',
  running: 'конвертирую',
  done: 'готово',
  failed: 'не вышло',
  canceled: 'отменено',
}

export type ConversionCard = {
  id: string
  format: string
  size: number
  duration: number
  expires_at: string
  download: string
}

export type ConvertPanel = {
  assetId: string
  kind: string
  ready: boolean
  job: { status: string; progress: number } | null
  conversions: ConversionCard[]
}

export function convertFormatsFor(kind: string): string[] {
  return kind === 'audio' ? ['mp3', 'm4a', 'wav'] : ['mp3', 'm4a', 'wav', 'mp4']
}

export function convertHint(): string {
  return 'извлечь звук займёт секунды; mp4 — примерно как черновик сборки этой длительности'
}

export function convertJobText(status: string, progress: number): string {
  const pct = Math.round(Math.min(1, Math.max(0, progress)) * 100)
  if (status === 'running') return `конвертирую, ${pct} %`
  return CONVERT_JOB_TEXT[status] ?? status
}

/** Дата без секунд: у готового файла важен день, до которого он доживёт. */
function until(iso: string): string {
  return iso.replace('T', ' ').slice(0, 16)
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

export function listConversions(assetId: string): Promise<{ conversions: ConversionCard[] }> {
  return api<{ conversions: ConversionCard[] }>(
    `/api/v1/assets/${encodeURIComponent(assetId)}/conversions`,
  )
}

export function deleteConversion(id: string): Promise<void> {
  return api<void>(`/api/v1/conversions/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function convertPanelHtml(p: ConvertPanel): string {
  if (!p.ready) return ''
  const running = Boolean(p.job && CONVERT_RUNNING.has(p.job.status))
  const menu = convertFormatsFor(p.kind)
    .map(
      fmt =>
        `<button type="button" class="btn btn-ghost" data-convert="${fmt}" data-asset="${escapeHtml(p.assetId)}"${
          running ? ' disabled' : ''
        }>${fmt}</button>`,
    )
    .join('')
  let job = ''
  if (p.job && p.job.status !== 'done') {
    const pct = Math.round(Math.min(1, Math.max(0, p.job.progress)) * 100)
    const bar = running
      ? `<div class="progress"><i style="width:${pct}%"></i></div>
        <button type="button" class="btn btn-ghost" data-cancel-convert="${escapeHtml(p.assetId)}">Отменить</button>`
      : ''
    job = `<div class="stack" style="gap:4px">
      <span class="meta">${escapeHtml(convertJobText(p.job.status, p.job.progress))}</span>
      ${bar}
    </div>`
  }
  const list = p.conversions.length
    ? `<ul class="versions">${p.conversions
        .map(
          c => `<li>
      <span>${escapeHtml(c.format)} · ${fmtDuration(c.duration)} · ${fmtSize(c.size)} · до ${until(c.expires_at)}</span>
      <span class="render-actions">
        <a href="${escapeHtml(c.download)}" download>Скачать</a>
        <button type="button" data-drop-conversion="${escapeHtml(c.id)}">Удалить</button>
      </span></li>`,
        )
        .join('')}</ul>`
    : ''
  return `<div class="stack convert-box" style="gap:8px">
    <details>
      <summary class="btn btn-ghost">Скачать иначе</summary>
      <p class="meta" style="margin:8px 0">${escapeHtml(convertHint())}</p>
      <span class="row" style="margin:0">${menu}</span>
    </details>
    ${job}
    ${list}
  </div>`
}

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
  const pending = new Map<string, string>()
  const jobs = new Map<string, JobView>()
  const conversions = new Map<string, ConversionCard[]>()
  let timer: number | undefined
  let stopped = false

  const alive = () => !stopped
  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }
  const clearError = () => {
    errorBox.hidden = true
    errorBox.textContent = ''
  }

  function card(a: Asset): string {
    // Подпись рядом с пилюлей нужна, только если добавляет знание: «анализ» дважды подряд —
    // это шум, а «анализ, 40 %» и текст ошибки сказать стоит.
    const work = progressText(a.status, a.progress ?? null)
    const note = a.error ? a.error : work === statusText(a.status) ? '' : work
    const state = a.status === 'failed' ? ' pill-bad' : ''
    const ready = a.status === 'ready' || a.status === 'proxy_ready'
    const job = jobs.get(a.id) ?? null
    return `<article class="card stack asset-card" style="gap:12px">
      <div class="row" style="margin:0;align-items:center;gap:16px">
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
      </div>
      ${convertPanelHtml({
        assetId: a.id,
        kind: a.kind,
        ready,
        job,
        conversions: conversions.get(a.id) ?? [],
      })}
    </article>`
  }

  async function loadReadyConversions(assets: Asset[]): Promise<void> {
    const ready = assets.filter(a => a.status === 'ready' || a.status === 'proxy_ready')
    const rows = await Promise.all(
      ready.map(async a => {
        const { conversions: items } = await listConversions(a.id)
        return [a.id, items] as const
      }),
    )
    for (const [id, items] of rows) conversions.set(id, items)
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
      if (job.status === 'done') {
        const { conversions: items } = await listConversions(assetId)
        conversions.set(assetId, items)
      }
    }
  }

  async function refresh(): Promise<void> {
    if (stopped) return
    const { assets } = await listAssets()
    if (stopped) return
    await loadReadyConversions(assets)
    if (stopped) return
    await pollJobs()
    if (stopped) return
    list.innerHTML = assets.length
      ? assets.map(card).join('')
      : '<p class="lead" style="margin:0">Загрузите первую запись — дальше из неё соберётся ролик</p>'
    paintFrames(list, frames, alive)
    wire()
    window.clearTimeout(timer)
    if (!stopped && (needsPolling(assets) || pending.size > 0)) {
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
        pending.delete(b.dataset.dropAsset ?? '')
        onChanged?.()
        await refresh().catch(showError)
      }),
    )
    list.querySelectorAll<HTMLButtonElement>('button[data-convert]').forEach(b =>
      b.addEventListener('click', async () => {
        const assetId = b.dataset.asset ?? ''
        const format = b.dataset.convert ?? ''
        b.disabled = true
        try {
          const { job_id } = await startConvert(assetId, format)
          if (stopped) return
          clearError()
          pending.set(assetId, job_id)
          jobs.set(assetId, {
            id: job_id,
            type: 'convert',
            status: 'queued',
            progress: 0,
            error: null,
          })
          await refresh()
        } catch (e) {
          b.disabled = false
          showError(e)
        }
      }),
    )
    list.querySelectorAll<HTMLButtonElement>('button[data-cancel-convert]').forEach(b =>
      b.addEventListener('click', async () => {
        const assetId = b.dataset.cancelConvert ?? ''
        const jobId = pending.get(assetId)
        if (!jobId) return
        try {
          await cancelJob(jobId)
        } catch (e) {
          showError(e)
          return
        }
        await refresh().catch(showError)
      }),
    )
    list.querySelectorAll<HTMLButtonElement>('button[data-drop-conversion]').forEach(b =>
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
