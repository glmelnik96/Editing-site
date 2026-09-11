import { expect, test } from 'vitest'
import type { Asset } from './assets'
import { addLabel, canOverlay, isPlaceable, sourcePoolNote } from './source'

test('без готовых записей — ссылка загрузить', () => {
  expect(sourcePoolNote(0)).toContain('#/files')
  expect(sourcePoolNote(0)).toContain('Загрузите')
  expect(sourcePoolNote(1)).toBe('')
})

const asset = (over: Partial<Asset>): Asset =>
  ({
    id: 'ast_1',
    kind: 'video',
    original_name: 'a.mp4',
    size: 1,
    status: 'proxy_ready',
    duration: 10,
    error: null,
    files: { proxy: null, thumbs: null, thumbs_meta: null, peaks: null, analysis: null, vtt: null, transcript: null },
    created_at: 'x',
    last_access_at: 'x',
    ...over,
  }) as Asset

test('на шкалу идут готовые видео, картинки и звук, но не субтитры', () => {
  expect(isPlaceable(asset({}))).toBe(true)
  // У картинки длительности нет — и это не повод не пускать её на шкалу: сколько она держится
  // в кадре, решают при добавлении.
  expect(isPlaceable(asset({ kind: 'image', duration: null }))).toBe(true)
  // Звук идёт не в клипы, а на свою дорожку, но выбирают его в той же панели.
  expect(isPlaceable(asset({ kind: 'audio' }))).toBe(true)
  expect(isPlaceable(asset({ kind: 'subtitle' }))).toBe(false)
})

test('кнопка говорит, куда ляжет кусок', () => {
  expect(addLabel('audio')).toBe('Положить на звуковую дорожку')
  expect(addLabel('video')).toBe('Добавить в шкалу')
  expect(addLabel(undefined)).toBe('Добавить в шкалу')
})

test('необработанную запись в список не пускаем', () => {
  expect(isPlaceable(asset({ status: 'analyzing' }))).toBe(false)
  expect(isPlaceable(asset({ status: 'failed' }))).toBe(false)
  expect(isPlaceable(asset({ kind: 'image', status: 'uploaded' }))).toBe(false)
  // «ready» без прокси годится: кусок можно отметить по таймкоду, не видя кадра.
  expect(isPlaceable(asset({ status: 'ready' }))).toBe(true)
})

test('поверх основы кладут картинку и видео, но не звук', () => {
  expect(canOverlay('image')).toBe(true)
  expect(canOverlay('video')).toBe(true)
  expect(canOverlay('audio')).toBe(false)
  expect(canOverlay(undefined)).toBe(false)
})
