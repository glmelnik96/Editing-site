/** Список проектов на главной: создать, открыть, завершить, удалить. */
import { api, ApiError } from './api'
import { fmtDuration } from './assets'
import { escapeHtml } from './html'
import { createProject, listProjects, type ProjectCard } from './project'

function row(p: ProjectCard): string {
  const state = p.status === 'finished' ? 'завершён' : 'в работе'
  const open = p.status === 'draft'
    ? `<a class="button" href="#/p/${encodeURIComponent(p.id)}">Открыть</a>`
    : ''
  const finish = p.status === 'draft'
    ? `<button data-finish="${escapeHtml(p.id)}" data-name="${escapeHtml(p.name)}">Завершить</button>`
    : ''
  return `<tr>
    <td>${escapeHtml(p.name)}</td><td>${p.clips_count}</td><td>${fmtDuration(p.duration)}</td>
    <td>${state}</td><td>${open} ${finish}
    <button data-drop="${escapeHtml(p.id)}" data-name="${escapeHtml(p.name)}">Удалить</button></td></tr>`
}

export function mountProjects(el: HTMLElement) {
  el.innerHTML = `
    <main class="card">
      <h2>Проекты</h2>
      <form id="prj-form"><input name="name" placeholder="Название проекта" required maxlength="200" /><button>Создать</button></form>
      <table>
        <thead><tr><th>Название</th><th>Клипов</th><th>Длина</th><th>Статус</th><th></th></tr></thead>
        <tbody id="prj-rows"><tr><td colspan="5">Пока нет</td></tr></tbody>
      </table>
      <pre id="prj-error" hidden></pre>
    </main>`
  const rows = el.querySelector('#prj-rows') as HTMLElement
  const errorBox = el.querySelector('#prj-error') as HTMLPreElement

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  async function refresh(): Promise<void> {
    const { projects } = await listProjects()
    rows.innerHTML = projects.map(row).join('') || '<tr><td colspan="5">Пока нет</td></tr>'
    rows.querySelectorAll<HTMLButtonElement>('button[data-drop]').forEach(b =>
      b.addEventListener('click', async () => {
        if (!window.confirm(`Удалить проект «${b.dataset.name}»? Файлы останутся.`)) return
        try {
          await api(`/api/v1/projects/${encodeURIComponent(b.dataset.drop ?? '')}`, { method: 'DELETE' })
          await refresh()
        } catch (e) {
          showError(e)
        }
      }),
    )
    rows.querySelectorAll<HTMLButtonElement>('button[data-finish]').forEach(b =>
      b.addEventListener('click', async () => {
        const ok = window.confirm(
          `Завершить проект «${b.dataset.name}»? Его файлы удалятся, если не заняты в других проектах.`,
        )
        if (!ok) return
        try {
          await api(`/api/v1/projects/${encodeURIComponent(b.dataset.finish ?? '')}/finish`, { method: 'POST' })
          await refresh()
        } catch (e) {
          showError(e)
        }
      }),
    )
  }

  const form = el.querySelector('#prj-form') as HTMLFormElement
  form.addEventListener('submit', async event => {
    event.preventDefault()
    const name = String(new FormData(form).get('name') ?? '').trim()
    try {
      const created = await createProject(name)
      location.hash = `#/p/${encodeURIComponent(created.id)}`
    } catch (e) {
      showError(e)
    }
  })

  void refresh().catch(showError)
  return { refresh }
}
