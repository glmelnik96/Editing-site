/**
 * Складка: кнопка-заголовок и тело, которое она показывает.
 *
 * Отдельным модулем, потому что складок в «Исходниках» две и они связаны — открытая закрывает
 * соседнюю. Решает, какая открыта, редактор: он один видит обе. Здесь только разметка и
 * переключение видимости, поэтому панели выглядят одинаково и не расходятся, когда правят одну.
 */
import { escapeHtml } from './html'

/** Разметка складки. `id` даёт имена кнопке и телу: `<id>-head` и `<id>-body`. */
export function foldHtml(id: string, title: string, body: string): string {
  return `<div class="fold">
    <button type="button" class="fold-head" id="${id}-head" aria-expanded="true"
      aria-controls="${id}-body">
      <span class="fold-mark" aria-hidden="true">▾</span>${escapeHtml(title)}
    </button>
    <div class="fold-body" id="${id}-body">${body}</div>
  </div>`
}

/**
 * Связывает кнопку с телом и возвращает «показать или спрятать» тому, кто решает.
 *
 * Сама кнопка ничего не открывает: щелчок только сообщает наверх. Иначе две складки открывались
 * бы независимо, а договорённость ровно обратная — одновременно активной может быть одна.
 */
export function wireFold(el: HTMLElement, id: string, onToggle: () => void): (open: boolean) => void {
  const head = el.querySelector(`#${id}-head`) as HTMLButtonElement
  const body = el.querySelector(`#${id}-body`) as HTMLElement
  const mark = head.querySelector('.fold-mark') as HTMLElement
  head.addEventListener('click', onToggle)
  return (open: boolean) => {
    body.hidden = !open
    head.setAttribute('aria-expanded', String(open))
    mark.textContent = open ? '▾' : '▸'
  }
}
