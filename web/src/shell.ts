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
import { mountWork, type WorkControls } from './work'

export type Me = {
  id: string
  email: string
  name: string
  role: 'admin' | 'user'
  auth: 'cookie' | 'token'
  quota: { used_bytes: number; limit_bytes: number }
  /** Место на сервере — одно на всех: файлы сервиса на диске и сколько ещё свободно. */
  server: { files_bytes: number; free_bytes: number }
}

export type Shell = {
  /** Контейнер экрана: единственное место, которое переписывают модули экранов. */
  screen: HTMLElement
  setUser: (me: Me) => void
  clearUser: () => void
  work: WorkControls
}

/**
 * Шапка вошедшего: место на сервере, разделы, настройки, кабинет админа, выход. «Проекты» и «Записи»
 * стоят всегда: на главной «Все проекты» живёт в «Недавнем», и без своих проектов на экран
 * «Проекты» было не попасть — а у админа там ещё и проекты команды. Место — на всём сервере и
 * у всех одно; свой расход и лимит — в подсказке.
 */
export function navHtml(me: Me): string {
  const admin = me.role === 'admin' ? '<a href="#/admin">Кабинет доступа</a>' : ''
  const tip = `${escapeHtml(me.email)}&#10;Ваши файлы: ${fmtSize(me.quota.used_bytes)} из ${fmtSize(me.quota.limit_bytes)}`
  return `
      <div class="meta" title="${tip}">${fmtSize(me.server.files_bytes)} · свободно ${fmtSize(me.server.free_bytes)}</div>
      <a href="#/projects">Проекты</a>
      <a href="#/files">Записи</a>
      <a href="#/settings">Настройки</a>
      ${admin}
      <button type="button" class="btn btn-ghost" id="shell-logout">Выйти</button>
      <div class="meta error" id="shell-logout-error" hidden></div>`
}

export function mountShell(root: HTMLElement): Shell {
  root.innerHTML = `
    <header class="bar">
      <a href="#/" class="bar-logo">Editing site</a>
      <span></span>
      <nav class="row" id="shell-nav" style="margin:0;--row-gap:16px"></nav>
    </header>
    <div id="shell-work" hidden></div>
    <div id="shell-screen"></div>`

  const nav = root.querySelector('#shell-nav') as HTMLElement
  const screen = root.querySelector('#shell-screen') as HTMLElement
  const work = mountWork(root.querySelector('#shell-work') as HTMLElement)

  function setUser(me: Me): void {
    nav.innerHTML = navHtml(me)

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
    work.start(me.email)
  }

  function clearUser(): void {
    nav.innerHTML = ''
    work.stop()
  }

  return { screen, setUser, clearUser, work }
}
