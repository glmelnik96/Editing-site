/**
 * Настройки: токены для агента.
 *
 * Секрет показывается один раз и нигде не хранится — на сервере лежит только его хеш. Поэтому
 * перерисовка списка после выпуска несёт показанный секрет с собой, иначе он исчез бы с экрана
 * раньше, чем человек успел его скопировать.
 */
import { api, ApiError } from './api'
import { fmtWhen } from './assets'
import { escapeHtml } from './html'

type Token = {
  id: string
  name: string
  created_at: string
  last_used_at: string | null
  expires_at: string | null
}

export function mountSettings(el: HTMLElement) {
  let stopped = false

  const showError = (e: unknown) => {
    const box = el.querySelector<HTMLPreElement>('#st-error')
    if (!box) return
    box.hidden = false
    box.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  function row(t: Token): string {
    return `<article class="card row token-card">
      <span class="stack token-name">
        <span>${escapeHtml(t.name)}</span>
        <span class="meta">выпущен ${fmtWhen(t.created_at)} · использован ${fmtWhen(t.last_used_at)}
          · истекает ${fmtWhen(t.expires_at)}</span>
      </span>
      <button class="btn btn-ghost" data-revoke="${escapeHtml(t.id)}"
        data-name="${escapeHtml(t.name)}">Отозвать</button>
    </article>`
  }

  async function draw(secret = ''): Promise<void> {
    if (stopped) return
    const { tokens } = await api<{ tokens: Token[] }>('/api/v1/tokens')
    if (stopped) return
    el.innerHTML = `
      <div class="screen stack">
        <h1 class="display-l" style="margin:0">Настройки</h1>
        <div class="stack">
          <h2 class="display-m" style="margin:0">Токены для агента</h2>
          <p class="lead" style="margin:0">Токен даёт доступ ко всем вашим записям и проектам через
            API. Выдавайте его программе, а не человеку</p>
        </div>
        ${secret ? `<div class="card stack secret"><span class="small">Скопируйте сейчас: второй раз
          секрет не покажется</span><code class="mono">${escapeHtml(secret)}</code></div>` : ''}
        <form id="st-form" class="row">
          <input name="name" class="field" placeholder="Для чего этот токен" required maxlength="100" />
          <button class="btn btn-key">Выпустить</button>
        </form>
        <div class="stack">${tokens.map(row).join('') ||
          '<p class="lead" style="margin:0">Токенов пока нет</p>'}</div>
        <pre id="st-error" hidden></pre>
      </div>`
    wire()
  }

  function wire(): void {
    const form = el.querySelector('#st-form') as HTMLFormElement
    form.addEventListener('submit', async event => {
      event.preventDefault()
      const name = String(new FormData(form).get('name') ?? '').trim()
      try {
        const created = await api<Token & { secret: string }>('/api/v1/tokens', {
          method: 'POST',
          body: JSON.stringify({ name }),
        })
        await draw(created.secret)
      } catch (e) {
        showError(e)
      }
    })

    el.querySelectorAll<HTMLButtonElement>('button[data-revoke]').forEach(b =>
      b.addEventListener('click', async () => {
        if (!window.confirm(`Отозвать токен «${b.dataset.name}»? Программа, которая им пользуется, потеряет доступ.`)) {
          return
        }
        b.disabled = true
        try {
          await api(`/api/v1/tokens/${encodeURIComponent(b.dataset.revoke ?? '')}`, { method: 'DELETE' })
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
