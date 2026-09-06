/** Кабинет: три белых списка в одной таблице — строка человек, столбец сервис. */
import { api, ApiError } from './api'
import { escapeHtml } from './html'

type Service = { key: string; title: string; state: string; message: string }
type Person = { email: string; admin: boolean; access: Record<string, boolean | null> }
type CabinetView = { services: Service[]; people: Person[] }
type Change = { service: string; action: string; ok: boolean; error: string | null }

/** Состояние сервиса человеческим словом: код состояния в заголовке столбца ничего не объясняет. */
const STATE_TEXT: Record<string, string> = {
  unconfigured: 'не настроен',
  unavailable: 'недоступен',
  forbidden: 'отказал в доступе',
  bad_response: 'ответил не тем',
}

/** Сообщение соседа уже начинается с его названия — второй раз приписывать не надо. */
function named(title: string, text: string): string {
  return text.startsWith(`${title}:`) ? text : `${title}: ${text}`
}

export function mountCabinet(el: HTMLElement): void {
  el.innerHTML = `
    <main class="card">
      <h2>Кабинет</h2>
      <p class="muted">Строка — человек, столбец — сервис. Галочка отправляет правку сразу.</p>
      <div id="cab-body"><p class="muted">Читаю списки…</p></div>
      <ul id="cab-results" class="versions" hidden></ul>
      <pre id="cab-error" hidden></pre>
    </main>`
  const body = el.querySelector('#cab-body') as HTMLElement
  const resultsBox = el.querySelector('#cab-results') as HTMLElement
  const errorBox = el.querySelector('#cab-error') as HTMLPreElement
  let services: Service[] = []

  const titleOf = (key: string): string => services.find(s => s.key === key)?.title ?? key

  const clearMessages = (): void => {
    resultsBox.hidden = true
    resultsBox.innerHTML = ''
    errorBox.hidden = true
    errorBox.textContent = ''
  }

  const showText = (text: string): void => {
    errorBox.hidden = false
    errorBox.textContent = text
  }

  const showError = (e: unknown): void => {
    showText(e instanceof ApiError ? `Ошибка: ${e.message}` : String(e))
  }

  /** Удачные и неудачные правки вместе: частичный успех обязан быть виден поимённо. */
  function showResults(results: Change[]): void {
    resultsBox.hidden = results.length === 0
    resultsBox.innerHTML = results
      .map(r => {
        const text = r.ok ? (r.action === 'grant' ? 'доступ дан' : 'доступ снят') : r.error || 'не вышло'
        return `<li${r.ok ? '' : ' class="error"'}>${escapeHtml(named(titleOf(r.service), text))}</li>`
      })
      .join('')
  }

  function head(): string {
    const cols = services
      .map(s => {
        const note =
          s.state === 'ok' ? '' : `<span class="muted">${escapeHtml(STATE_TEXT[s.state] ?? s.state)}</span>`
        const hint = s.message ? ` title="${escapeHtml(s.message)}"` : ''
        return `<th class="access"${hint}>${escapeHtml(s.title)}${note}</th>`
      })
      .join('')
    return `<tr><th>Адрес</th>${cols}<th></th></tr>`
  }

  function cell(p: Person, s: Service): string {
    const value = p.access[s.key] ?? null
    // «Не знаем» и «нет доступа» — разные вещи: у недоступного сервиса пустая галочка соврала бы,
    // будто человека там нет. Точка честнее.
    if (value === null) return '<td class="access muted">·</td>'
    return `<td class="access"><input type="checkbox" data-email="${escapeHtml(p.email)}"
      data-service="${escapeHtml(s.key)}"${value ? ' checked' : ''} /></td>`
  }

  function row(p: Person): string {
    // Доступ администратора задан конфигурацией каждого сервиса, в списках его может не быть вовсе:
    // и снятая, и поставленная галочка тут одинаково врут, поэтому галочек в строке нет ни одной.
    if (p.admin) {
      const cols = services.map(() => '<td class="access muted">из конфигурации</td>').join('')
      return `<tr><td>${escapeHtml(p.email)}</td>${cols}<td></td></tr>`
    }
    const cols = services.map(s => cell(p, s)).join('')
    // Снимаем только там, где доступ точно есть: недоступный сервис не трогаем.
    const targets = services.filter(s => p.access[s.key] === true).map(s => s.key)
    const off = targets.length === 0 ? ' disabled' : ''
    return `<tr><td>${escapeHtml(p.email)}</td>${cols}
      <td><button type="button" data-drop="${escapeHtml(p.email)}"
        data-keys="${escapeHtml(targets.join(','))}"${off}>Убрать отовсюду</button></td></tr>`
  }

  function addForm(): string {
    const boxes = services
      .map(
        s =>
          `<label><input type="checkbox" data-add="${escapeHtml(s.key)}"${s.state === 'ok' ? '' : ' disabled'} />
            ${escapeHtml(s.title)}</label>`,
      )
      .join('')
    return `<form id="cab-add">
      <input name="email" type="email" placeholder="user@yandex.ru" required />
      ${boxes}
      <button type="submit">Добавить</button>
    </form>`
  }

  /** Одна правка: до ответа кнопки заперты, после — таблица перечитывается и показывается итог. */
  async function send(email: string, grant: string[], revoke: string[]): Promise<void> {
    clearMessages()
    try {
      const { results } = await api<{ results: Change[] }>('/api/v1/admin/cabinet/access', {
        method: 'POST',
        body: JSON.stringify({ email, grant, revoke }),
      })
      showResults(results)
    } catch (e) {
      showError(e)
    }
    // Перечитываем в любом случае: при частичном успехе часть правок уже применена, и таблица
    // без перечитывания показывала бы то, чего на сервисах больше нет.
    await refresh().catch(showError)
  }

  /** Один текст на оба способа снять доступ: обещание про обрыв сессий должно звучать одинаково. */
  function dropWarning(email: string, keys: string[]): string {
    return (
      `Убрать ${email} из сервисов: ${keys.map(titleOf).join(', ')}?

` +
      'Доступ снимется сразу: открытые сессии оборвутся, и человека выкинет прямо посреди работы.'
    )
  }

  function wire(): void {
    body.querySelectorAll<HTMLInputElement>('input[data-service]').forEach(box =>
      box.addEventListener('change', () => {
        const key = box.dataset.service ?? ''
        const email = box.dataset.email ?? ''
        // Снятие спрашивает подтверждение, выдача — нет: галочка вниз обрывает живые сессии,
        // и промах мышью по чужой строке выкинул бы человека посреди работы.
        if (!box.checked && !window.confirm(dropWarning(email, [key]))) {
          box.checked = true
          return
        }
        box.disabled = true // второй щелчок отправил бы правку поверх неотвеченной
        void send(email, box.checked ? [key] : [], box.checked ? [] : [key])
      }),
    )

    body.querySelectorAll<HTMLButtonElement>('button[data-drop]').forEach(b =>
      b.addEventListener('click', () => {
        const email = b.dataset.drop ?? ''
        const keys = (b.dataset.keys ?? '').split(',').filter(Boolean)
        if (keys.length === 0) return
        if (!window.confirm(dropWarning(email, keys))) return
        b.disabled = true
        void send(email, [], keys)
      }),
    )

    const form = body.querySelector('#cab-add') as HTMLFormElement | null
    form?.addEventListener('submit', ev => {
      ev.preventDefault()
      const email = String(new FormData(form).get('email') ?? '').trim()
      const keys = Array.from(form.querySelectorAll<HTMLInputElement>('input[data-add]'))
        .filter(b => b.checked)
        .map(b => b.dataset.add ?? '')
      if (keys.length === 0) {
        showText('Отметьте хотя бы один сервис')
        return
      }
      void send(email, keys, [])
    })
  }

  async function refresh(): Promise<void> {
    const view = await api<CabinetView>('/api/v1/admin/cabinet')
    services = view.services
    const rows = view.people.map(row).join('')
    body.innerHTML = `<table>
      <thead>${head()}</thead>
      <tbody>${rows || `<tr><td colspan="${services.length + 2}">Пока никого</td></tr>`}</tbody>
    </table>${addForm()}`
    wire()
  }

  // По таймеру не опрашиваем: у соседей нет лимитера, а ВМ общая — ходим по действию.
  void refresh().catch(showError)
}
