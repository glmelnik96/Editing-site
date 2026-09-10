/** Экран проектов: карточки, завершение, удаление. Создание живёт на экране нового проекта. */
import { api, ApiError } from './api'
import { fmtDuration, fmtWhen } from './assets'
import { escapeHtml } from './html'
import { listProjects, type ProjectCard } from './project'

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
    // Порядок задаёт сервер: свежие правки выше. Своей сортировки здесь больше нет.
    list.innerHTML = projects.length
      ? projects.map(card).join('')
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
        `Удалить проект «${b.dataset.name}»? Его записи освободятся и уйдут по сроку хранения.`,
        `/api/v1/projects/${encodeURIComponent(b.dataset.drop ?? '')}`,
        'DELETE',
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
