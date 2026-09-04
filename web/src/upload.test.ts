import { describe, expect, it } from 'vitest'
import { ApiError } from './api'
import { backoffMs, chunkCount, fingerprint, isRetryable, missingChunks, uploadFile } from './upload'

describe('chunk math', () => {
  it('counts chunks', () => {
    expect(chunkCount(2500, 1024)).toBe(3)
    expect(chunkCount(1024, 1024)).toBe(1)
    expect(chunkCount(1, 1024)).toBe(1)
  })
  it('lists missing chunks in order', () => {
    expect(missingChunks(4, [0, 2])).toEqual([1, 3])
    expect(missingChunks(2, [])).toEqual([0, 1])
    expect(missingChunks(2, [1, 0])).toEqual([])
  })
  it('fingerprint and backoff', () => {
    expect(fingerprint({ name: 'a.mp4', size: 5, lastModified: 7 })).toBe('upload:a.mp4:5:7')
    expect(backoffMs(0)).toBe(1000)
    expect(backoffMs(2)).toBe(4000)
  })
  it('retries only on network errors, 5xx and 429', () => {
    expect(isRetryable(new Error('net'))).toBe(true)
    expect(isRetryable(new ApiError(503, 'x', 'x'))).toBe(true)
    expect(isRetryable(new ApiError(429, 'x', 'x'))).toBe(true)
    expect(isRetryable(new ApiError(422, 'x', 'x'))).toBe(false)
    expect(isRetryable(new ApiError(401, 'x', 'x'))).toBe(false)
  })
})

function fakeFile(size: number) {
  const bytes = new Uint8Array(size).map((_, i) => i % 251)
  return { name: 'f.bin', size, lastModified: 1, slice: (s: number, e: number) => new Blob([bytes.slice(s, e)]) }
}

function memStorage() {
  const map = new Map<string, string>()
  return {
    map,
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
  }
}

const noSleep = async () => {}

describe('uploadFile', () => {
  it('creates, sends every chunk, completes and clears the resume key', async () => {
    const calls: string[] = []
    const request = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      calls.push(`${init.method ?? 'GET'} ${path}`)
      if (path === '/api/v1/uploads' && init.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({ filename: 'f.bin', size: 10 })
        return { upload_id: 'upl_1', chunk_size: 4, total_chunks: 3, expires_at: 'x' } as T
      }
      if (path.endsWith('/complete')) return { asset_id: 'ast_1', status: 'uploaded' } as T
      return undefined as T
    }
    const storage = memStorage()
    const progress: number[] = []
    const res = await uploadFile(fakeFile(10), { request, storage, sleep: noSleep, onProgress: d => progress.push(d) })
    expect(res.asset_id).toBe('ast_1')
    expect(calls.filter(c => c.startsWith('PUT')).sort()).toEqual([
      'PUT /api/v1/uploads/upl_1/chunks/0',
      'PUT /api/v1/uploads/upl_1/chunks/1',
      'PUT /api/v1/uploads/upl_1/chunks/2',
    ])
    expect(progress[0]).toBe(0)
    expect(progress.at(-1)).toBe(10)
    expect(storage.map.size).toBe(0)
  })

  it('resumes a saved upload and sends only the missing chunks', async () => {
    const storage = memStorage()
    storage.setItem('upload:f.bin:10:1', 'upl_9')
    const puts: string[] = []
    const request = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      if (path === '/api/v1/uploads/upl_9' && !init.method) {
        return { upload_id: 'upl_9', received: [0, 2], total: 3, size: 10, chunk_size: 4 } as T
      }
      if (init.method === 'PUT') {
        puts.push(path)
        return undefined as T
      }
      if (path.endsWith('/complete')) return { asset_id: 'ast_2', status: 'uploaded' } as T
      throw new Error('unexpected ' + path)
    }
    await uploadFile(fakeFile(10), { request, storage, sleep: noSleep })
    expect(puts).toEqual(['/api/v1/uploads/upl_9/chunks/1'])
  })

  it('starts over when the saved upload is gone and retries a failing chunk', async () => {
    const storage = memStorage()
    storage.setItem('upload:f.bin:10:1', 'upl_old')
    let failures = 0
    const request = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      if (path === '/api/v1/uploads/upl_old') throw new ApiError(404, 'not_found', 'нет')
      if (path === '/api/v1/uploads' && init.method === 'POST') {
        return { upload_id: 'upl_new', chunk_size: 4, total_chunks: 3, expires_at: 'x' } as T
      }
      if (init.method === 'PUT' && path.endsWith('/chunks/1') && failures++ < 2) throw new ApiError(503, 'x', 'busy')
      if (path.endsWith('/complete')) return { asset_id: 'ast_3', status: 'uploaded' } as T
      return undefined as T
    }
    const res = await uploadFile(fakeFile(10), { request, storage, sleep: noSleep })
    expect(res.asset_id).toBe('ast_3')
    expect(failures).toBe(3)
  })

  it('starts over once when the server says the upload file is gone', async () => {
    const storage = memStorage()
    storage.setItem('upload:f.bin:10:1', 'upl_stale')
    const created: string[] = []
    const request = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      if (path === '/api/v1/uploads/upl_stale' && !init.method) {
        return { upload_id: 'upl_stale', received: [], total: 3, size: 10, chunk_size: 4 } as T
      }
      if (path === '/api/v1/uploads' && init.method === 'POST') {
        created.push('new')
        return { upload_id: 'upl_fresh', chunk_size: 4, total_chunks: 3, expires_at: 'x' } as T
      }
      if (init.method === 'PUT' && path.startsWith('/api/v1/uploads/upl_stale')) {
        throw new ApiError(410, 'file_missing', 'пропал')
      }
      if (path.endsWith('/complete')) return { asset_id: 'ast_4', status: 'uploaded' } as T
      return undefined as T
    }
    const res = await uploadFile(fakeFile(10), { request, storage, sleep: noSleep })
    expect(res.asset_id).toBe('ast_4')
    expect(created).toEqual(['new'])
    expect(storage.map.size).toBe(0)
  })

  it('gives up on a 4xx and keeps the resume key', async () => {
    const storage = memStorage()
    const request = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      if (init.method === 'POST' && path === '/api/v1/uploads') {
        return { upload_id: 'u', chunk_size: 4, total_chunks: 3, expires_at: 'x' } as T
      }
      if (init.method === 'PUT') throw new ApiError(422, 'chunk_size_mismatch', 'bad')
      return undefined as T
    }
    await expect(uploadFile(fakeFile(10), { request, storage, sleep: noSleep })).rejects.toThrow('bad')
    expect(storage.map.get('upload:f.bin:10:1')).toBe('u')
  })

  it('rejects an empty file before touching the network', async () => {
    const request = async <T,>(): Promise<T> => {
      throw new Error('should not be called')
    }
    await expect(uploadFile(fakeFile(0), { request, storage: memStorage(), sleep: noSleep })).rejects.toThrow('Пустой')
  })
})
