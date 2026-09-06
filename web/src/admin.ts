/**
 * Экран администратора: кабинет доступа к трём сервисам ВМ и наш собственный белый список.
 *
 * Кабинет и белый список — разные вещи, и держать их на одном экране правильно: кабинет ведёт
 * допуск сразу в три сервиса, а белый список отвечает на вопрос «кого вообще пускает вход сюда».
 */
import { api, ApiError } from './api'
import { mountCabinet } from './cabinet'
import { fmtWhen } from './assets'
import { escapeHtml } from './html'

type WhitelistEntry = { email: string; added_by: string | null; added_at: string }

export function mountAdmin(el: HTMLElement) {
  el.innerHTML = `
    <div class="screen stack">
      <h1 class="display-l" style="margin:0">Кабинет доступа</h1>
      <div id="ad-cabinet"></div>
      <div id="ad-whitelist" class="stack"></div>
    </div>`

  const whitelist = el.querySelector('#ad-whitelist') as HTMLElement
  let stopped = false

  mountCabinet(el.querySelector('#ad-cabinet') as HTMLElement)

  const showError = (e: unknown) => {
    const box = whitelist.querySelector<HTMLPreElement>('#wl-error')
    if (!box) return
    box.hidden = false
    box.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  async function draw(): Promise<void> {
    if (stopped) return
    const { emails } = await api<{ emails: WhitelistEntry[] }>('/api/v1/admin/whitelist')
    if (stopped) return
    const rows = emails
      .map(
        e => `<article class="card row token-card">
          <span class="stack token-name">
            <span>${escapeHtml(e.email)}</span>
            <span class="meta">добавил ${escapeHtml(e.added_by ?? 'при установке')} · ${fmtWhen(e.added_at)}</span>
          </span>
          <button class="btn btn-ghost" data-remove="${escapeHtml(e.email)}">Убрать</button>
        </article>`,
      )
      .join('')
    whitelist.innerHTML = `
      <div class="stack">
        <h2 class="display-m" style="margin:0">Кого пускает вход сюда</h2>
        <p class="lead" style="margin:0">Список нашего сервиса. Доступ к соседям ведётся кабинетом выше</p>
      </div>
      <form id="wl-form" class="row">
        <input name="email" type="email" class="field" placeholder="user@yandex.ru" required />
        <button class="btn btn-key">Добавить</button>
      </form>
      <div class="stack">${rows || '<p class="lead" style="margin:0">Пока никого</p>'}</div>
      <pre id="wl-error" hidden></pre>`
    wire()
  }

  function wire(): void {
    const form = whitelist.querySelector('#wl-form') as HTMLFormElement
    form.addEventListener('submit', async event => {
      event.preventDefault()
      const email = String(new FormData(form).get('email') ?? '').trim()
      try {
        await api('/api/v1/admin/whitelist', { method: 'POST', body: JSON.stringify({ email }) })
        await draw()
      } catch (e) {
        showError(e)
      }
    })

    whitelist.querySelectorAll<HTMLButtonElement>('button[data-remove]').forEach(b =>
      b.addEventListener('click', async () => {
        const email = b.dataset.remove ?? ''
        // Тот же текст, что и в кабинете: снятие доступа обрывает живые сессии сразу.
        if (!window.confirm(`Убрать ${email}? Доступ снимется сразу: открытые сессии оборвутся.`)) return
        b.disabled = true
        try {
          await api(`/api/v1/admin/whitelist/${encodeURIComponent(email)}`, { method: 'DELETE' })
          await draw()
        } catch (e) {
          b.disabled = false
          showError(e)
        }
      }),
    )
  }

  void draw().catch(showError)

  return {
    stop(): void {
      stopped = true
    },
  }
}
