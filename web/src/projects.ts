/**
 * Экран проектов: свои карточки, а у админа под ними — проекты команды.
 *
 * Создание живёт на экране нового проекта. Проекты команды стоят здесь, а не в «Кабинете
 * доступа»: чужой проект ищут там же, где свой, и в кабинете админ его не находил.
 */
import { api, ApiError } from './api'
import { fmtDuration, fmtWhen } from './assets'
import { escapeHtml } from './html'
import { loadTeamProjects, othersOnly, ownerLabel, type TeamProject } from './overview'
import { listProjects, type ProjectCard } from './project'
import type { Me } from './shell'

function card(p: ProjectCard, index: number): string {
  return `<a class="card project-card appear" style="--delay:${index * 40}ms"
      href="#/p/${encodeURIComponent(p.id)}">
      <span class="display-m project-title">${escapeHtml(p.name)}</span>
      <span class="meta">${p.clips_count} кл. · ${fmtDuration(p.duration)} · ${fmtWhen(p.updated_at)}</span>
      <span class="row">
        <button class="btn btn-ghost" data-drop="${escapeHtml(p.id)}" data-name="${escapeHtml(p.name)}">Удалить</button>
      </span>
    </a>`
}

/** Чужая карточка: та же, что своя, плюс чья. Без владельца два «Ролика для сайта» не различить. */
function teamCard(p: TeamProject, index: number): string {
  const owner = ownerLabel(p)
  return `<a class="card project-card appear" style="--delay:${index * 40}ms"
      href="#/p/${encodeURIComponent(p.id)}">
      <span class="display-m project-title">${escapeHtml(p.name)}</span>
      <span class="meta">${escapeHtml(owner)} · ${p.clips_count} кл. · ${fmtDuration(p.duration)} · ${fmtWhen(p.updated_at)}</span>
      <span class="row">
        <button class="btn btn-ghost" data-drop="${escapeHtml(p.id)}" data-name="${escapeHtml(p.name)}"
          data-owner="${escapeHtml(owner)}">Удалить</button>
      </span>
    </a>`
}

export function mountProjects(el: HTMLElement, me?: Me) {
  const admin = me?.role === 'admin'
  el.innerHTML = `
    <div class="screen stack">
      <div class="row space-between">
        <h1 class="display-l" style="margin:0">Проекты</h1>
        <a class="btn btn-key" href="#/new">Новый</a>
      </div>
      <div id="prj-list" class="tiles"></div>
      ${
        admin
          ? `<h2 class="display-m" style="margin:24px 0 0">Проекты команды</h2>
      <p class="meta" style="margin:0">Открываются как свои, но правки уходят владельцу, а не копией вам.</p>
      <div id="prj-team" class="tiles"><p class="lead" style="margin:0">Загружаю…</p></div>`
          : ''
      }
      <pre id="prj-error" hidden></pre>
    </div>`
  const list = el.querySelector('#prj-list') as HTMLElement
  const team = el.querySelector('#prj-team') as HTMLElement | null
  const errorBox = el.querySelector('#prj-error') as HTMLPreElement
  let stopped = false

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  async function refresh(): Promise<void> {
    if (stopped) return
    const { projects } = await listProjects()
    if (stopped) return
    // Порядок задаёт сервер: свежие правки выше. Своей сортировки здесь больше нет.
    list.innerHTML = projects.length
      ? projects.map(card).join('')
      : '<p class="lead" style="margin:0">Проектов пока нет. Начните с записи</p>'
    list.querySelectorAll<HTMLButtonElement>('button[data-drop]').forEach(b =>
      drop(b, `Удалить проект «${b.dataset.name}»? Его записи освободятся и уйдут по сроку хранения.`),
    )
  }

  async function refreshTeam(): Promise<void> {
    if (stopped || !team || !me) return
    const projects = othersOnly(await loadTeamProjects(), me.email)
    if (stopped) return
    team.innerHTML = projects.length
      ? projects.map(teamCard).join('')
      : '<p class="lead" style="margin:0">У остальных проектов пока нет</p>'
    team.querySelectorAll<HTMLButtonElement>('button[data-drop]').forEach(b =>
      drop(
        b,
        `Удалить чужой проект «${b.dataset.name}» (${b.dataset.owner})? ` +
          'Его записи освободятся и уйдут по сроку хранения.',
      ),
    )
  }

  function drop(button: HTMLButtonElement, ask: string): void {
    button.addEventListener('click', async event => {
      // Карточка целиком — ссылка в редактор: без этого кнопка внутри неё уводила бы со страницы.
      event.preventDefault()
      event.stopPropagation()
      if (!window.confirm(ask)) return
      button.disabled = true
      try {
        await api(`/api/v1/projects/${encodeURIComponent(button.dataset.drop ?? '')}`, { method: 'DELETE' })
        await Promise.all([refresh(), refreshTeam()])
      } catch (e) {
        button.disabled = false
        showError(e)
      }
    })
  }

  void refresh().catch(showError)
  void refreshTeam().catch(showError)

  return {
    stop(): void {
      stopped = true
    },
  }
}
