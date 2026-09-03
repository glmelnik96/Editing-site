import { expect, test } from 'vitest'
import { ApiError, parseError } from './api'

test('parseError reads the api error envelope', () => {
  const e = parseError(403, { error: { code: 'cross_site', message: 'Отклонено', details: { a: 1 } } })
  expect(e).toBeInstanceOf(ApiError)
  expect(e.status).toBe(403)
  expect(e.code).toBe('cross_site')
  expect(e.message).toBe('Отклонено')
  expect(e.details).toEqual({ a: 1 })
})

test('parseError falls back for non-json bodies', () => {
  const e = parseError(502, '<html>Bad gateway</html>')
  expect(e.code).toBe('http_error')
  expect(e.message).toBe('<html>Bad gateway</html>')
  expect(parseError(500, null).message).toBe('HTTP 500')
})

test('parseError tolerates a null or codeless error object', () => {
  expect(parseError(500, { error: null }).code).toBe('http_error')
  expect(parseError(500, { error: { message: 'x' } }).code).toBe('http_error')
})
