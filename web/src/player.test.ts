import { describe, expect, it } from 'vitest'
import { playerMarkup, progressText, thumbAt } from './player'

const meta = { count: 6, cols: 3, rows: 2, interval: 2, width: 160, height: 90 }

describe('player helpers', () => {
  it('maps a moment to a sprite cell', () => {
    expect(thumbAt(meta, 0)).toEqual({ index: 0, x: 0, y: 0 })
    expect(thumbAt(meta, 2.5)).toEqual({ index: 1, x: -160, y: 0 })
    expect(thumbAt(meta, 7)).toEqual({ index: 3, x: 0, y: -90 })
    expect(thumbAt(meta, 999)).toEqual({ index: 5, x: -320, y: -90 })
    expect(thumbAt(meta, -5)).toEqual({ index: 0, x: 0, y: 0 })
  })

  it('describes processing progress in words', () => {
    expect(progressText('uploaded', null)).toBe('ждёт обработки')
    expect(progressText('analyzing', 0.5)).toBe('анализ, 50 %')
    expect(progressText('analyzing', null)).toBe('анализ')
    expect(progressText('ready', 0.25)).toBe('готовим прокси, 25 %')
    expect(progressText('proxy_ready', 1)).toBe('')
    expect(progressText('failed', null)).toBe('')
  })

  it('builds a video element for a video proxy and audio for sound', () => {
    expect(playerMarkup({ proxy: '/files/u/assets/a/proxy.mp4' }, 'video')).toContain('<video')
    expect(playerMarkup({ proxy: '/files/u/assets/a/proxy.m4a' }, 'audio')).toContain('<audio')
    expect(playerMarkup({ proxy: null }, 'video')).toBe('')
    expect(playerMarkup({ proxy: '/x"onerror="alert(1)' }, 'video')).not.toContain('onerror="alert')
  })
})
