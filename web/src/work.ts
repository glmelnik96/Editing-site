export const FLASH_MS = 2000

export type WorkJob = {
  id: string
  type: 'analyze' | 'proxy' | 'transcribe' | 'render'
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

  return rows
}
