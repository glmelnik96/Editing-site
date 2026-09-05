import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { createSaver, type Project } from './project'

function project(version = 1, clips: unknown[] = []): Project {
  return {
    id: 'prj_1',
    name: 'Мой',
    version,
    status: 'draft',
    created_at: 'x',
    updated_at: 'x',
    finished_at: null,
    doc: { output: { aspect: '16:9', fit: 'pad', fps: 30 }, clips, music: null, subtitles: null } as never,
  }
}

const tick = () => new Promise(resolve => setTimeout(resolve, 0))

describe('автосохранение', () => {
  it('ждёт тишины и шлёт одно сохранение вместо трёх', async () => {
    vi.useFakeTimers()
    const request = vi.fn(async (_p: Project) => project(2))
    const saver = createSaver({ request, delay: 500 })
    saver.schedule(project(1, [{ id: 'a' }]))
    saver.schedule(project(1, [{ id: 'b' }]))
    saver.schedule(project(1, [{ id: 'c' }]))
    expect(request).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(500)
    expect(request).toHaveBeenCalledTimes(1)
    expect(request.mock.calls[0][0].doc.clips).toEqual([{ id: 'c' }])
    vi.useRealTimers()
  })

  it('сохраняет по требованию сразу, без ожидания', async () => {
    const request = vi.fn(async () => project(2))
    const saver = createSaver({ request, delay: 500 })
    await saver.flush(project(1))
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('отдаёт наверх нормализованный ответ сервера', async () => {
    const saved = project(7, [{ id: 'server' }])
    const onSaved = vi.fn()
    const saver = createSaver({ request: async () => saved, delay: 0, onSaved })
    await saver.flush(project(1))
    expect(onSaved).toHaveBeenCalledWith(saved)
  })

  it('не шлёт два запроса одновременно: следующая правка ждёт ответа', async () => {
    let release: (p: Project) => void = () => {}
    const inFlight = new Promise<Project>(resolve => (release = resolve))
    const request = vi.fn((_p: Project) => inFlight)
    const saver = createSaver({ request, delay: 0 })
    const first = saver.flush(project(1))
    const second = saver.flush(project(1))
    expect(request).toHaveBeenCalledTimes(1)
    release(project(2))
    await first
    await second
    await tick()
    expect(request).toHaveBeenCalledTimes(2)
    expect(request.mock.calls[1][0].version).toBe(2) // вторая правка ушла уже с новой версией
  })

  it('сообщает о конфликте версий и не теряет ответ сервера', async () => {
    const fresh = project(9, [{ id: 'чужой' }])
    const onConflict = vi.fn()
    const request = async () => {
      throw new ApiError(409, 'version_conflict', 'устарело', { project: fresh })
    }
    const saver = createSaver({ request, delay: 0, onConflict })
    await saver.flush(project(1))
    expect(onConflict).toHaveBeenCalledWith(fresh)
  })

  it('сообщает об ошибке проверки списком полей', async () => {
    const onInvalid = vi.fn()
    const request = async () => {
      throw new ApiError(422, 'invalid_project', 'плохо', { errors: [{ field: 'clips[0].out', message: 'коротко' }] })
    }
    const saver = createSaver({ request, delay: 0, onInvalid })
    await saver.flush(project(1))
    expect(onInvalid).toHaveBeenCalledWith([{ field: 'clips[0].out', message: 'коротко' }])
  })

  it('прочие ошибки отдаёт как есть', async () => {
    const onError = vi.fn()
    const request = async () => {
      throw new ApiError(500, 'internal_error', 'ой')
    }
    const saver = createSaver({ request, delay: 0, onError })
    await saver.flush(project(1))
    expect(onError).toHaveBeenCalled()
  })

  it('после конфликта не пытается досохранить старое', async () => {
    const fresh = project(9)
    const request = vi.fn(async () => {
      throw new ApiError(409, 'version_conflict', 'устарело', { project: fresh })
    })
    const saver = createSaver({ request, delay: 0, onConflict: () => {} })
    await saver.flush(project(1))
    await saver.flush(project(1))
    expect(request).toHaveBeenCalledTimes(2) // каждая попытка честная, накопленной очереди нет
    expect(saver.pending()).toBe(false)
  })

  it('знает, есть ли несохранённые правки', async () => {
    vi.useFakeTimers()
    const saver = createSaver({ request: async () => project(2), delay: 500 })
    expect(saver.pending()).toBe(false)
    saver.schedule(project(1))
    expect(saver.pending()).toBe(true)
    await vi.advanceTimersByTimeAsync(500)
    expect(saver.pending()).toBe(false)
    vi.useRealTimers()
  })
})
