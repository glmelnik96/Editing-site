import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * Токены держатся стражем: живых проверок вёрстки у нас нет (jsdom не подключён), а сорванная
 * шкала расползается молча — одно `transition: 0.15s` или `font-size: 13px` в новом правиле, и
 * через месяц их снова тринадцать.
 */
const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'style.css'), 'utf8')
const root = css.match(/:root\s*\{([^}]*)\}/)?.[1] ?? ''
const body = css.replace(/:root\s*\{[^}]*\}/, '').replace(/\/\*[\s\S]*?\*\//g, '')

function luminance(token: string): number {
  const hex = root.match(new RegExp(`--${token}:\\s*#([0-9a-f]{6})`, 'i'))?.[1]
  if (!hex) throw new Error(`Missing color token: ${token}`)
  const rgb = [0, 2, 4].map(i => {
    const s = parseInt(hex.slice(i, i + 2), 16) / 255
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722
}

function contrast(a: string, b: string): number {
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

describe('токены', () => {
  it('серый текст и рамка полей проходят WCAG', () => {
    for (const surface of ['ink', 'panel', 'surface-control']) {
      for (const text of ['paper', 'muted', 'dim']) {
        expect(contrast(text, surface), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5)
      }
      expect(contrast('border-field', surface)).toBeGreaterThanOrEqual(3)
    }
    expect(contrast('brand-ink', 'brand')).toBeGreaterThanOrEqual(4.5)
    const field =
      css.match(/input:not\(\[type='checkbox'\]\):not\(\[type='radio'\]\),\s*select,\s*textarea\s*\{[^}]*\}/)?.[0] ?? ''
    expect(field).toContain('var(--border-field)')
  })

  it('длительности переходов — только из токенов', () => {
    // 0.01ms — выключатель движения в блоке prefers-reduced-motion, а не длительность дизайна.
    for (const decl of body.match(/transition(?:-duration)?\s*:[^;]+;/g) ?? []) {
      expect(decl.replace(/\b0\.01ms\b/g, ''), decl).not.toMatch(/\d(?:\.\d+)?m?s\b/)
    }
  })

  it('размеры шрифта — только из токенов', () => {
    for (const decl of body.match(/font-size\s*:[^;]+;/g) ?? []) expect(decl, decl).not.toMatch(/\d+px/)
    for (const decl of body.match(/(?<![-\w])font\s*:[^;]+;/g) ?? []) expect(decl, decl).not.toMatch(/\d+px/)
  })

  it('новых цветов вне токенов нет', () => {
    const hexes = (body.match(/#[0-9a-f]{3,8}\b/gi) ?? []).map(h => h.toLowerCase())
    expect(new Set(hexes)).toEqual(new Set(['#fff', '#000']))
  })

  it('спрятанное прячется, даже если у элемента свой display', () => {
    expect(css).toMatch(/\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}/)
  })

  it('вкладки в одну строку, ползунок масштаба не растягивается', () => {
    expect(css.match(/\.tabs\s*\{[^}]*\}/)?.[0]).toContain('flex-wrap: nowrap')
    expect(css.match(/\.zoom\s*\{[^}]*\}/)?.[0]).toContain('flex: none')
  })

  it('подписи блоков шкалы — в одну строку с многоточием', () => {
    const rule = css.match(/\.block \.label,\s*\.lane-block \.label\s*\{[^}]*\}/)?.[0] ?? ''
    expect(rule).toContain('white-space: nowrap')
    expect(rule).toContain('text-overflow: ellipsis')
  })
})
