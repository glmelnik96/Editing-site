import { describe, expect, it } from 'vitest'
import { homeStepsHtml } from './home'

describe('шаги кабинета', () => {
  it('ведёт двумя шагами: сначала исходники, потом редактор', () => {
    const html = homeStepsHtml()
    expect(html).toContain('Шаг 1')
    expect(html).toContain('Загрузить исходники')
    expect(html).toContain('Шаг 2')
    expect(html).toContain('Открыть редактор')
    expect(html.indexOf('Загрузить исходники')).toBeLessThan(html.indexOf('Открыть редактор'))
  })

  it('конвертер — не шаг сборки: без номера и вне карточек пути', () => {
    const html = homeStepsHtml()
    expect(html).not.toContain('Шаг 2.1')
    expect(html).not.toContain('step-cluster')
    const steps = html.slice(html.indexOf('<div class="steps">'), html.indexOf('</div>'))
    expect(steps).not.toContain('#/convert')
  })

  it('строка конвертера ведёт на свой экран, не на записи', () => {
    const html = homeStepsHtml()
    const at = html.indexOf('конвертер')
    const slice = html.slice(at - 200, at + 200)
    expect(slice).toContain('href="#/convert"')
    expect(slice).not.toContain('href="#/files"')
  })
})
