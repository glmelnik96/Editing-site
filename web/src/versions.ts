/** Точки сохранения проекта: показать пул, сохранить новую, вернуться к выбранной. */
import { ApiError } from './api'
import { fmtDuration } from './assets'
import { escapeHtml } from './html'
import { createCheckpoint, listVersions, restoreVersion, type Project, type VersionCard } from './project'

function when(iso: string): string {
  return iso.replace('T', ' ').slice(11, 19)
}

export function mountVersions(el: HTMLElement, projectId: string, onRestored: (p: Project) => void) {
  el.innerHTML = `
    <main class="card">
      <h3>Точки сохранения</h3>
      <form id="ver-form" class="row">
        <input name="label" placeholder="Например: до перестановки" maxlength="200" />
        <button type="submit">Сохранить точку</button>
      </form>
      <ul id="ver-list" class="versions"><li class="muted">Пока нет</li></ul>
      <pre id="ver-error" hidden></pre>
    </main>`
  const list = el.querySelector('#ver-list') as HTMLElement
  const errorBox = el.querySelector('#ver-error') as HTMLPreElement

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  function row(v: VersionCard): string {
    const title = v.label || `версия ${v.version}`
    return `<li><span>${escapeHtml(title)} · ${when(v.created_at)} · ${v.clips_count} кл. · ${fmtDuration(v.duration)}</span>
      <button data-restore="${escapeHtml(v.id)}" data-title="${escapeHtml(title)}">Вернуться</button></li>`
  }

  async function refresh(): Promise<void> {
    const { versions } = await listVersions(projectId)
    list.innerHTML = versions.map(row).join('') || '<li class="muted">Пока нет</li>'
    list.querySelectorAll<HTMLButtonElement>('button[data-restore]').forEach(b =>
      b.addEventListener('click', async () => {
        if (!window.confirm(`Вернуться к точке «${b.dataset.title}»? Текущее состояние заменится.`)) return
        try {
          onRestored(await restoreVersion(projectId, b.dataset.restore ?? ''))
          await refresh()
        } catch (e) {
          showError(e)
        }
      }),
    )
  }

  const form = el.querySelector('#ver-form') as HTMLFormElement
  form.addEventListener('submit', async event => {
    event.preventDefault()
    const label = String(new FormData(form).get('label') ?? '').trim()
    try {
      await createCheckpoint(projectId, label)
      form.reset()
      await refresh()
    } catch (e) {
      showError(e)
    }
  })

  void refresh().catch(showError)
  return { refresh }
}
