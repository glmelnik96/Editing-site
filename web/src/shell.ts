/**
 * Оболочка сайта: шапка и контейнер экрана.
 *
 * Шапка живёт дольше экранов — она рисуется один раз и меняется только тогда, когда меняется
 * человек (вошёл, вышел, обновилась квота). Экраны перерисовывают лишь свой контейнер, поэтому
 * при переходе имя сервиса и ссылки не мигают.
 */
import { api } from './api'
import { fmtSize } from './assets'
import { escapeHtml } from './html'

export type Me = {
  id: string
  email: string
  name: string
  role: 'admin' | 'user'
  auth: 'cookie' | 'token'
  quota: { used_bytes: number; limit_bytes: number }
}

export type Shell = {
  /** Контейнер экрана: единственное место, которое переписывают модули экранов. */
  screen: HTMLElement
  setUser: (me: Me) => void
  clearUser: () => void
}

export function mountShell(root: HTMLElement): Shell {
  root.innerHTML = `
    <header class="bar">
      <a href="#/" style="font-family:var(--font-display);font-weight:300;font-size:20px;color:var(--paper);text-decoration:none">Editing site</a>
      <span></span>
      <nav class="row" id="shell-nav" style="margin:0;--row-gap:16px"></nav>
    </header>
    <div id="shell-screen"></div>`

  const nav = root.querySelector('#shell-nav') as HTMLElement
  const screen = root.querySelector('#shell-screen') as HTMLElement

  function setUser(me: Me): void {
    const admin = me.role === 'admin' ? '<a href="#/admin">Кабинет доступа</a>' : ''
    nav.innerHTML = `
      <div class="meta" title="${escapeHtml(me.email)}">${fmtSize(me.quota.used_bytes)} из ${fmtSize(me.quota.limit_bytes)}</div>
      <a href="#/settings">Настройки</a>
      ${admin}
      <button type="button" class="btn btn-ghost" id="shell-logout">Выйти</button>
      <div class="meta error" id="shell-logout-error" hidden></div>`

    const logout = nav.querySelector('#shell-logout') as HTMLButtonElement
    const errorBox = nav.querySelector('#shell-logout-error') as HTMLElement
    logout.addEventListener('click', async () => {
      logout.disabled = true
      errorBox.hidden = true
      try {
        await api('/api/v1/auth/logout', { method: 'POST' })
      } catch (e) {
        logout.disabled = false
        errorBox.hidden = false
        errorBox.textContent = e instanceof Error ? e.message : String(e)
        return
      }
      // Не перерисовка, а перезагрузка: после выхода в памяти не должно остаться ни документа
      // проекта, ни опросов статусов. Новая загрузка спросит /me, получит 401 и покажет дверь.
      location.hash = '#/'
      location.reload()
    })
  }

  function clearUser(): void {
    nav.innerHTML = ''
  }

  return { screen, setUser, clearUser }
}
