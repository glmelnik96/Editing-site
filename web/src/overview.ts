/**
 * Обзор работы команды: чужие проекты и чужие записи в одном месте.
 *
 * Диск на ВМ общий и делится с двумя соседними сервисами, а квота у каждого своя — значит когда
 * место кончается, вопрос «чем оно занято» ни у кого, кроме админа, ответа не имеет. Поэтому здесь
 * не только список, но и итог по людям: он отвечает на этот вопрос первой строкой.
 *
 * Чужой проект открывается в редакторе как свой. Работает редактор при этом от имени владельца:
 * файлы лежат в его каталоге, и правка уходит ему же, а не заводит копию у админа.
 */
import { api, ApiError } from './api'
import { fmtDuration, fmtSize, fmtWhen, statusText } from './assets'
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

export type OwnerUse = { email: string; name: string; bytes: number; count: number }

/** Кто сколько занял. Тяжёлые сверху: место кончается из-за них, а не из-за числа файлов. */
export function diskByOwner(assets: TeamAsset[]): OwnerUse[] {
  const by = new Map<string, OwnerUse>()
  for (const asset of assets) {
    const seen = by.get(asset.owner_email)
    if (seen) {
      seen.bytes += asset.size
      seen.count += 1
    } else {
      by.set(asset.owner_email, {
        email: asset.owner_email,
        name: asset.owner_name,
        bytes: asset.size,
        count: 1,
      })
    }
  }
  return [...by.values()].sort((a, b) => b.bytes - a.bytes)
}

/** Имя человека, а если его не назвали — почта. Пустая строка в списке ничего не значит. */
export function ownerLabel(owner: { owner_email: string; owner_name: string }): string {
  return owner.owner_name.trim() || owner.owner_email
}

export function mountOverview(el: HTMLElement) {
  el.innerHTML = `
    <section class="card stack">
      <h2 class="display-m" style="margin:0">Работа команды</h2>
      <div id="ov-use" class="stack" style="--stack-gap:4px"></div>
      <h3 style="margin:0">Проекты</h3>
      <ul id="ov-projects" class="versions"><li class="muted">Загружаю…</li></ul>
      <h3 style="margin:0">Записи</h3>
      <ul id="ov-assets" class="versions"><li class="muted">Загружаю…</li></ul>
      <pre id="ov-error" hidden></pre>
    </section>`

  const useBox = el.querySelector('#ov-use') as HTMLElement
  const projectsBox = el.querySelector('#ov-projects') as HTMLElement
  const assetsBox = el.querySelector('#ov-assets') as HTMLElement
  const errorBox = el.querySelector('#ov-error') as HTMLPreElement
  let stopped = false

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  function useHtml(rows: OwnerUse[]): string {
    if (!rows.length) return ''
    return rows
      .map(
        row =>
          `<div class="row" style="margin:0;justify-content:space-between">
            <span>${escapeHtml(row.name.trim() || row.email)}</span>
            <span class="meta">${fmtSize(row.bytes)} · ${row.count} файл(ов)</span>
          </div>`,
      )
      .join('')
  }

  function projectHtml(p: TeamProject): string {
    return `<li>
      <span>${escapeHtml(p.name)} · ${escapeHtml(ownerLabel(p))} · ${p.clips_count} кл. ·
        ${fmtDuration(p.duration)} · ${fmtWhen(p.updated_at)}</span>
      <span class="render-actions">
        <a href="#/p/${encodeURIComponent(p.id)}">Открыть</a>
        <button type="button" data-drop-project="${escapeHtml(p.id)}"
          data-name="${escapeHtml(p.name)}">Удалить</button>
      </span></li>`
  }

  function assetHtml(a: TeamAsset): string {
    return `<li>
      <span>${escapeHtml(a.original_name)} · ${escapeHtml(ownerLabel(a))} · ${fmtSize(a.size)} ·
        ${fmtDuration(a.duration)} · ${escapeHtml(statusText(a.status))}</span>
      <span class="render-actions">
        <button type="button" data-drop-asset="${escapeHtml(a.id)}"
          data-name="${escapeHtml(a.original_name)}">Удалить</button>
      </span></li>`
  }

  async function refresh(): Promise<void> {
    if (stopped) return
    const [projects, assets] = await Promise.all([
      api<{ projects: TeamProject[] }>('/api/v1/admin/projects'),
      api<{ assets: TeamAsset[] }>('/api/v1/admin/assets'),
    ])
    if (stopped) return
    useBox.innerHTML = useHtml(diskByOwner(assets.assets))
    projectsBox.innerHTML =
      projects.projects.map(projectHtml).join('') || '<li class="muted">Проектов ни у кого нет</li>'
    assetsBox.innerHTML =
      assets.assets.map(assetHtml).join('') || '<li class="muted">Записей ни у кого нет</li>'
    wire()
  }

  function drop(button: HTMLButtonElement, ask: string, path: string): void {
    button.addEventListener('click', async () => {
      if (!window.confirm(ask)) return
      button.disabled = true
      try {
        await api(path, { method: 'DELETE' })
      } catch (e) {
        button.disabled = false
        showError(e)
        return
      }
      await refresh().catch(showError)
    })
  }

  function wire(): void {
    projectsBox.querySelectorAll<HTMLButtonElement>('button[data-drop-project]').forEach(b =>
      drop(
        b,
        `Удалить чужой проект «${b.dataset.name}»? Его записи освободятся и уйдут по сроку хранения.`,
        `/api/v1/projects/${encodeURIComponent(b.dataset.dropProject ?? '')}`,
      ),
    )
    assetsBox.querySelectorAll<HTMLButtonElement>('button[data-drop-asset]').forEach(b =>
      drop(
        b,
        `Удалить чужую запись «${b.dataset.name}» без возможности восстановления?`,
        `/api/v1/assets/${encodeURIComponent(b.dataset.dropAsset ?? '')}`,
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
