/**
 * Работа команды: чужие проекты и чужие записи.
 *
 * Показываются там, где их ищут: у админа под своими проектами на экране «Проекты» и под своими
 * записями на экране «Записи». Раньше всё жило в «Кабинете доступа», и чужой проект, лежащий на
 * сервере, админ не находил: на экране проектов его не было. В кабинете остался итог по людям —
 * он отвечает на вопрос «чем занят диск», и ему место рядом с доступами.
 *
 * Чужой проект открывается в редакторе как свой. Работает редактор при этом от имени владельца:
 * файлы лежат в его каталоге, и правка уходит ему же, а не заводит копию у админа.
 */
import { api, ApiError } from './api'
import { fmtSize } from './assets'
import { escapeHtml } from './html'

export type TeamProject = {
  id: string
  name: string
  owner_email: string
  owner_name: string
  clips_count: number
  duration: number
  updated_at: string
}

export type TeamAsset = {
  id: string
  original_name: string
  owner_email: string
  owner_name: string
  kind: string
  status: string
  size: number
  duration: number | null
  created_at: string
}

/** Место человека на диске — по файлам в его папке, как считает его лимит (GET /admin/usage). */
export type PersonUse = { email: string; name: string; bytes: number; records: number }

export function loadUsage(): Promise<PersonUse[]> {
  return api<{ people: PersonUse[] }>('/api/v1/admin/usage').then(body => body.people)
}

/** Строки кабинета. Порядок — тяжёлые сверху — уже задал сервер. */
export function usageHtml(rows: PersonUse[]): string {
  if (!rows.length) return '<span class="muted">На диске пока ничего нет</span>'
  return rows
    .map(
      row =>
        `<div class="row" style="margin:0;justify-content:space-between">
          <span>${escapeHtml(row.name.trim() || row.email)}</span>
          <span class="meta">${fmtSize(row.bytes)} · записей: ${row.records}</span>
        </div>`,
    )
    .join('')
}

/** Имя человека, а если его не назвали — почта. Пустая строка в списке ничего не значит. */
export function ownerLabel(owner: { owner_email: string; owner_name: string }): string {
  return owner.owner_name.trim() || owner.owner_email
}

/** Моё ли это. Почту сравниваем без регистра и пробелов: так же её хранит сервер. */
export function ownedBy(item: { owner_email: string }, myEmail: string): boolean {
  return item.owner_email.trim().toLowerCase() === myEmail.trim().toLowerCase()
}

/**
 * Только чужое. Своё человек и так видит в своём списке сразу над этим, а одно и то же дважды
 * подряд — шум.
 */
export function othersOnly<T extends { owner_email: string }>(items: T[], myEmail: string): T[] {
  return items.filter(item => !ownedBy(item, myEmail))
}

export function loadTeamProjects(): Promise<TeamProject[]> {
  return api<{ projects: TeamProject[] }>('/api/v1/admin/projects').then(body => body.projects)
}

export function loadTeamAssets(): Promise<TeamAsset[]> {
  return api<{ assets: TeamAsset[] }>('/api/v1/admin/assets').then(body => body.assets)
}

/** Итог по людям для кабинета: кто сколько занимает на диске — по файлам, как считает лимит.
 * Сами списки — на экранах проектов и записей. */
export function mountOverview(el: HTMLElement) {
  el.innerHTML = `
    <section class="card stack">
      <h2 class="display-m" style="margin:0">Работа команды</h2>
      <p class="meta" style="margin:0">Чужие проекты — на экране <a href="#/projects">«Проекты»</a>,
        чужие записи — на экране <a href="#/files">«Записи»</a>, под своими.</p>
      <div id="ov-use" class="stack" style="--stack-gap:4px"><span class="muted">Загружаю…</span></div>
      <pre id="ov-error" hidden></pre>
    </section>`

  const useBox = el.querySelector('#ov-use') as HTMLElement
  const errorBox = el.querySelector('#ov-error') as HTMLPreElement
  let stopped = false

  void loadUsage()
    .then(rows => {
      if (!stopped) useBox.innerHTML = usageHtml(rows)
    })
    .catch(e => {
      if (stopped) return
      errorBox.hidden = false
      errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
    })

  return {
    stop(): void {
      stopped = true
    },
  }
}
