/**
 * Переключатель сегментами: несколько значений полосой, одно отмечено.
 *
 * Внутри — обычные радиокнопки одной группы: стрелки с клавиатуры, фокус и подписи для читалки
 * экрана работают сами, а горячие клавиши редактора в поле ввода не срабатывают. У каждой группы
 * на странице своё имя: «Свойства» и «Рендер» показывают одно и то же.
 */
import { escapeHtml } from './html'

export type Segment = { value: string; label: string }

export function segmentedHtml(name: string, legend: string, items: Segment[], value: string, title = ''): string {
  const hint = title ? ` title="${escapeHtml(title)}"` : ''
  const options = items
    .map(item => {
      const checked = item.value === value ? ' checked' : ''
      return (
        `<label class="seg-item"><input type="radio" name="${escapeHtml(name)}" ` +
        `value="${escapeHtml(item.value)}"${checked} /><span>${escapeHtml(item.label)}</span></label>`
      )
    })
    .join('')
  return `<fieldset class="seg"${hint}><legend>${escapeHtml(legend)}</legend><div class="seg-items">${options}</div></fieldset>`
}

/** Отметить значение в уже нарисованной группе. Без перерисовки: фокус остаётся на месте. */
export function setSegmented(root: ParentNode, name: string, value: string): void {
  root.querySelectorAll<HTMLInputElement>(`input[type="radio"][name="${name}"]`).forEach(input => {
    input.checked = input.value === value
  })
}
