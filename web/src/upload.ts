import { api, ApiError } from './api'

export type UploadCreated = { upload_id: string; chunk_size: number; total_chunks: number; expires_at: string }
export type UploadStatus = { upload_id: string; received: number[]; total: number; size: number; chunk_size: number }
export type UploadResult = { asset_id: string; status: string }
export type FileLike = { name: string; size: number; lastModified: number; slice(start: number, end: number): Blob }
type Storage = { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void }
export type UploadOptions = {
  onProgress?: (doneBytes: number, totalBytes: number) => void
  parallel?: number
  retries?: number
  storage?: Storage
  request?: typeof api
  sleep?: (ms: number) => Promise<void>
}

export const PARALLEL = 3 // спека, раздел 6.1
export const RETRIES = 3

export function chunkCount(size: number, chunkSize: number): number {
  return Math.max(1, Math.ceil(size / chunkSize))
}

export function missingChunks(total: number, received: number[]): number[] {
  const got = new Set(received)
  const out: number[] = []
  for (let i = 0; i < total; i++) if (!got.has(i)) out.push(i)
  return out
}

/** Ключ докачки в localStorage: имя, размер и дата изменения файла. */
export function fingerprint(f: { name: string; size: number; lastModified: number }): string {
  return `upload:${f.name}:${f.size}:${f.lastModified}`
}

export function backoffMs(attempt: number): number {
  return 1000 * 2 ** attempt
}

/** Повторяем только сбои сети, 5xx и 429: ошибка 4xx не исправится сама. */
export function isRetryable(e: unknown): boolean {
  if (e instanceof ApiError) return e.status >= 500 || e.status === 429
  return true
}

function defaultStorage(): Storage | undefined {
  try {
    return localStorage
  } catch {
    return undefined
  }
}

type Session = { id: string; chunkSize: number; total: number; received: number[] }

async function resumeOrCreate(file: FileLike, request: typeof api, storage?: Storage): Promise<Session> {
  const key = fingerprint(file)
  const saved = storage?.getItem(key)
  if (saved) {
    try {
      const st = await request<UploadStatus>(`/api/v1/uploads/${saved}`)
      if (st.size === file.size) return { id: st.upload_id, chunkSize: st.chunk_size, total: st.total, received: st.received }
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) throw e
    }
    storage?.removeItem(key)
  }
  const created = await request<UploadCreated>('/api/v1/uploads', {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, size: file.size }),
  })
  storage?.setItem(key, created.upload_id)
  return { id: created.upload_id, chunkSize: created.chunk_size, total: created.total_chunks, received: [] }
}

/** Загрузка по частям с докачкой: до PARALLEL частей одновременно, повтор с задержкой, продолжение после перезагрузки. */
export async function uploadFile(file: FileLike, opts: UploadOptions = {}): Promise<UploadResult> {
  if (file.size <= 0) throw new Error('Пустой файл')
  const request = opts.request ?? api
  const sleep = opts.sleep ?? (ms => new Promise<void>(r => setTimeout(r, ms)))
  const retries = opts.retries ?? RETRIES
  const storage = opts.storage ?? defaultStorage()

  /** Одна полная попытка: докачка либо новая загрузка, отправка всех частей, завершение. */
  const attemptOnce = async (): Promise<UploadResult> => {
    const up = await resumeOrCreate(file, request, storage)
    const queue = missingChunks(up.total, up.received)
    let done = up.received.length
    let failed = false // фатальный сбой одного воркера — остальные не берут новые части
    const report = () => opts.onProgress?.(Math.min(file.size, done * up.chunkSize), file.size)
    report()

    const worker = async () => {
      for (let idx = queue.shift(); idx !== undefined; idx = queue.shift()) {
        if (failed) return
        const body = file.slice(idx * up.chunkSize, Math.min(file.size, (idx + 1) * up.chunkSize))
        for (let attempt = 0; ; attempt++) {
          try {
            await request(`/api/v1/uploads/${up.id}/chunks/${idx}`, {
              method: 'PUT',
              body,
              headers: { 'Content-Type': 'application/octet-stream' },
            })
            break
          } catch (e) {
            if (attempt >= retries || !isRetryable(e)) {
              failed = true
              throw e
            }
            await sleep(backoffMs(attempt))
          }
        }
        done++
        report()
      }
    }
    const workers = Math.min(opts.parallel ?? PARALLEL, Math.max(1, queue.length))
    await Promise.all(Array.from({ length: workers }, worker))

    const result = await request<UploadResult>(`/api/v1/uploads/${up.id}/complete`, { method: 'POST' })
    storage?.removeItem(fingerprint(file))
    return result
  }

  try {
    return await attemptOnce()
  } catch (e) {
    // 410 file_missing: запись о загрузке жива, а файла на диске уже нет — раз забываем ключ и грузим заново
    if (e instanceof ApiError && e.status === 410) {
      storage?.removeItem(fingerprint(file))
      return await attemptOnce()
    }
    throw e
  }
}
