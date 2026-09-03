import './style.css'
import { api, ApiError } from './api'
import { escapeHtml } from './html'

type Me = { id: string; email: string; name: string; role: 'admin' | 'user'; auth: 'cookie' | 'token' }
type Token = { id: string; name: string; created_at: string; last_used_at: string | null; expires_at: string | null }
type WhitelistEntry = { email: string; added_by: string | null; added_at: string }

const root = document.getElementById('app') as HTMLElement

function fmt(ts: string | null): string {
  return ts ? ts.replace('T', ' ').slice(0, 16) : '—'
}

async function boot(): Promise<void> {
  try {
    const me = await api<Me>('/api/v1/me')
    await renderSettings(me)
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) renderLogin()
    else renderError(e)
  }
}

function renderLogin(): void {
  root.innerHTML = `
    <main class="card">
      <h1>Editing site</h1>
      <p>Вход только для адресов из списка.</p>
      <a class="button" href="/api/v1/auth/login">Войти через Яндекс</a>
    </main>`
}

function renderError(e: unknown): void {
  const msg = e instanceof Error ? e.message : String(e)
  root.innerHTML = `<main class="card"><h1>Ошибка</h1><p>${escapeHtml(msg)}</p></main>`
}

async function renderSettings(me: Me): Promise<void> {
  const { tokens } = await api<{ tokens: Token[] }>('/api/v1/tokens')
  const rows = tokens
    .map(
      t => `<tr><td>${escapeHtml(t.name)}</td><td>${fmt(t.created_at)}</td><td>${fmt(t.last_used_at)}</td>
        <td>${fmt(t.expires_at)}</td><td><button data-revoke="${t.id}">Отозвать</button></td></tr>`,
    )
    .join('')
  root.innerHTML = `
    <header class="bar"><strong>Editing site</strong><span>${escapeHtml(me.email)}</span><button id="logout">Выйти</button></header>
    <main class="card">
      <h2>Токены для агента</h2>
      <table>
        <thead><tr><th>Имя</th><th>Создан</th><th>Использован</th><th>Истекает</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5">Пока нет</td></tr>'}</tbody>
      </table>
      <form id="token-form"><input name="name" placeholder="Имя токена" required maxlength="100" /><button>Выпустить</button></form>
      <pre id="secret" hidden></pre>
    </main>
    <section id="admin"></section>`

  document.getElementById('logout')!.addEventListener('click', async () => {
    await api('/api/v1/auth/logout', { method: 'POST' })
    await boot()
  })

  const form = document.getElementById('token-form') as HTMLFormElement
  form.addEventListener('submit', async ev => {
    ev.preventDefault()
    const name = String(new FormData(form).get('name') ?? '').trim()
    try {
      const created = await api<Token & { secret: string }>('/api/v1/tokens', {
        method: 'POST',
        body: JSON.stringify({ name }),
      })
      await renderSettings(me)
      const box = document.getElementById('secret') as HTMLPreElement
      box.hidden = false
      box.textContent = `Токен «${created.name}» показывается один раз:\n${created.secret}`
    } catch (e) {
      renderInlineError(e)
    }
  })

  root.querySelectorAll<HTMLButtonElement>('button[data-revoke]').forEach(b =>
    b.addEventListener('click', async () => {
      await api(`/api/v1/tokens/${b.dataset.revoke}`, { method: 'DELETE' })
      await renderSettings(me)
    }),
  )

  if (me.role === 'admin') await renderAdmin()
}

function renderInlineError(e: unknown): void {
  const box = document.getElementById('secret') as HTMLPreElement | null
  if (!box) return
  box.hidden = false
  box.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
}

async function renderAdmin(): Promise<void> {
  const { emails } = await api<{ emails: WhitelistEntry[] }>('/api/v1/admin/whitelist')
  const el = document.getElementById('admin') as HTMLElement
  const items = emails
    .map(e => `<li>${escapeHtml(e.email)} <button data-remove="${escapeHtml(e.email)}">Убрать</button></li>`)
    .join('')
  el.innerHTML = `
    <main class="card">
      <h2>Разрешённые адреса</h2>
      <ul>${items || '<li>Пока никого</li>'}</ul>
      <form id="wl-form"><input name="email" type="email" placeholder="user@yandex.ru" required /><button>Добавить</button></form>
    </main>`

  const form = document.getElementById('wl-form') as HTMLFormElement
  form.addEventListener('submit', async ev => {
    ev.preventDefault()
    const email = String(new FormData(form).get('email') ?? '').trim()
    try {
      await api('/api/v1/admin/whitelist', { method: 'POST', body: JSON.stringify({ email }) })
    } catch (e) {
      renderInlineError(e)
    }
    await renderAdmin()
  })

  el.querySelectorAll<HTMLButtonElement>('button[data-remove]').forEach(b =>
    b.addEventListener('click', async () => {
      try {
        await api(`/api/v1/admin/whitelist/${encodeURIComponent(b.dataset.remove ?? '')}`, { method: 'DELETE' })
      } catch (e) {
        renderInlineError(e)
      }
      await renderAdmin()
    }),
  )
}

void boot()
