/** Экран проектов: карточки, завершение, удаление. Создание живёт на экране нового проекта. */
import { api, ApiError } from './api'
import { fmtDuration, fmtWhen } from './assets'
import { escapeHtml } from './html'
import { listProjects, type ProjectCard } from './project'

function card(p: ProjectCard, index: number): string {
  const draft = p.status === 'draft'
  const state = draft ? 'в работе' : 'завершён'
  // Завершённый проект — история: открывать в редакторе нечего, его файлы уже удалены.
  const open = draft
    ? `<a class="card project-card appear" style="--delay:${index * 40}ms" href="#/p/${encodeURIComponent(p.id)}">`
    : `<div class="card project-card appear" style="--delay:${index * 40}ms">`
  const close = draft ? '</a>' : '</div>'
  return `${open}
      <span class="display-m project-title">${escapeHtml(p.name)}</span>
      <span class="meta">${p.clips_count} кл. · ${fmtDuration(p.duration)} · ${fmtWhen(p.updated_at)}</span>
      <span class="row">
        <span class="pill">${state}</span>
        ${draft ? `<button class="btn btn-ghost" data-finish="${escapeHtml(p.id)}" data-name="${escapeHtml(p.name)}">Завершить</button>` : ''}
        <button class="btn btn-ghost" data-drop="${escapeHtml(p.id)}" data-name="${escapeHtml(p.name)}">Удалить</button>
      </span>
    ${close}`
}

export function mountProjects(el: HTMLElement) {
  el.innerHTML = `
    <div class="screen stack">
      <div class="row space-between">
        <h1 class="display-l" style="margin:0">Проекты</h1>
        <a class="btn btn-key" href="#/new">Новый</a>
      </div>
      <div id="prj-list" class="tiles"></div>
      <pre id="prj-error" hidden></pre>
    </div>`
  const list = el.querySelector('#prj-list') as HTMLElement
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
    // Черновики выше завершённых: работают с ними, а не с историей.
    const sorted = [...projects].sort((a, b) => Number(b.status === 'draft') - Number(a.status === 'draft'))
    list.innerHTML = sorted.length
      ? sorted.map(card).join('')
      : '<p class="lead" style="margin:0">Проектов пока нет. Начните с записи</p>'
    wire()
  }

  function act(button: HTMLButtonElement, ask: string, path: string, method: string): void {
    button.addEventListener('click', async event => {
      // Карточка целиком — ссылка в редактор: без этого кнопка внутри неё уводила бы со страницы.
      event.preventDefault()
      event.stopPropagation()
      if (!window.confirm(ask)) return
      button.disabled = true
      try {
        await api(path, { method })
        await refresh()
      } catch (e) {
        button.disabled = false
        showError(e)
      }
    })
  }

  function wire(): void {
    list.querySelectorAll<HTMLButtonElement>('button[data-drop]').forEach(b =>
      act(
        b,
        `Удалить проект «${b.dataset.name}»? Записи останутся.`,
        `/api/v1/projects/${encodeURIComponent(b.dataset.drop ?? '')}`,
        'DELETE',
      ),
    )
    list.querySelectorAll<HTMLButtonElement>('button[data-finish]').forEach(b =>
      act(
        b,
        `Завершить проект «${b.dataset.name}»? Его записи удалятся, если не заняты в других проектах.`,
        `/api/v1/projects/${encodeURIComponent(b.dataset.finish ?? '')}/finish`,
        'POST',
      ),
    )
  }

  void refresh().catch(showError)

  return {
    stop(): void {
      stopped = true
    },
  }
}
