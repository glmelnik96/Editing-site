import { expect, test } from 'vitest'
import { segmentedHtml } from './segmented'

test('рисует радиокнопки одной группы и отмечает текущее значение', () => {
  const html = segmentedHtml(
    'roll-aspect',
    'Пропорция',
    [{ value: '16:9', label: '16:9' }, { value: '9:16', label: '9:16' }],
    '9:16',
  )
  expect(html.match(/type="radio" name="roll-aspect"/g)).toHaveLength(2)
  expect(html).toContain('value="9:16" checked')
  expect(html).not.toContain('value="16:9" checked')
  expect(html).toContain('<legend>Пропорция</legend>')
})

test('подписи и подсказка экранируются', () => {
  const html = segmentedHtml('x', '<b>', [{ value: 'a"b', label: '<i>' }], '', 'Поля — "кадр"')
  expect(html).toContain('&lt;b&gt;')
  expect(html).toContain('value="a&quot;b"')
  expect(html).toContain('&lt;i&gt;')
  expect(html).toContain('title="Поля — &quot;кадр&quot;"')
})
