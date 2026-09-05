/** Панель сборки: запуск рендера, ход задания, список готовых роликов со скачиванием. */
import { ApiError, isRetryable } from './api'
import { fmtDuration, fmtSize } from './assets'
import { escapeHtml } from './html'
import {
  cancelJob,
  deleteRender,
  listRenders,
  loadJob,
  startRender,
  type JobView,
  type RenderCard,
} from './project'

const POLL_MS = 2000

const QUALITY: Record<string, string> = { draft: 'черновик', final: 'финал' }
const JOB_TEXT: Record<string, string> = {
  queued: 'в очереди',
  running: 'собираю',
  done: 'готово',
  failed: 'не собралось',
  canceled: 'отменено',
}
const RUNNING = new Set(['queued', 'running'])

/** Дата и время без секунд: у готового ролика важен день, до которого он доживёт. */
function whenFull(iso: string): string {
  return iso.replace('T', ' ').slice(0, 16)
}

function percent(progress: number): number {
  return Math.round(Math.min(1, Math.max(0, progress)) * 100)
}

export function mountRender(el: HTMLElement, projectId: string, onBeforeStart: () => Promise<void>) {
  el.innerHTML = `
    <main class="card">
      <h3>Сборка</h3>
      <div class="row">
        <button id="rnd-draft" type="button">Собрать черновик</button>
        <button id="rnd-final" type="button">Собрать финал</button>
      </div>
      <div id="rnd-job" hidden>
        <span class="muted" id="rnd-status"></span>
        <div class="progress"><i id="rnd-bar" style="width:0%"></i></div>
        <button id="rnd-cancel" type="button">Отменить сборку</button>
      </div>
      <ul id="rnd-list" class="versions"><li class="muted">Пока нет</li></ul>
      <pre id="rnd-error" hidden></pre>
    </main>`
  const draftButton = el.querySelector('#rnd-draft') as HTMLButtonElement
  const finalButton = el.querySelector('#rnd-final') as HTMLButtonElement
  const jobBox = el.querySelector('#rnd-job') as HTMLElement
  const statusBox = el.querySelector('#rnd-status') as HTMLElement
  const bar = el.querySelector('#rnd-bar') as HTMLElement
  const cancelButton = el.querySelector('#rnd-cancel') as HTMLButtonElement
  const list = el.querySelector('#rnd-list') as HTMLElement
  const errorBox = el.querySelector('#rnd-error') as HTMLPreElement

  let jobId: string | null = null
  let timer: number | undefined
  let stopped = false // ушли с экрана — опрос дальше не идёт

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }
  const clearError = () => {
    errorBox.hidden = true
    errorBox.textContent = ''
  }

  function showJob(status: JobView['status'], progress: number): void {
    jobBox.hidden = false
    const pct = percent(progress)
    const broken = status === 'failed' || status === 'canceled'
    statusBox.textContent = status === 'running' ? `${JOB_TEXT.running}, ${pct} %` : (JOB_TEXT[status] ?? status)
    // Под подписью «не собралось» полоса на 80 % врёт: у сорвавшейся сборки прогресса больше нет.
    bar.style.width = broken ? '0%' : `${pct}%`
    const running = RUNNING.has(status)
    cancelButton.hidden = !running
    draftButton.disabled = running
    finalButton.disabled = running
  }

  /** Вернуть панель в исходный вид: задания больше нет, собрать можно заново. */
  function releaseControls(): void {
    jobId = null
    window.clearTimeout(timer)
    jobBox.hidden = true
    cancelButton.hidden = true
    draftButton.disabled = false
    finalButton.disabled = false
  }

  function row(r: RenderCard): string {
    const quality = QUALITY[r.quality] ?? r.quality
    return `<li>
      <span>${escapeHtml(quality)} · ${fmtDuration(r.duration)} · ${fmtSize(r.size)} · до ${whenFull(r.expires_at)}</span>
      <span class="render-actions">
        <a href="${escapeHtml(r.download)}" download>Скачать</a>
        <button data-drop="${escapeHtml(r.id)}">Удалить</button>
      </span></li>`
  }

  async function refresh(): Promise<void> {
    if (stopped) return
    const { renders } = await listRenders(projectId)
    if (stopped) return
    list.innerHTML = renders.map(row).join('') || '<li class="muted">Пока нет</li>'
    list.querySelectorAll<HTMLButtonElement>('button[data-drop]').forEach(b =>
      b.addEventListener('click', async () => {
        if (!window.confirm('Удалить готовый ролик? Файл пропадёт без возможности восстановления.')) return
        try {
          await deleteRender(b.dataset.drop ?? '')
          await refresh()
        } catch (e) {
          showError(e)
        }
      }),
    )
  }

  function scheduleNext(): void {
    window.clearTimeout(timer)
    if (stopped || !jobId) return
    timer = window.setTimeout(() => void poll(), POLL_MS)
  }

  async function poll(): Promise<void> {
    if (stopped || !jobId) return
    let job: JobView
    try {
      job = await loadJob(jobId)
    } catch (e) {
      showError(e)
      // Оборванный запрос и 5xx — не приговор сборке: показываем и ждём следующего круга.
      // А 401 (сессию вытеснил чужой вход) или 404 сами не пройдут: опрос бился бы в дверь каждые
      // две секунды вечно, и кнопки сборки остались бы заблокированными навсегда.
      if (isRetryable(e)) scheduleNext()
      else releaseControls()
      return
    }
    if (stopped || job.id !== jobId) return
    clearError() // опрос снова доходит: жалобу на прошлый оборванный запрос убираем
    showJob(job.status, job.progress)
    if (RUNNING.has(job.status)) {
      scheduleNext()
      return
    }
    jobId = null
    window.clearTimeout(timer)
    if (job.status === 'failed') showError(job.error || 'Сборка не удалась')
    if (job.status === 'done') await refresh().catch(showError)
  }

  async function start(quality: 'draft' | 'final'): Promise<void> {
    clearError()
    draftButton.disabled = true
    finalButton.disabled = true
    try {
      // Сначала дописываем несохранённое: собрать надо то, что человек видит на шкале.
      await onBeforeStart()
    } catch (e) {
      // Правка до сервера не дошла — воркер собрал бы прошлую версию. Молчать тут нельзя:
      // человек получил бы чужой ролик и не понял, почему в нём нет его последних правок.
      showError(`Правки не сохранены, сборка не запущена: ${e instanceof Error ? e.message : String(e)}`)
      releaseControls()
      return
    }
    try {
      const { job_id } = await startRender(projectId, quality)
      if (stopped) return
      jobId = job_id
      showJob('queued', 0)
      scheduleNext()
    } catch (e) {
      showError(e)
      releaseControls()
    }
  }

  draftButton.addEventListener('click', () => void start('draft'))
  finalButton.addEventListener('click', () => void start('final'))

  cancelButton.addEventListener('click', async () => {
    if (!jobId) return
    try {
      await cancelJob(jobId)
    } catch (e) {
      showError(e)
      return
    }
    window.clearTimeout(timer)
    await poll()
  })

  void refresh().catch(showError)

  return {
    /** Остановить опрос: редактор зовёт при уходе с экрана. */
    stop(): void {
      stopped = true
      window.clearTimeout(timer)
    },
  }
}
