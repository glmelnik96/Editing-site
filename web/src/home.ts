/**
 * Кабинет вошедшего: приветствие, шаги пути и недавние проекты команды.
 *
 * Карточки — ссылки, а не блоки с обработчиком: клавиатура, средняя кнопка мыши и «открыть в
 * новой вкладке» достаются даром, а не переписываются руками.
 */
import { fmtDuration } from './assets'
import { escapeHtml } from './html'
import { ownedBy, ownerLabel } from './overview'
import { listProjects, type ProjectCard } from './project'
import type { Me } from './shell'

const RECENT_LIMIT = 3
const ROW_STEP_MS = 40

const CARD_STYLE = [
  'display:flex',
  'flex-direction:column',
  'gap:var(--space-2)',
  'padding:var(--space-4)',
  'color:var(--paper)',
  'text-decoration:none',
].join(';')

type Step = {
  href: string
  title: string
  lead: string
  key: boolean
}

// Путь ровно один: сначала приносят исходники, потом собирают ролик. Конвертер сюда шагом не
// встаёт — он не часть сборки, а отдельный инструмент мимо неё, поэтому живёт строкой ниже.
// Номером «2.1» он читался как под-шаг редактора, хотя редактор ему не нужен вовсе.
const UPLOAD: Step = {
  href: '#/files',
  title: 'Загрузить исходники',
  lead: 'Видео, музыка и субтитры',
  key: true,
}
const EDITOR: Step = {
  href: '#/new',
  title: 'Открыть редактор',
  lead: 'Монтаж и сборка ролика',
  key: false,
}
const ROW_STYLE = [
  'justify-content:space-between',
  'margin:0',
  'padding:12px 0',
  'border-top:1px solid var(--line)',
  'color:var(--paper)',
  'text-decoration:none',
].join(';')

function card(step: Step, delayMs: number): string {
  return `
    <a class="card appear step-card${step.key ? ' step-key' : ''}"
      href="${step.href}" style="${CARD_STYLE};--delay:${delayMs}ms">
      <h2 class="display-m" style="margin:0">${step.title}</h2>
      <p class="lead" style="margin:0">${step.lead}</p>
      <span class="step-arrow">→</span>
    </a>`
}

/** Строка «Недавнего»: имя, у чужого проекта — автор, клипы и длительность. */
export function recentRowHtml(p: ProjectCard, i: number, myEmail: string): string {
  const whose = ownedBy(p, myEmail) ? '' : `${escapeHtml(ownerLabel(p))} · `
  return `
    <a class="row appear" href="#/p/${encodeURIComponent(p.id)}" style="${ROW_STYLE};--delay:${i * ROW_STEP_MS}ms">
      <span>${escapeHtml(p.name)}</span>
      <span class="meta">${whose}${p.clips_count} кл. · ${fmtDuration(p.duration)}</span>
    </a>`
}

function recentBlock(projects: ProjectCard[], myEmail: string): string {
  return `
    <section class="stack" style="--stack-gap:8px">
      <div class="row" style="justify-content:space-between;--row-gap:16px;margin:0">
        <h2 class="display-m" style="margin:0">Недавнее</h2>
        <a href="#/projects">Все проекты</a>
      </div>
      <div>${projects.map((p, i) => recentRowHtml(p, i, myEmail)).join('')}</div>
    </section>`
}

export function homeStepsHtml(): string {
  return `
      <div class="steps">
        ${card(UPLOAD, 60)}
        ${card(EDITOR, 120)}
      </div>
      <p class="side-tool" style="margin:0"><a href="#/convert">Открыть конвертер</a></p>`
}

export function mountHome(el: HTMLElement, me: Me): { stop: () => void } {
  let stopped = false
  const name = me.name.trim() || me.email

  el.innerHTML = `
    <div class="screen stack" style="--stack-gap:32px" id="home-column">
      <h1 class="display-l appear" style="margin:0">Привет, ${escapeHtml(name)}</h1>
      ${homeStepsHtml()}
    </div>`

  const column = el.querySelector('#home-column') as HTMLElement

  // «Недавнее» — три последних проекта команды; дорисовывается, когда придёт список: два главных
  // выбора не должны ждать сети.
  // Проектов нет или запрос не удался — блока просто нет, надписи «пусто» тоже.
  void listProjects()
    .then(({ projects }) => {
      if (stopped || projects.length === 0) return
      column.insertAdjacentHTML('beforeend', recentBlock(projects.slice(0, RECENT_LIMIT), me.email))
    })
    .catch(() => {})

  return {
    stop(): void {
      stopped = true
    },
  }
}
