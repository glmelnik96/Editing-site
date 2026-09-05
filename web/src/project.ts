import { api, ApiError } from './api'
import type { Clip } from './timeline/model'

export type Output = { aspect: '16:9' | '9:16' | '1:1'; fit: 'pad' | 'crop'; fps: number }
export type Music = { asset_id: string; volume: number; fade_in: number; fade_out: number; loop: boolean }
export type Subtitles = { source: 'file' | 'transcript'; asset_id: string; mode: 'burn' | 'soft'; style: string }
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

  const notify = (state: 'idle' | 'pending' | 'saving' | 'failed') => options.onStateChange?.(state)

  async function run(project: Project): Promise<void> {
    saving = true
    failed = false
    notify('saving')
    let saved: Project | null = null
    try {
      saved = await request(project)
      options.onSaved?.(saved)
    } catch (error) {
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
        if (next) void run(next)
      }, delay)
    },
    /** Сохранить немедленно (уход со страницы, кнопка «сохранить»). */
    async flush(project: Project): Promise<void> {
      clearTimeout(timer)
      if (saving) {
        queued = project
        return
      }
      queued = null
      await run(project)
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
