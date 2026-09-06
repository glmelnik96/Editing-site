/**
 * Сборка приложения: оболочка, маршрут, экран.
 *
 * Здесь только это. Каждый экран — свой модуль с `mount…(el)`; предыдущий останавливается
 * своим `stop()` перед тем, как контейнер перепишут, иначе его опросы и плеер продолжат жить
 * в разобранной разметке.
 */
import './style.css'
import { api, ApiError } from './api'
import { mountDoor } from './door'
import { mountEditor } from './editor'
import { escapeHtml } from './html'
import { mountHome } from './home'
import { parseRoute, type Route } from './router'
import { mountShell, type Me } from './shell'

const root = document.getElementById('app') as HTMLElement
const shell = mountShell(root)

let current: { stop?: () => void } | null = null
// Номер перехода: пока летит запрос /me, человек мог уйти на другой адрес — ответ старого
// перехода не должен рисовать свой экран поверх нового.
let pass = 0

const STUB_TITLE: Record<'files' | 'new' | 'projects' | 'settings' | 'admin', string> = {
  files: 'Записи',
  new: 'Новый проект',
  projects: 'Проекты',
  settings: 'Настройки',
  admin: 'Кабинет доступа',
}

function screenText(title: string, lead: string): void {
  shell.screen.innerHTML = `
    <div class="screen stack">
      <h1 class="display-l" style="margin:0">${escapeHtml(title)}</h1>
      <p class="lead" style="margin:0">${escapeHtml(lead)}</p>
    </div>`
}

function show(route: Route, me: Me): void {
  switch (route.name) {
    case 'home':
      current = mountHome(shell.screen, me)
      return
    case 'editor':
      current = mountEditor(shell.screen, route.projectId)
      return
    default:
      // Записи, новый проект, список проектов, настройки и кабинет доступа делает следующая
      // задача — она же убирает заглушку.
      screenText(STUB_TITLE[route.name], 'Скоро')
  }
}

async function route(): Promise<void> {
  const mine = ++pass
  current?.stop?.()
  current = null
  const wanted = parseRoute(location.hash)

  let me: Me
  try {
    me = await api<Me>('/api/v1/me')
  } catch (e) {
    if (mine !== pass) return
    if (e instanceof ApiError && e.status === 401) {
      shell.clearUser()
      mountDoor(shell.screen)
    } else {
      screenText('Не открылось', e instanceof Error ? e.message : String(e))
    }
    return
  }
  if (mine !== pass) return

  shell.setUser(me)
  show(wanted, me)
}

window.addEventListener('hashchange', () => void route())
void route()

/* ═══ Наследство прежней страницы ════════════════════════════════════════════
 *
 * Токены агента и список разрешённых адресов жили на одной длинной странице вместе с файлами
 * и проектами. Экранов #/settings и #/admin ещё нет — их делает следующая задача, она же
 * забирает эти две функции отсюда в свои модули. Пока никто их не зовёт: переписывать рабочий
 * код заново через задачу дороже, чем подержать его здесь.
 *
 * Квоту в шапке обновлять больше нечем: у оболочки для этого есть setUser со свежим /me.
 */

type Token = { id: string; name: string; created_at: string; last_used_at: string | null; expires_at: string | null }
type WhitelistEntry = { email: string; added_by: string | null; added_at: string }

function fmt(ts: string | null): string {
  return ts ? ts.replace('T', ' ').slice(0, 16) : '—'
}

function showError(box: HTMLElement | null, e: unknown): void {
  if (!box) return
  box.hidden = false
  box.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
}

/** Токены для агента: список, выпуск, отзыв. Секрет показывается один раз. */
export async function renderTokens(el: HTMLElement, secretNote = ''): Promise<void> {
  const { tokens } = await api<{ tokens: Token[] }>('/api/v1/tokens')
  const rows = tokens
    .map(
      t => `<tr><td>${escapeHtml(t.name)}</td><td>${fmt(t.created_at)}</td><td>${fmt(t.last_used_at)}</td>
        <td>${fmt(t.expires_at)}</td><td><button data-revoke="${escapeHtml(t.id)}">Отозвать</button></td></tr>`,
    )
    .join('')
  el.innerHTML = `
    <main class="card">
      <h2>Токены для агента</h2>
      <table>
        <thead><tr><th>Имя</th><th>Создан</th><th>Использован</th><th>Истекает</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5">Пока нет</td></tr>'}</tbody>
      </table>
      <form id="token-form"><input name="name" placeholder="Имя токена" required maxlength="100" /><button>Выпустить</button></form>
      <pre id="secret" hidden></pre>
      <pre id="tokens-error" hidden></pre>
    </main>`
  const errorBox = el.querySelector<HTMLPreElement>('#tokens-error')

  if (secretNote) {
    const box = el.querySelector('#secret') as HTMLPreElement
    box.hidden = false
    box.textContent = secretNote
  }

  const form = el.querySelector('#token-form') as HTMLFormElement
  form.addEventListener('submit', async ev => {
    ev.preventDefault()
    const name = String(new FormData(form).get('name') ?? '').trim()
    try {
      const created = await api<Token & { secret: string }>('/api/v1/tokens', {
        method: 'POST',
        body: JSON.stringify({ name }),
      })
      // Перерисовка съест показанный секрет, поэтому он едет в неё же и появляется снова.
      await renderTokens(el, `Токен «${created.name}» показывается один раз:\n${created.secret}`)
    } catch (e) {
      showError(errorBox, e)
    }
  })

  el.querySelectorAll<HTMLButtonElement>('button[data-revoke]').forEach(b =>
    b.addEventListener('click', async () => {
      try {
        await api(`/api/v1/tokens/${b.dataset.revoke}`, { method: 'DELETE' })
        await renderTokens(el)
      } catch (e) {
        showError(errorBox, e)
      }
    }),
  )
}

/** Разрешённые адреса: кого пускает вход. Отдельно от кабинета доступа соседей. */
export async function renderWhitelist(el: HTMLElement): Promise<void> {
  const { emails } = await api<{ emails: WhitelistEntry[] }>('/api/v1/admin/whitelist')
  const items = emails
    .map(e => `<li>${escapeHtml(e.email)} <button data-remove="${escapeHtml(e.email)}">Убрать</button></li>`)
    .join('')
  el.innerHTML = `
    <main class="card">
      <h2>Разрешённые адреса</h2>
      <ul>${items || '<li>Пока никого</li>'}</ul>
      <form id="wl-form"><input name="email" type="email" placeholder="user@yandex.ru" required /><button>Добавить</button></form>
      <pre id="admin-error" hidden></pre>
    </main>`
  const errorBox = el.querySelector<HTMLPreElement>('#admin-error')

  const form = el.querySelector('#wl-form') as HTMLFormElement
  form.addEventListener('submit', async ev => {
    ev.preventDefault()
    const email = String(new FormData(form).get('email') ?? '').trim()
    try {
      await api('/api/v1/admin/whitelist', { method: 'POST', body: JSON.stringify({ email }) })
      await renderWhitelist(el)
    } catch (e) {
      showError(errorBox, e)
    }
  })

  el.querySelectorAll<HTMLButtonElement>('button[data-remove]').forEach(b =>
    b.addEventListener('click', async () => {
      try {
        await api(`/api/v1/admin/whitelist/${encodeURIComponent(b.dataset.remove ?? '')}`, { method: 'DELETE' })
        await renderWhitelist(el)
      } catch (e) {
        showError(errorBox, e)
      }
    }),
  )
}
