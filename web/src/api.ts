export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: unknown = null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function parseError(status: number, body: unknown): ApiError {
  if (body && typeof body === 'object' && 'error' in body) {
    const err = (body as { error: { code?: string; message?: string; details?: unknown } }).error
    return new ApiError(status, err.code ?? 'error', err.message ?? `HTTP ${status}`, err.details ?? null)
  }
  const text = typeof body === 'string' && body ? body.slice(0, 200) : `HTTP ${status}`
  return new ApiError(status, 'http_error', text)
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const res = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (res.status === 204) return undefined as T
  const text = await res.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }
  if (!res.ok) throw parseError(res.status, body)
  return body as T
}
