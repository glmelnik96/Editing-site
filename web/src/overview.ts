/**
 * Подписи автора и итог места по людям.
 *
 * Проекты и записи общие: их видит вся команда, и на карточке стоит автор — «вы» у своего. Итог
 * «кто сколько занимает на диске» остался в «Кабинете доступа»: он отвечает на вопрос, чем занят
 * диск, и ему место рядом с доступами.
 */
import { api, ApiError } from './api'
import { fmtSize } from './assets'
import { escapeHtml } from './html'

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

/** Подпись автора на карточке: у своего «вы», у чужого — имя, а без имени почта. */
export function authorLabel(item: { owner_email: string; owner_name: string }, myEmail: string): string {
  return ownedBy(item, myEmail) ? 'вы' : ownerLabel(item)
}

/** Итог по людям для кабинета: кто сколько занимает на диске — по файлам, как считает лимит.
 * Сами списки — на экранах проектов и записей. */
export function mountOverview(el: HTMLElement) {
  el.innerHTML = `
    <section class="card stack">
      <h2 class="display-m" style="margin:0">Работа команды</h2>
      <p class="meta" style="margin:0">Проекты и записи всей команды — на экранах <a href="#/projects">«Проекты»</a>
        и <a href="#/files">«Записи»</a>; здесь — кто сколько занимает на диске.</p>
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
