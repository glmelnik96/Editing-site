/**
 * Свойства выбранного: панель справа от сцены.
 *
 * Работает по выбранному на шкале куску и показывает только то, что у него есть: у звука — звук,
 * у клипа со звуком — переход и его дорожку, у наложения — место в кадре и появление. Раньше все
 * эти контролы стояли одной строкой над шкалой, гасли и зажигались по выбору, и к строке из семи
 * полей взгляд возвращался как к чужой. Панель читается сверху вниз, и её можно свернуть.
 *
 * Ползунки шлют правку на каждый тик (live): звук должен меняться под пальцем сразу, а в историю
 * попадает одно состояние на всё движение — это решает редактор по флагу.
 */
import { escapeHtml } from './html'
import type { Overlay, OverlayPlace, Sound } from './project'
import type { Clip } from './timeline/model'
import { PLACES, SIZED_PLACES } from './timeline/overlays'

export type Selected =
  | { kind: 'none' }
  | { kind: 'clip'; clip: Clip; index: number; hasAudio: boolean; maxFade: number }
  | { kind: 'sound'; sound: Sound }
  | { kind: 'overlay'; overlay: Overlay; isImage: boolean }

export type InspectorHandlers = {
  onClip: (patch: Partial<Clip>, live: boolean) => void
  onSound: (patch: Partial<Sound>, live: boolean) => void
  onOverlay: (patch: Partial<Overlay>, live: boolean) => void
  /** Отказ, который надо сказать человеку: панель под шкалу сама не пишет. */
  onRefuse: (text: string) => void
}

/** Заголовок панели: что выбрано и сколько оно длится. */
export function selectionTitle(sel: Selected): string {
  switch (sel.kind) {
    case 'clip':
      return `Клип ${sel.clip.id} · ${(sel.clip.out - sel.clip.in).toFixed(1)} с`
    case 'sound':
      return `Звук ${sel.sound.id} · ${(sel.sound.out - sel.sound.in).toFixed(1)} с`
    case 'overlay':
      return `Наложение ${sel.overlay.id} · ${(sel.overlay.out - sel.overlay.in).toFixed(1)} с`
    default:
      return 'Ничего не выбрано'
  }
}

/**
 * Ключ разметки: выбрали другое — панель строится заново, то же — только обновляет значения.
 *
 * Перестраивать на каждое обновление нельзя: ползунок под пальцем терял бы фокус и захват, а
 * значения от сервера приходят через полсекунды после каждой правки.
 */
export function selectionKey(sel: Selected): string {
  switch (sel.kind) {
    case 'clip':
      return `clip:${sel.clip.id}:${sel.hasAudio}:${sel.index === 0}`
    case 'sound':
      return `sound:${sel.sound.id}`
    case 'overlay':
      return `overlay:${sel.overlay.id}:${sel.isImage}`
    default:
      return 'none'
  }
}

/** Появление и затухание вместе не длиннее самого куска — иначе они перекрылись бы. */
export function fadesFit(fadeIn: number, fadeOut: number, length: number): boolean {
  return fadeIn >= 0 && fadeOut >= 0 && fadeIn + fadeOut <= length + 1e-6
}

const VOLUME_NOTE = 'выше 1 — в сборке громче, чем слышно в превью'

function volumeRow(id: string, value: number, label = 'Громкость'): string {
  return `<label class="pr-row">${escapeHtml(label)}
    <span class="pr-range"><input id="${id}" type="range" min="0" max="2" step="0.01" value="${value}" />
    <output id="${id}-out">${value.toFixed(2)}</output></span>
    <span class="pr-note muted" id="${id}-note"${value > 1 ? '' : ' hidden'}>${VOLUME_NOTE}</span>
  </label>`
}

function numberRow(id: string, label: string, value: number, title = ''): string {
  return `<label class="pr-row">${escapeHtml(label)}
    <input id="${id}" class="tc" type="number" min="0" step="0.1" value="${value}" title="${escapeHtml(title)}" />
  </label>`
}

function checkRow(id: string, label: string, checked: boolean, title = ''): string {
  return `<label class="pr-check" title="${escapeHtml(title)}">
    <input id="${id}" type="checkbox"${checked ? ' checked' : ''} /> ${escapeHtml(label)}
  </label>`
}

function clipHtml(sel: Extract<Selected, { kind: 'clip' }>): string {
  const fade =
    sel.index === 0
      ? '<p class="muted pr-hint">Первый клип: переходить в него не из чего.</p>'
      : numberRow(
          'pr-fade',
          'Переход из предыдущего, с',
          sel.clip.transition?.duration ?? 0,
          'Плавный переход в этот клип из предыдущего. Ноль — стык.',
        )
  const audio = sel.hasAudio
    ? `<h4 class="pr-sub">Звук клипа</h4>${volumeRow('pr-volume', sel.clip.volume ?? 1)}`
    : '<p class="muted pr-hint">У этого клипа нет звуковой дорожки.</p>'
  return `${fade}${audio}`
}

function soundHtml(sel: Extract<Selected, { kind: 'sound' }>): string {
  const s = sel.sound
  return `${volumeRow('pr-volume', s.volume)}
    ${checkRow('pr-loop', 'По кругу до конца ролика', s.loop, 'Кусок повторяется, пока идёт ролик — так кладут музыку')}
    ${checkRow('pr-duck', 'Приглушать под речь', s.duck, 'Под речью клипов звук становится тише, в паузах — громче')}
    <div class="pr-pair">
      ${numberRow('pr-fade-in', 'Появление, с', s.fade_in)}
      ${numberRow('pr-fade-out', 'Затухание, с', s.fade_out)}
    </div>`
}

function overlayHtml(sel: Extract<Selected, { kind: 'overlay' }>): string {
  const o = sel.overlay
  const sized = SIZED_PLACES.has(o.place)
  return `<label class="pr-row">Место в кадре
      <select id="pr-place">${PLACES.map(
        p => `<option value="${p.value}"${p.value === o.place ? ' selected' : ''}>${escapeHtml(p.label)}</option>`,
      ).join('')}</select>
    </label>
    <label class="pr-row">Размер, % ширины кадра
      <span class="pr-range"><input id="pr-size" type="range" min="5" max="100" step="1" value="${o.size}"${sized ? '' : ' disabled'} />
      <output id="pr-size-out">${o.size}</output></span>
      <span class="pr-note muted" id="pr-size-note"${sized ? ' hidden' : ''}>у «весь кадр» и половин размер задаёт сама коробка</span>
    </label>
    <div class="pr-pair">
      ${numberRow('pr-fade-in', 'Появление, с', o.fade_in)}
      ${numberRow('pr-fade-out', 'Исчезновение, с', o.fade_out)}
    </div>
    ${sel.isImage ? '<p class="muted pr-hint">У картинки звука нет.</p>' : `<h4 class="pr-sub">Звук наложения</h4>${volumeRow('pr-volume', o.volume)}`}`
}

export function mountInspector(el: HTMLElement, handlers: InspectorHandlers) {
  let current: Selected = { kind: 'none' }
  let key = ''
  // Ползунок под пальцем: его значение не переписываем данными с сервера, пока движение не кончилось.
  let sliding: HTMLInputElement | null = null

  const q = <T extends HTMLElement>(sel: string): T | null => el.querySelector(sel) as T | null

  function build(): void {
    let body: string
    switch (current.kind) {
      case 'clip':
        body = clipHtml(current)
        break
      case 'sound':
        body = soundHtml(current)
        break
      case 'overlay':
        body = overlayHtml(current)
        break
      default:
        body = '<p class="muted pr-hint">Щёлкните по куску на шкале — здесь будут его свойства.</p>'
    }
    el.innerHTML = `<h3 class="pr-title">${escapeHtml(selectionTitle(current))}</h3>${body}`
    wire()
  }

  /** Ползунок: на каждый тик — live, в конце — обычная правка тем же значением. */
  function slider(id: string, emit: (value: number, live: boolean) => void): void {
    const input = q<HTMLInputElement>(`#${id}`)
    const out = q<HTMLOutputElement>(`#${id}-out`)
    const note = q<HTMLElement>(`#${id}-note`)
    if (!input) return
    const show = (value: number) => {
      if (out) out.value = id === 'pr-size' ? String(value) : value.toFixed(2)
      if (note && id === 'pr-volume') note.hidden = value <= 1
    }
    input.addEventListener('input', () => {
      sliding = input
      const value = Number(input.value)
      show(value)
      emit(value, true)
    })
    input.addEventListener('change', () => {
      sliding = null
      emit(Number(input.value), false)
    })
  }

  /** Число: применяется по change, непонятное или недопустимое возвращается к прежнему. */
  function number(id: string, accept: (value: number) => string | null): void {
    const input = q<HTMLInputElement>(`#${id}`)
    if (!input) return
    input.addEventListener('change', () => {
      const value = Number(input.value.replace(',', '.'))
      const refusal = Number.isFinite(value) ? accept(Math.round(value * 1000) / 1000) : 'Нужно число секунд'
      if (refusal) {
        handlers.onRefuse(refusal)
        paint()
      }
    })
  }

  function wire(): void {
    if (current.kind === 'clip') {
      const sel = current
      slider('pr-volume', (value, live) => handlers.onClip({ volume: value }, live))
      number('pr-fade', value => {
        if (value < 0) return 'Переход не может быть отрицательным'
        if (value > sel.maxFade + 1e-6) {
          return `Переход не длиннее ${sel.maxFade.toFixed(1)} с: он должен быть короче обоих клипов`
        }
        handlers.onClip({ transition: value > 0 ? { kind: 'fade', duration: value } : undefined }, false)
        return null
      })
    }
    if (current.kind === 'sound') {
      const sel = current
      slider('pr-volume', (value, live) => handlers.onSound({ volume: value }, live))
      q<HTMLInputElement>('#pr-loop')?.addEventListener('change', event => {
        handlers.onSound({ loop: (event.target as HTMLInputElement).checked }, false)
      })
      q<HTMLInputElement>('#pr-duck')?.addEventListener('change', event => {
        handlers.onSound({ duck: (event.target as HTMLInputElement).checked }, false)
      })
      const length = sel.sound.out - sel.sound.in
      for (const [id, field] of [
        ['pr-fade-in', 'fade_in'],
        ['pr-fade-out', 'fade_out'],
      ] as const) {
        number(id, value => {
          const other = field === 'fade_in' ? sel.sound.fade_out : sel.sound.fade_in
          // У звука по кругу край — конец ролика, а не куска: там затухания зажмёт сборка.
          if (value < 0) return 'Затухание не может быть отрицательным'
          if (!sel.sound.loop && !fadesFit(field === 'fade_in' ? value : other, field === 'fade_in' ? other : value, length)) {
            return `Появление и затухание вместе не длиннее самого звука (${length.toFixed(1)} с)`
          }
          handlers.onSound(field === 'fade_in' ? { fade_in: value } : { fade_out: value }, false)
          return null
        })
      }
    }
    if (current.kind === 'overlay') {
      const sel = current
      q<HTMLSelectElement>('#pr-place')?.addEventListener('change', event => {
        handlers.onOverlay({ place: (event.target as HTMLSelectElement).value as OverlayPlace }, false)
      })
      slider('pr-size', (value, live) => handlers.onOverlay({ size: value }, live))
      slider('pr-volume', (value, live) => handlers.onOverlay({ volume: value }, live))
      const length = sel.overlay.out - sel.overlay.in
      for (const [id, field] of [
        ['pr-fade-in', 'fade_in'],
        ['pr-fade-out', 'fade_out'],
      ] as const) {
        number(id, value => {
          const other = field === 'fade_in' ? sel.overlay.fade_out : sel.overlay.fade_in
          if (value < 0) return 'Появление не может быть отрицательным'
          if (!fadesFit(field === 'fade_in' ? value : other, field === 'fade_in' ? other : value, length)) {
            return `Появление и исчезновение вместе не длиннее самого наложения (${length.toFixed(1)} с)`
          }
          handlers.onOverlay(field === 'fade_in' ? { fade_in: value } : { fade_out: value }, false)
          return null
        })
      }
    }
  }

  /** Обновить значения полей без перестройки: то же выбранное, свежие числа. */
  function paint(): void {
    const set = (id: string, value: string | number) => {
      const input = q<HTMLInputElement>(`#${id}`)
      if (!input || input === sliding) return
      input.value = String(value)
      const out = q<HTMLOutputElement>(`#${id}-out`)
      if (out) out.value = id === 'pr-size' ? String(value) : Number(value).toFixed(2)
    }
    const check = (id: string, value: boolean) => {
      const input = q<HTMLInputElement>(`#${id}`)
      if (input) input.checked = value
    }
    const title = q<HTMLElement>('.pr-title')
    if (title) title.textContent = selectionTitle(current)
    if (current.kind === 'clip') {
      set('pr-volume', current.clip.volume ?? 1)
      set('pr-fade', current.clip.transition?.duration ?? 0)
      const note = q<HTMLElement>('#pr-volume-note')
      if (note) note.hidden = (current.clip.volume ?? 1) <= 1
    } else if (current.kind === 'sound') {
      set('pr-volume', current.sound.volume)
      check('pr-loop', current.sound.loop)
      check('pr-duck', current.sound.duck)
      set('pr-fade-in', current.sound.fade_in)
      set('pr-fade-out', current.sound.fade_out)
      const note = q<HTMLElement>('#pr-volume-note')
      if (note) note.hidden = current.sound.volume <= 1
    } else if (current.kind === 'overlay') {
      const o = current.overlay
      const place = q<HTMLSelectElement>('#pr-place')
      if (place) place.value = o.place
      set('pr-size', o.size)
      const size = q<HTMLInputElement>('#pr-size')
      const sized = SIZED_PLACES.has(o.place)
      if (size) size.disabled = !sized
      const sizeNote = q<HTMLElement>('#pr-size-note')
      if (sizeNote) sizeNote.hidden = sized
      set('pr-fade-in', o.fade_in)
      set('pr-fade-out', o.fade_out)
      set('pr-volume', o.volume)
    }
  }

  build()

  return {
    /** Показать выбранное. Другой кусок — новая разметка, тот же — только значения. */
    set(next: Selected): void {
      current = next
      const nextKey = selectionKey(next)
      if (nextKey !== key) {
        key = nextKey
        sliding = null
        build()
      } else {
        paint()
      }
    },
  }
}
