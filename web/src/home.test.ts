import { describe, expect, it } from 'vitest'
import { homeStepsHtml } from './home'

describe('шаги кабинета', () => {
  it('ставит конвертер шагом 2.1 под редактором, не между загрузкой и склейкой', () => {
    const html = homeStepsHtml()
    expect(html).toContain('Шаг 1')
    expect(html).toContain('Загрузить исходники')
    expect(html).toContain('Шаг 2')
    expect(html).toContain('Открыть редактор')
    expect(html).toContain('Шаг 2.1')
    expect(html).toContain('Конвертировать')
    expect(html).toContain('step-cluster')
    expect(html.indexOf('Открыть редактор')).toBeLessThan(html.indexOf('Конвертировать'))
    expect(html.indexOf('Загрузить исходники')).toBeLessThan(html.indexOf('Открыть редактор'))
  })
})
