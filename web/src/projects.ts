/**
 * Экран проектов: один общий список проектов команды, свежие правки сверху, на карточке автор.
 *
 * Создание живёт на экране нового проекта. Проект открывает, правит и удаляет любой: работа
 * общая. Удаление необратимо, поэтому подтверждение у чужого проекта называет автора.
 */
import { api, ApiError } from './api'
import { fmtDuration, fmtWhen } from './assets'
import { escapeHtml } from './html'
import { authorLabel, ownedBy, ownerLabel } from './overview'
import { listProjects, type ProjectCard } from './project'
import type { Me } from './shell'

/** Карточка проекта: имя, автор («вы» у своего), клипы, длительность, последняя правка. */
export function projectCardHtml(p: ProjectCard, index: number, myEmail: string): string {
  return `<a class="card project-card appear" style="--delay:${index * 40}ms"
      href="#/p/${encodeURIComponent(p.id)}">
      <span class="display-m project-title">${escapeHtml(p.name)}</span>
      <span class="meta">${escapeHtml(authorLabel(p, myEmail))} · ${p.clips_count} кл. · ${fmtDuration(p.duration)} · ${fmtWhen(p.updated_at)}</span>
      <span class="row">
        <button class="btn btn-ghost" data-drop="${escapeHtml(p.id)}">Удалить</button>
      </span>
    </a>`
}

/** Вопрос перед удалением. Удаление необратимо: у чужого проекта называем автора. */
export function dropProjectQuestion(p: ProjectCard, myEmail: string): string {
  const whose = ownedBy(p, myEmail) ? '' : ` (автор — ${ownerLabel(p)})`
  return (
    `Удалить проект «${p.name}»${whose}? Удаление необратимо: пропадут его точки сохранения ` +
    'и готовые ролики. Записи освободятся и уйдут по сроку хранения.'
  )
}

export function mountProjects(el: HTMLElement, me: Me) {
  el.innerHTML = `
    <div class="screen stack">
      <div class="row space-between">
        <h1 class="display-l" style="margin:0">Проекты</h1>
        <a class="btn btn-key" href="#/new">Новый</a>
      </div>
      <pre id="prj-error" hidden></pre>
      <div id="prj-list" class="tiles"></div>
    </div>`
  const list = el.querySelector('#prj-list') as HTMLElement
  // Ошибка — над списком: под длинным общим списком её не видно.
  const errorBox = el.querySelector('#prj-error') as HTMLPreElement
  let stopped = false
  let shown: ProjectCard[] = []

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  async function refresh(): Promise<void> {
    if (stopped) return
    const { projects } = await listProjects()
    if (stopped) return
    shown = projects
    // Порядок задаёт сервер: свежие правки выше.
    list.innerHTML = projects.length
      ? projects.map((p, i) => projectCardHtml(p, i, me.email)).join('')
      : '<p class="lead" style="margin:0">Проектов пока нет. Начните с записи</p>'
    list.querySelectorAll<HTMLButtonElement>('button[data-drop]').forEach(wireDrop)
  }

  function wireDrop(button: HTMLButtonElement): void {
    button.addEventListener('click', async event => {
      // Карточка целиком — ссылка в редактор: без этого кнопка внутри неё уводила бы со страницы.
      event.preventDefault()
      event.stopPropagation()
      const p = shown.find(x => x.id === button.dataset.drop)
      if (!p || !window.confirm(dropProjectQuestion(p, me.email))) return
      button.disabled = true
      try {
        await api(`/api/v1/projects/${encodeURIComponent(p.id)}`, { method: 'DELETE' })
        errorBox.hidden = true
        await refresh()
      } catch (e) {
        button.disabled = false
        showError(e)
      }
    })
  }

  void refresh().catch(showError)

  return {
    stop(): void {
      stopped = true
    },
  }
}
