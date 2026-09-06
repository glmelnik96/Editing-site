/**
 * Кабинет вошедшего: приветствие, два больших выбора и недавние проекты.
 *
 * Карточки — ссылки, а не блоки с обработчиком: клавиатура, средняя кнопка мыши и «открыть в
 * новой вкладке» достаются даром, а не переписываются руками.
 */
import { fmtDuration } from './assets'
import { escapeHtml } from './html'
import { listProjects, type ProjectCard } from './project'
import type { Me } from './shell'

const RECENT_LIMIT = 3
const ROW_STEP_MS = 40

const CARD_STYLE = [
  'display:flex',
  'flex-direction:column',
  'gap:8px',
  'min-height:220px',
  'padding:28px',
  'color:var(--paper)',
  'text-decoration:none',
].join(';')

const ROW_STYLE = [
  'justify-content:space-between',
  'margin:0',
  'padding:12px 0',
  'border-top:1px solid var(--line)',
  'color:var(--paper)',
  'text-decoration:none',
].join(';')

function card(href: string, title: string, lead: string, delayMs: number): string {
  return `
    <a class="card appear" href="${href}" style="${CARD_STYLE};--delay:${delayMs}ms">
      <h2 class="display-m" style="margin:0">${title}</h2>
      <p class="lead" style="margin:0">${lead}</p>
      <span style="margin-top:auto;color:var(--brand);font-size:24px;line-height:1">→</span>
    </a>`
}

function recentRow(p: ProjectCard, i: number): string {
  const state = p.status === 'finished' ? 'завершён' : 'в работе'
  return `
    <a class="row appear" href="#/p/${encodeURIComponent(p.id)}" style="${ROW_STYLE};--delay:${i * ROW_STEP_MS}ms">
      <span>${escapeHtml(p.name)}</span>
      <span class="meta">${fmtDuration(p.duration)} · ${state}</span>
    </a>`
}

function recentBlock(projects: ProjectCard[]): string {
  return `
    <section class="stack" style="--stack-gap:8px">
      <div class="row" style="justify-content:space-between;--row-gap:16px;margin:0">
        <h2 class="display-m" style="margin:0">Недавнее</h2>
        <a href="#/projects">Все проекты</a>
      </div>
      <div>${projects.map(recentRow).join('')}</div>
    </section>`
}

export function mountHome(el: HTMLElement, me: Me): { stop: () => void } {
  let stopped = false
  const name = me.name.trim() || me.email

  el.innerHTML = `
    <div class="screen stack" style="--stack-gap:32px" id="home-column">
      <h1 class="display-l appear" style="margin:0">Привет, ${escapeHtml(name)}</h1>
      <div class="grid-2">
        ${card('#/new', 'Собрать ролик', 'Возьмите запись и соберите из неё готовый файл', 60)}
        ${card('#/files', 'Мои записи', 'Загруженные файлы и их обработка', 120)}
      </div>
    </div>`

  const column = el.querySelector('#home-column') as HTMLElement

  // «Недавнее» дорисовывается, когда придёт список: два главных выбора не должны ждать сети.
  // Проектов нет или запрос не удался — блока просто нет, надписи «пусто» тоже.
  void listProjects()
    .then(({ projects }) => {
      if (stopped || projects.length === 0) return
      column.insertAdjacentHTML('beforeend', recentBlock(projects.slice(0, RECENT_LIMIT)))
    })
    .catch(() => {})

  return {
    stop(): void {
      stopped = true
    },
  }
}
