import { api, ApiError } from './api'
import type { Clip } from './timeline/model'

export type Output = { aspect: '16:9' | '9:16' | '1:1'; fit: 'pad' | 'crop'; fps: number }
export type Music = { asset_id: string; volume: number; fade_in: number; fade_out: number; loop: boolean }
export type Cue = { start: number; end: number; text: string }
export type Subtitles = {
  source: 'file' | 'transcript' | 'cues'
  asset_id: string | null
  mode: 'burn' | 'soft'
  style: string
  cues?: Cue[]
}
export type ProjectDoc = { output: Output; clips: Clip[]; music: Music | null; subtitles: Subtitles | null }

export type Project = {
  id: string
  name: string
  version: number
  status: 'draft' | 'finished'
  created_at: string
  updated_at: string
  finished_at: string | null
  doc: ProjectDoc
}

export type ProjectCard = Omit<Project, 'doc'> & { clips_count: number; duration: number }
export type FieldError = { field: string; message: string }

export const SAVE_DELAY_MS = 500

export function loadProject(id: string): Promise<Project> {
  return api<Project>(`/api/v1/projects/${encodeURIComponent(id)}`)
}

export function listProjects(): Promise<{ projects: ProjectCard[] }> {
  return api<{ projects: ProjectCard[] }>('/api/v1/projects')
}

export function createProject(name: string): Promise<Project> {
  return api<Project>('/api/v1/projects', { method: 'POST', body: JSON.stringify({ name }) })
}

export function saveRequest(project: Project): Promise<Project> {
  return api<Project>(`/api/v1/projects/${encodeURIComponent(project.id)}`, {
    method: 'PUT',
    body: JSON.stringify({ name: project.name, version: project.version, doc: project.doc }),
  })
}

type SaverOptions = {
  request?: (project: Project) => Promise<Project>
  delay?: number
  onSaved?: (project: Project) => void
  onConflict?: (fresh: Project) => void
  onInvalid?: (errors: FieldError[]) => void
  onError?: (error: unknown) => void
  onStateChange?: (state: 'idle' | 'pending' | 'saving' | 'failed') => void
}

function conflictProject(error: ApiError): Project | null {
  const details = error.details as { project?: Project } | null
  return details?.project ?? null
}

function invalidErrors(error: ApiError): FieldError[] {
  const details = error.details as { errors?: FieldError[] } | null
  return details?.errors ?? []
}

/**
 * Автосохранение: правка ждёт тишины, запрос идёт один за раз.
 *
 * Пока запрос в полёте, новая правка не отправляется — она копится в очереди (последняя
 * побеждает) и уходит сразу после ответа. Перед повторной отправкой её версию подтягиваем к
 * версии из ответа сервера: иначе она уйдёт со старой версией и получит конфликт на пустом месте,
 * хотя на самом деле никто с чужой правкой не сталкивался.
 */
export function createSaver(options: SaverOptions = {}) {
  const request = options.request ?? saveRequest
  const delay = options.delay ?? SAVE_DELAY_MS
  let timer: number | undefined
  let queued: Project | null = null
  let saving = false
  let failed = false // последняя попытка сохранения провалилась не по вине документа
  // Промис текущей цепочки сохранений и итог её последнего звена: на них смотрит flush, которому
  // нужно не «отправлено», а «записано» — иначе следом за ним пойдёт работа со старым документом.
  let chain: Promise<void> = Promise.resolve()
  let lastError: unknown = null

  const notify = (state: 'idle' | 'pending' | 'saving' | 'failed') => options.onStateChange?.(state)

  async function run(project: Project): Promise<void> {
    saving = true
    failed = false
    lastError = null
    notify('saving')
    let saved: Project | null = null
    try {
      saved = await request(project)
      options.onSaved?.(saved)
    } catch (error) {
      lastError = error
      if (error instanceof ApiError && error.status === 409) {
        // Правку поверх чужой версии не досылаем: копить состояние после конфликта нельзя.
        queued = null
        const fresh = conflictProject(error)
        if (fresh) options.onConflict?.(fresh)
        else options.onError?.(error)
      } else if (error instanceof ApiError && error.status === 422) {
        queued = null
        options.onInvalid?.(invalidErrors(error))
      } else {
        // Сеть, 401, 500: правку не выбрасываем, но и повторять сами не будем — следующая
        // правка отправит её вместе с собой, а пока честно показываем, что не сохранено.
        failed = true
        options.onError?.(error)
      }
    } finally {
      saving = false
      const next = queued
      queued = null
      if (next) await run(saved ? { ...next, version: saved.version } : next)
      else notify(failed ? 'failed' : 'idle')
    }
  }

  return {
    /** Отложить сохранение: последняя правка выигрывает. */
    schedule(project: Project): void {
      queued = project
      notify('pending')
      clearTimeout(timer)
      timer = setTimeout(() => {
        if (saving) return // запрос уже летит — очередь подхватит его собственный finally
        const next = queued
        queued = null
        if (next) chain = run(next)
      }, delay)
    },
    /**
     * Сохранить немедленно и дождаться, пока очередь опустеет (уход со страницы, снимок, сборка).
     *
     * Ждём не свой запрос, а всю цепочку: при летящем сохранении правка встаёт в очередь, и её
     * отправит finally текущего run. Если последнее звено цепочки не записалось — сеть, 401,
     * конфликт версий, отказ проверки — молча резолвиться нельзя: вызывающий поверит, что на
     * сервере лежит показанное на экране, и будет работать со старым документом.
     */
    async flush(project: Project): Promise<void> {
      clearTimeout(timer)
      if (saving) queued = project
      else {
        queued = null
        chain = run(project)
      }
      await chain
      if (lastError) throw lastError
    },
    /** Есть ли несохранённые правки (в очереди или уже отправляются). */
    pending(): boolean {
      return queued !== null || saving
    },
    cancel(): void {
      clearTimeout(timer)
      queued = null
    },
  }
}

export type VersionCard = {
  id: string
  version: number
  label: string
  name: string
  created_at: string
  clips_count: number
  duration: number
}

export function listVersions(id: string): Promise<{ versions: VersionCard[] }> {
  return api<{ versions: VersionCard[] }>(`/api/v1/projects/${encodeURIComponent(id)}/versions`)
}

export function createCheckpoint(id: string, label: string): Promise<VersionCard> {
  return api<VersionCard>(`/api/v1/projects/${encodeURIComponent(id)}/checkpoint`, {
    method: 'POST',
    body: JSON.stringify({ label }),
  })
}

export function restoreVersion(id: string, versionId: string): Promise<Project> {
  return api<Project>(`/api/v1/projects/${encodeURIComponent(id)}/restore`, {
    method: 'POST',
    body: JSON.stringify({ version_id: versionId }),
  })
}

export type RenderCard = {
  id: string
  project_id: string
  quality: 'draft' | 'final'
  size: number
  duration: number
  created_at: string
  expires_at: string
  download: string
}

export type JobView = {
  id: string
  type: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'canceled'
  progress: number
  error: string | null
}

export function startRender(id: string, quality: 'draft' | 'final'): Promise<{ job_id: string; quality: string }> {
  return api<{ job_id: string; quality: string }>(`/api/v1/projects/${encodeURIComponent(id)}/render`, {
    method: 'POST',
    body: JSON.stringify({ quality }),
  })
}

export function listRenders(id: string): Promise<{ renders: RenderCard[] }> {
  return api<{ renders: RenderCard[] }>(`/api/v1/projects/${encodeURIComponent(id)}/renders`)
}

export function loadJob(jobId: string): Promise<JobView> {
  return api<JobView>(`/api/v1/jobs/${encodeURIComponent(jobId)}`)
}

export function cancelJob(jobId: string): Promise<void> {
  return api<void>(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })
}

export function deleteRender(renderId: string): Promise<void> {
  return api<void>(`/api/v1/renders/${encodeURIComponent(renderId)}`, { method: 'DELETE' })
}

/**
 * Слово транскрипта во времени исходника.
 *
 * interpolated — времена разложены по слогам, а не измерены: точность около ±0.3 с. Резать по ним
 * напрямую нельзя, поэтому кусок из выделения кладётся клипом со snap_to_pauses.
 */
export type TranscriptWord = { w: string; s: number; e: number; interpolated?: boolean }

export type TranscriptSegment = {
  id: number
  start: number
  end: number
  text: string
  start_verified: boolean
  end_verified: boolean
  suspect: boolean
  words?: TranscriptWord[]
}

/** Карты пауз (silences, silences_dense) и stats тоже приходят, но панели они не нужны. */
export type Transcript = {
  asset_id: string
  provider: string
  model: string
  language: string
  duration: number
  segments: TranscriptSegment[]
}

export function loadTranscript(assetId: string): Promise<Transcript> {
  return api<Transcript>(`/api/v1/assets/${encodeURIComponent(assetId)}/transcript?format=json`)
}

/** Язык не передаём: сервер возьмёт свой по умолчанию, а угадывать за человека нам нечем. */
export function startTranscribe(assetId: string): Promise<{ job_id: string; language: string }> {
  return api<{ job_id: string; language: string }>(
    `/api/v1/assets/${encodeURIComponent(assetId)}/transcribe`,
    { method: 'POST', body: JSON.stringify({}) },
  )
}

/** Собрать реплики из расшифровки в документ проекта: правка как правка, версия растёт. */
export function generateSubtitles(id: string, assetId: string, mode: 'burn' | 'soft'): Promise<Project> {
  return api<Project>(`/api/v1/projects/${encodeURIComponent(id)}/subtitles/generate`, {
    method: 'POST',
    body: JSON.stringify({ asset_id: assetId, mode }),
  })
}
