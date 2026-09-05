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

type Saver = ReturnType<typeof createSaver>
/** flush отвергает промис, когда правка не долетела: здесь проверяем колбэки, а не сам отказ. */
const flushFailing = (saver: Saver, p: Project) => saver.flush(p).catch(() => {})

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
    await flushFailing(saver, project(1))
    expect(onConflict).toHaveBeenCalledWith(fresh)
  })

  it('сообщает об ошибке проверки списком полей', async () => {
    const onInvalid = vi.fn()
    const request = async () => {
      throw new ApiError(422, 'invalid_project', 'плохо', { errors: [{ field: 'clips[0].out', message: 'коротко' }] })
    }
    const saver = createSaver({ request, delay: 0, onInvalid })
    await flushFailing(saver, project(1))
    expect(onInvalid).toHaveBeenCalledWith([{ field: 'clips[0].out', message: 'коротко' }])
  })

  it('прочие ошибки отдаёт как есть', async () => {
    const onError = vi.fn()
    const request = async () => {
      throw new ApiError(500, 'internal_error', 'ой')
    }
    const saver = createSaver({ request, delay: 0, onError })
    await flushFailing(saver, project(1))
    expect(onError).toHaveBeenCalled()
  })

  it('после конфликта не пытается досохранить старое', async () => {
    const fresh = project(9)
    const request = vi.fn(async () => {
      throw new ApiError(409, 'version_conflict', 'устарело', { project: fresh })
    })
    const saver = createSaver({ request, delay: 0, onConflict: () => {} })
    await flushFailing(saver, project(1))
    await flushFailing(saver, project(1))
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

describe('flush отвечает за то, что правка записана', () => {
  it('во время летящего запроса ждёт, пока очередь опустеет', async () => {
    const order: string[] = []
    let release: (p: Project) => void = () => {}
    const inFlight = new Promise<Project>(resolve => (release = resolve))
    let calls = 0
    const request = vi.fn((_p: Project) => {
      if (++calls === 1) return inFlight
      order.push('очередь записана')
      return Promise.resolve(project(3))
    })
    const saver = createSaver({ request, delay: 0 })
    const first = saver.flush(project(1, [{ id: 'a' }]))
    const second = saver.flush(project(1, [{ id: 'b' }])).then(() => order.push('flush вернулся'))
    expect(request).toHaveBeenCalledTimes(1) // вторая правка пока в очереди
    release(project(2))
    await first
    await second
    expect(request.mock.calls[1][0].doc.clips).toEqual([{ id: 'b' }])
    // Порядок и есть суть: flush не вправе резолвиться раньше, чем очередь дошла до сервера.
    expect(order).toEqual(['очередь записана', 'flush вернулся'])
    expect(saver.pending()).toBe(false)
  })

  it('бросает, когда сохранение провалилось', async () => {
    const onError = vi.fn()
    const saver = createSaver({
      request: async () => {
        throw new ApiError(500, 'internal_error', 'ой')
      },
      delay: 0,
      onError,
    })
    await expect(saver.flush(project(1))).rejects.toThrow('ой')
    expect(onError).toHaveBeenCalled() // колбэк работает как раньше, редактор на нём завязан
  })

  it('бросает при конфликте версий', async () => {
    const fresh = project(9, [{ id: 'чужой' }])
    const onConflict = vi.fn()
    const saver = createSaver({
      request: async () => {
        throw new ApiError(409, 'version_conflict', 'устарело', { project: fresh })
      },
      delay: 0,
      onConflict,
    })
    await expect(saver.flush(project(1))).rejects.toThrow('устарело')
    expect(onConflict).toHaveBeenCalledWith(fresh)
  })

  it('молчит, если сорвалось звено, а следующая правка его перекрыла', async () => {
    let fail: (e: unknown) => void = () => {}
    const inFlight = new Promise<Project>((_, reject) => (fail = reject))
    let calls = 0
    const request = vi.fn((_p: Project) => (++calls === 1 ? inFlight : Promise.resolve(project(3))))
    const saver = createSaver({ request, delay: 0, onError: () => {} })
    // Обе правки ждём разом: сорванная первая перекрыта второй, на сервере лежит свежий документ.
    const settled = Promise.allSettled([saver.flush(project(1, [{ id: 'a' }])), saver.flush(project(1, [{ id: 'b' }]))])
    fail(new ApiError(500, 'internal_error', 'ой'))
    expect(await settled).toEqual([
      { status: 'fulfilled', value: undefined },
      { status: 'fulfilled', value: undefined },
    ])
    expect(calls).toBe(2)
  })
})

describe('состояние после сбоя', () => {
  it('не показывает «сохранено», когда сохранение провалилось', async () => {
    const states: string[] = []
    const saver = createSaver({
      request: async () => {
        throw new ApiError(500, 'internal_error', 'ой')
      },
      delay: 0,
      onError: () => {},
      onStateChange: s => states.push(s),
    })
    await flushFailing(saver, project(1))
    expect(states.at(-1)).toBe('failed')
  })

  it('после удачного сохранения снова показывает «сохранено»', async () => {
    let fail = true
    const states: string[] = []
    const saver = createSaver({
      request: async () => {
        if (fail) {
          fail = false
          throw new ApiError(500, 'internal_error', 'ой')
        }
        return project(2)
      },
      delay: 0,
      onError: () => {},
      onStateChange: s => states.push(s),
    })
    await flushFailing(saver, project(1))
    await saver.flush(project(1))
    expect(states.at(-1)).toBe('idle')
  })
})
