/**
 * Блок «Ролик» внизу «Свойств»: пропорция и вписывание всего ролика.
 *
 * Эти настройки меняют то, что видно на сцене, поэтому живут рядом с ней, а не только во вкладке
 * «Рендер». Когда на шкале выбран кусок, блок свёрнут в строку сводки: сводку видно всегда,
 * переключатели — по щелчку.
 */
import type { Output } from './project'
import { segmentedHtml, setSegmented, type Segment } from './segmented'

export const ASPECT_ITEMS: Segment[] = [
  { value: '16:9', label: '16:9' },
  { value: '9:16', label: '9:16' },
  { value: '1:1', label: '1:1' },
]
export const FIT_ITEMS: Segment[] = [
  { value: 'pad', label: 'Поля' },
  { value: 'crop', label: 'Обрезка' },
]
export const FPS_ITEMS: Segment[] = [25, 30, 50, 60].map(n => ({ value: String(n), label: String(n) }))
export const FIT_HINT = 'Поля — кадр целиком, с полосами по краям; обрезка — кадр заполнен, края срезаны'

/** Сводка в голове блока: видна и у свёрнутого. */
export function rollSummary(output: Pick<Output, 'aspect' | 'fit'>): string {
  return `Ролик · ${output.aspect} · ${output.fit === 'crop' ? 'обрезка' : 'поля'}`
}

export type RollFold = { picked: boolean; open: boolean }

/**
 * Раскрыт ли блок. Правило срабатывает, когда выбор меняется с пустого на непустой и обратно:
 * пусто — раскрыт, выбран кусок — свёрнут. Иначе держится положение, выбранное вручную.
 */
export function nextRollFold(prev: RollFold | null, picked: boolean): RollFold {
  if (!prev || prev.picked !== picked) return { picked, open: !picked }
  return prev
}

export type RollHandlers = { onChange: (patch: Partial<Output>) => void }

export function mountRoll(el: HTMLElement, handlers: RollHandlers) {
  el.innerHTML = `
    <button type="button" class="roll-head" id="roll-head" aria-expanded="true" aria-controls="roll-body">
      <span class="fold-mark" aria-hidden="true">▾</span><span id="roll-summary">Ролик</span>
    </button>
    <div class="roll-body" id="roll-body">
      ${segmentedHtml('roll-aspect', 'Пропорция', ASPECT_ITEMS, '16:9')}
      ${segmentedHtml('roll-fit', 'Вписывание', FIT_ITEMS, 'pad', FIT_HINT)}
    </div>`
  const head = el.querySelector('#roll-head') as HTMLButtonElement
  const mark = head.querySelector('.fold-mark') as HTMLElement
  const summary = el.querySelector('#roll-summary') as HTMLElement
  const body = el.querySelector('#roll-body') as HTMLElement
  let fold: RollFold | null = null

  function paintFold(): void {
    const open = fold?.open ?? true
    body.hidden = !open
    head.setAttribute('aria-expanded', String(open))
    mark.textContent = open ? '▾' : '▸'
  }

  head.addEventListener('click', () => {
    if (!fold) return
    fold = { ...fold, open: !fold.open }
    paintFold()
  })
  el.addEventListener('change', event => {
    const input = event.target as HTMLInputElement
    if (input.name === 'roll-aspect') handlers.onChange({ aspect: input.value as Output['aspect'] })
    else if (input.name === 'roll-fit') handlers.onChange({ fit: input.value as Output['fit'] })
  })

  return {
    /** Показать настройки ролика; свернуть или раскрыть блок по выбору на шкале (nextRollFold). */
    set(output: Output, picked: boolean): void {
      summary.textContent = rollSummary(output)
      setSegmented(el, 'roll-aspect', output.aspect)
      setSegmented(el, 'roll-fit', output.fit)
      fold = nextRollFold(fold, picked)
      paintFold()
    },
  }
}
