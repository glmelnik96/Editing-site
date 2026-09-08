import { POLL_MS } from './assets'
import { escapeHtml } from './html'
import { cancelJob, listJobs, type JobListItem } from './project'

export const FLASH_MS = 2000

export type WorkJob = {
  id: string
  type: 'analyze' | 'proxy' | 'transcribe' | 'render' | 'convert'
  status: 'queued' | 'running' | 'done' | 'failed' | 'canceled'
  progress: number
  error: string | null
  label: string
  cancelable: boolean
  quality: 'draft' | 'final' | null
}

export type UploadWork = {
  id: string
  name: string
  done: number
  total: number
}

export type WorkFlash = {
  id: string
  kind: 'done' | 'canceled'
  until: number
  title: string
}

export type WorkState = {
  jobs: WorkJob[]
  uploads: UploadWork[]
  dismissed: string[]
  heldFailed: WorkJob[]
  flashes: WorkFlash[]
  uploadFails: { id: string; name: string; error: string }[]
}

export type WorkRow = {
  key: string
  title: string
  percent?: number
  cancelable: boolean
  closeable: boolean
  error?: string | null
  bar?: boolean
}

const VERB: Record<WorkJob['type'], string> = {
  analyze: 'Анализ',
  proxy: 'Прокси',
  transcribe: 'Расшифровка',
  render: 'Сборка',
  convert: 'Конвертер',
}

export function jobTitle(job: WorkJob): string {
  if (job.type === 'render') {
    const kind = job.quality === 'final' ? 'финала' : 'черновика'
    return `Сборка ${kind} «${job.label}»`
  }
  return `${VERB[job.type]} «${job.label}»`
}

function live(status: WorkJob['status']): boolean {
  return status === 'queued' || status === 'running'
}

export function foldIncoming(prev: WorkState, incoming: WorkJob[], now: number): WorkState {
  const flashes = prev.flashes.filter(flash => flash.until > now)
  const prevById = new Map(prev.jobs.map(job => [job.id, job]))
  const incomingIds = new Set(incoming.map(job => job.id))

  const addFlash = (id: string, kind: WorkFlash['kind'], title: string) => {
    if (flashes.some(flash => flash.id === id)) return
    flashes.push({ id, kind, until: now + FLASH_MS, title })
  }

  for (const job of incoming) {
    const old = prevById.get(job.id)
    if (job.status === 'done' || job.status === 'canceled') {
      if (!old || live(old.status)) {
        addFlash(job.id, job.status === 'canceled' ? 'canceled' : 'done', jobTitle(job))
      }
    }
  }
  for (const old of prev.jobs) {
    if (live(old.status) && !incomingIds.has(old.id)) {
      addFlash(old.id, 'done', jobTitle(old))
    }
  }

  const dismissed = new Set(prev.dismissed)
  const heldFailed: WorkJob[] = []
  const seen = new Set<string>()
  const hold = (job: WorkJob) => {
    if (job.status !== 'failed' || dismissed.has(job.id) || seen.has(job.id)) return
    seen.add(job.id)
    heldFailed.push(job)
  }
  for (const job of incoming) hold(job)
  for (const job of prev.heldFailed) hold(job)

  return {
    jobs: incoming,
    uploads: prev.uploads,
    dismissed: prev.dismissed,
    heldFailed,
    flashes,
    uploadFails: prev.uploadFails ?? [],
  }
}

function percentOf(done: number, total: number): number {
  if (total <= 0) return 0
  return Math.round((done / total) * 100)
}

export function workRows(state: WorkState, now: number): WorkRow[] {
  const rows: WorkRow[] = []
  const dismissed = new Set(state.dismissed)

  for (const upload of state.uploads) {
    rows.push({
      key: `up:${upload.id}`,
      title: `Загрузка «${upload.name}»`,
      percent: percentOf(upload.done, upload.total),
      cancelable: true,
      closeable: false,
      bar: true,
    })
  }

  for (const flash of state.flashes) {
    if (flash.until <= now) continue
    const word = flash.kind === 'canceled' ? 'отменено' : 'готово'
    rows.push({
      key: flash.id,
      title: `${flash.title} — ${word}`,
      cancelable: false,
      closeable: false,
    })
  }

  const flashing = new Set(state.flashes.filter(flash => flash.until > now).map(flash => flash.id))
  for (const job of state.jobs) {
    if (!live(job.status) || flashing.has(job.id)) continue
    rows.push({
      key: job.id,
      title: jobTitle(job),
      percent: percentOf(job.progress, 1),
      cancelable: job.cancelable,
      closeable: false,
      bar: true,
    })
  }

  const failed: WorkJob[] = []
  const seen = new Set<string>()
  for (const job of [...state.jobs, ...state.heldFailed]) {
    if (job.status !== 'failed' || dismissed.has(job.id) || seen.has(job.id) || flashing.has(job.id)) continue
    seen.add(job.id)
    failed.push(job)
  }
  for (const job of failed) {
    rows.push({
      key: job.id,
      title: jobTitle(job),
      cancelable: false,
      closeable: true,
      error: job.error,
    })
  }
  for (const fail of state.uploadFails ?? []) {
    const key = `up:${fail.id}`
    if (dismissed.has(key)) continue
    rows.push({
      key,
      title: `Загрузка «${fail.name}»`,
      cancelable: false,
      closeable: true,
      error: fail.error,
    })
  }

  return rows
}

export function emptyWork(): WorkState {
  return { jobs: [], uploads: [], dismissed: [], heldFailed: [], flashes: [], uploadFails: [] }
}

export function toWorkJob(job: JobListItem): WorkJob {
  return {
    id: job.id,
    type: job.type,
    status: job.status,
    progress: job.progress,
    error: job.error,
    label: job.label,
    cancelable: job.cancelable,
    quality: job.quality,
  }
}

export type TrackedUpload = {
  id: string
  signal: AbortSignal
  setProgress: (done: number, total: number) => void
  succeed: () => void
  fail: (message: string) => void
  abort: () => void
}

export type WorkControls = {
  trackUpload: (name: string) => TrackedUpload
  start: () => void
  stop: () => void
}

export function mountWork(el: HTMLElement): WorkControls {
  let state = emptyWork()
  let running = false
  let stale = false
  let seq = 0
  let pollTimer = 0
  let flashTimer = 0
  const rowErrors = new Map<string, string>()

  function armFlash(now: number): void {
    window.clearTimeout(flashTimer)
    const next = state.flashes.reduce((min, flash) => Math.min(min, flash.until), Infinity)
    if (next !== Infinity) {
      flashTimer = window.setTimeout(draw, Math.max(0, next - now + 10))
    }
  }

  function draw(): void {
    const now = Date.now()
    const rows = workRows(state, now)
    el.hidden = rows.length === 0 && !stale
    const staleNote = stale ? '<p class="meta">ход мог устареть</p>' : ''
    el.innerHTML =
      rows
        .map(row => {
          const err = row.error || rowErrors.get(row.key) || ''
          const pct = row.percent !== undefined ? `<span class="meta">${row.percent} %</span>` : ''
          const cancel = row.cancelable
            ? row.key.startsWith('up:')
              ? `<button type="button" class="btn btn-ghost" data-cancel-up="${escapeHtml(row.key.slice(3))}">Отменить</button>`
              : `<button type="button" class="btn btn-ghost" data-cancel-job="${escapeHtml(row.key)}">Отменить</button>`
            : ''
          const close = row.closeable
            ? `<button type="button" class="btn btn-ghost" data-dismiss="${escapeHtml(row.key)}">Закрыть</button>`
            : ''
          const bar = row.bar
            ? `<div class="progress"><i style="width:${row.percent ?? 0}%"></i></div>`
            : ''
          const error = err ? `<p class="meta error">${escapeHtml(err)}</p>` : ''
          return `<div class="work-row">
            <div class="row">
              <span>${escapeHtml(row.title)}</span>
              ${pct}
              ${cancel}${close}
            </div>
            ${bar}${error}
          </div>`
        })
        .join('') + staleNote

    el.querySelectorAll<HTMLButtonElement>('[data-cancel-job]').forEach(btn => {
      btn.addEventListener('click', () => void onCancelJob(btn.dataset.cancelJob ?? '', btn))
    })
    el.querySelectorAll<HTMLButtonElement>('[data-cancel-up]').forEach(btn => {
      btn.addEventListener('click', () => aborts.get(btn.dataset.cancelUp ?? '')?.())
    })
    el.querySelectorAll<HTMLButtonElement>('[data-dismiss]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.dismiss ?? ''
        state = { ...state, dismissed: [...state.dismissed, id] }
        rowErrors.delete(id)
        draw()
      })
    })
    armFlash(now)
  }

  const aborts = new Map<string, () => void>()

  async function onCancelJob(id: string, btn: HTMLButtonElement): Promise<void> {
    btn.disabled = true
    try {
      await cancelJob(id)
      rowErrors.delete(id)
    } catch (e) {
      rowErrors.set(id, e instanceof Error ? e.message : String(e))
    }
    draw()
  }

  async function poll(): Promise<void> {
    if (!running) return
    try {
      const { jobs } = await listJobs()
      if (!running) return
      stale = false
      state = foldIncoming(state, jobs.map(toWorkJob), Date.now())
    } catch {
      if (!running) return
      stale = true
    }
    draw()
  }

  function start(): void {
    if (running) return
    running = true
    void poll()
    pollTimer = window.setInterval(() => void poll(), POLL_MS)
  }

  function stop(): void {
    running = false
    window.clearInterval(pollTimer)
    window.clearTimeout(flashTimer)
    state = emptyWork()
    stale = false
    rowErrors.clear()
    aborts.clear()
    el.innerHTML = ''
    el.hidden = true
  }

  function trackUpload(name: string): TrackedUpload {
    const id = `u${++seq}`
    const ac = new AbortController()
    state = { ...state, uploads: [...state.uploads, { id, name, done: 0, total: 1 }] }
    const drop = () => {
      state = { ...state, uploads: state.uploads.filter(item => item.id !== id) }
      aborts.delete(id)
    }
    const abort = () => {
      ac.abort()
      drop()
      draw()
    }
    aborts.set(id, abort)
    draw()
    return {
      id,
      signal: ac.signal,
      setProgress(done, total) {
        state = {
          ...state,
          uploads: state.uploads.map(item => (item.id === id ? { ...item, done, total } : item)),
        }
        draw()
      },
      succeed() {
        drop()
        const now = Date.now()
        state = {
          ...state,
          flashes: [...state.flashes.filter(flash => flash.until > now), {
            id: `up:${id}`,
            kind: 'done',
            until: now + FLASH_MS,
            title: `Загрузка «${name}»`,
          }],
        }
        draw()
      },
      fail(message) {
        drop()
        state = { ...state, uploadFails: [...state.uploadFails, { id, name, error: message }] }
        draw()
      },
      abort,
    }
  }

  return { trackUpload, start, stop }
}
