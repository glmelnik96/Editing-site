import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * Раскладка монтажки по высоте окна держится стражем: живых проверок вёрстки нет (jsdom не
 * подключён), а вернувшееся `max-height: 52vh` или сворачивание снова утянули бы шкалу под сгиб.
 */
const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'style.css'), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
)
/** Тело правила, у которого ровно этот селектор и который начинается после `}` или с начала файла. */
const rule = (selector: string): string => {
  const escaped = selector.replace(/[.#:()[\]>+]/g, '\\$&')
  return css.match(new RegExp(`(^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`))?.[2] ?? ''
}

describe('монтажка по высоте окна', () => {
  it('по высоте окна — только экран редактора', () => {
    expect(rule('body.editor-fit #app')).toMatch(/height:\s*100vh/)
    expect(css).not.toMatch(/(^|\})\s*#app\s*\{[^}]*height:\s*100vh/)
  })

  it('верхний ряд не ниже 240 px, под ним инструменты и шкала', () => {
    expect(rule('.editor')).toMatch(/grid-template-rows:\s*minmax\(240px,\s*1fr\)\s+auto\s+auto/)
  })

  it('сетка появляется целиком', () => {
    expect(rule('.editor:not(.ready)')).toMatch(/visibility:\s*hidden/)
  })

  it('сцены на --stage-h и сворачивания панелей больше нет', () => {
    expect(css).not.toContain('--stage-h')
    expect(css).not.toMatch(/\.side-off|\.props-off|\.side-fold|\.props-title/)
  })

  it('боковые панели прокручиваются сами, а не страница', () => {
    expect(rule('.side-body')).toMatch(/overflow-y:\s*auto/)
    expect(rule('.side-body')).not.toMatch(/max-height/)
    expect(rule('.props-body')).toMatch(/overflow-y:\s*auto/)
  })

  it('радиокнопки сегментов прячутся от глаз, но не от клавиатуры', () => {
    expect(rule('.seg-item input')).toMatch(/opacity:\s*0/)
    expect(rule('.seg-item input')).not.toMatch(/display:\s*none|visibility:\s*hidden/)
    expect(css).toMatch(/\.seg-item input:focus-visible \+ span\s*\{[^}]*outline/)
  })

  it('«Собрать» прилипает к низу панели', () => {
    expect(rule('.rnd-go')).toMatch(/position:\s*sticky/)
    expect(rule('.rnd-go')).toMatch(/bottom:\s*0/)
  })

  it('инструменты одной строкой во всю ширину; уже 1220 px — переносятся группами', () => {
    expect(rule('.editor > .bar-edit')).toMatch(/flex-wrap:\s*nowrap/)
    expect(css).toMatch(/@media\s*\(max-width:\s*1219px\)\s*\{\s*\.editor > \.bar-edit\s*\{[^}]*flex-wrap:\s*wrap/)
    expect(css).not.toContain('#ed-out-group')
  })

  it('шкала: заголовки дорожек в своём столбце, высоты колей из переменных, полоса прокрутки на месте', () => {
    expect(rule('.timeline')).toMatch(/overflow-x:\s*scroll/)
    expect(rule('.tl-frame')).toMatch(/grid-template-columns:\s*88px/)
    expect(css).not.toContain('.lane-tag')
    const lanes: [string, string][] = [
      ['.track', '--lane-track'],
      ['.track-audio', '--lane-audio'],
      ['.sound-track', '--lane-sound'],
      ['.overlay-track', '--lane-overlay'],
    ]
    for (const [selector, variable] of lanes) expect(rule(selector)).toContain(`var(${variable})`)
  })
})
