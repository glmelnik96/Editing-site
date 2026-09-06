/**
 * Панель субтитров: реплики карточками до того, как они попадут в кадр.
 *
 * Расшифровка ошибается, и ошибку надо править до вжигания, а не после. Поэтому реплики живут
 * в документе проекта: правка карточки — обычная правка документа, а значит работают откат,
 * точки сохранения и защита от одновременной работы.
 */
import { ApiError, isRetryable } from './api'
import { loadAsset } from './assets'
import { escapeHtml } from './html'
import { generateSubtitles, loadJob, startTranscribe, type Cue, type Project } from './project'
import { formatTimecode, parseTimecode } from './timecode'

const POLL_MS = 2000

export type SubtitleHandlers = {
  /** Правка реплик: редактор кладёт её в документ и планирует сохранение. */
  onChange: (cues: Cue[], mode?: 'burn' | 'soft') => void
  /** Собранный сервером проект: у него уже новая версия, редактор берёт его целиком. */
  onProject: (project: Project) => void
  onSeek: (seconds: number) => void
}

/** Реплика, которая не влезает в ролик или лезет на соседнюю: сервер такую не сохранит. */
export function cueTrouble(cues: Cue[], index: number, total: number): string {
  const cue = cues[index]
  if (!cue) return ''
  if (cue.end <= cue.start) return 'конец раньше начала'
  if (total > 0 && cue.start >= total) return 'начинается после конца ролика'
  const previous = cues[index - 1]
  if (previous && cue.start < previous.end) return 'налезает на предыдущую'
  if (!cue.text.trim()) return 'пустой текст'
  if (cue.text.length > 200) return 'длиннее 200 знаков'
  if (cue.text.split('\n').length > 2) return 'больше двух строк'
  return ''
}

/** Разрезать реплику пополам по времени: текст уезжает в первую половину целиком. */
export function splitCue(cues: Cue[], index: number): Cue[] {
  const cue = cues[index]
  if (!cue || cue.end - cue.start < 0.2) return cues
  const middle = Math.round(((cue.start + cue.end) / 2) * 1000) / 1000
  const words = cue.text.split(/\s+/).filter(Boolean)
  const half = Math.ceil(words.length / 2)
  const head = words.slice(0, half).join(' ') || cue.text
  const tail = words.slice(half).join(' ') || '…'
  const next = cues.slice()
  next.splice(index, 1, { start: cue.start, end: middle, text: head }, { start: middle, end: cue.end, text: tail })
  return next
}

export function mountSubtitles(el: HTMLElement, projectId: string, handlers: SubtitleHandlers) {
  let stopped = false
  let project: Project | null = null
  let assetId: string | null = null // запись, из которой собираются реплики
  let hasTranscript = false
  let jobId: string | null = null
  let timer: number | undefined
  let time = 0

  const alive = () => !stopped
  const cues = (): Cue[] => project?.doc.subtitles?.cues ?? []
  const total = (): number =>
    (project?.doc.clips ?? []).reduce((sum, clip) => sum + Math.max(0, clip.out - clip.in), 0)

  const showError = (e: unknown) => {
    const box = el.querySelector<HTMLPreElement>('#sub-error')
    if (!box) return
    box.hidden = false
    box.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  function card(cue: Cue, index: number): string {
    const trouble = cueTrouble(cues(), index, total())
    const now = time >= cue.start && time < cue.end
    return `<article class="card cue${now ? ' now' : ''}${trouble ? ' bad' : ''}" data-cue="${index}">
      <div class="row cue-times">
        <input class="field tc mono" data-start="${index}" value="${formatTimecode(cue.start)}" />
        <input class="field tc mono" data-end="${index}" value="${formatTimecode(cue.end)}" />
        <button class="btn btn-ghost" data-split="${index}" title="Разрезать надвое">Разрезать</button>
        <button class="btn btn-ghost" data-remove="${index}">Убрать</button>
      </div>
      <textarea class="field cue-text" rows="2" maxlength="200" data-text="${index}">${escapeHtml(cue.text)}</textarea>
      ${trouble ? `<span class="meta cue-trouble">${escapeHtml(trouble)}</span>` : ''}
    </article>`
  }

  function draw(): void {
    if (stopped) return
    const list = cues()
    if (!assetId) {
      el.innerHTML = shell('<p class="lead" style="margin:0">Выберите запись во вкладке «Исходник»</p>')
      return
    }
    if (jobId) {
      el.innerHTML = shell(`<p class="lead" style="margin:0">Расшифровываю. Это занимает несколько
        минут — можно уйти на другую вкладку, работа не прервётся</p>
        <div class="progress"><i id="sub-bar" style="width:0%"></i></div>`)
      return
    }
    if (!hasTranscript) {
      el.innerHTML = shell(`<p class="lead" style="margin:0">Субтитры берутся из расшифровки записи.
        Расшифровка занимает несколько минут и делается один раз</p>
        <button class="btn btn-key" id="sub-transcribe">Расшифровать</button>`)
      el.querySelector('#sub-transcribe')?.addEventListener('click', () => void transcribe())
      return
    }
    if (!list.length) {
      el.innerHTML = shell(`<p class="lead" style="margin:0">Расшифровка готова. Соберите из неё
        реплики — потом их можно будет поправить</p>
        <button class="btn btn-key" id="sub-build">Собрать субтитры</button>`)
      el.querySelector('#sub-build')?.addEventListener('click', () => void build())
      return
    }
    const mode = project?.doc.subtitles?.mode ?? 'burn'
    const applied = project?.doc.subtitles?.source === 'cues'
    el.innerHTML = shell(`
      <div class="row">
        <span class="small">${list.length} реплик</span>
        <select class="field" id="sub-mode">
          <option value="burn"${mode === 'burn' ? ' selected' : ''}>вжечь в кадр</option>
          <option value="soft"${mode === 'soft' ? ' selected' : ''}>отдельной дорожкой</option>
        </select>
        <button class="btn btn-ghost" id="sub-rebuild">Собрать заново</button>
      </div>
      <p class="meta" style="margin:0">${applied
        ? 'Субтитры войдут в ролик при следующей сборке'
        : 'Пока не наложены: ролик соберётся без них'}</p>
      <div class="stack">${list.map(card).join('')}</div>`)
    wire()
  }

  function shell(inner: string): string {
    return `<main class="card stack">
      <h3 class="display-m" style="margin:0">Субтитры</h3>
      ${inner}
      <pre id="sub-error" hidden></pre>
    </main>`
  }

  function wire(): void {
    el.querySelector('#sub-rebuild')?.addEventListener('click', () => {
      if (!window.confirm('Собрать реплики заново? Ваши правки текста и времени пропадут.')) return
      void build()
    })
    el.querySelector<HTMLSelectElement>('#sub-mode')?.addEventListener('change', event => {
      handlers.onChange(cues(), (event.target as HTMLSelectElement).value as 'burn' | 'soft')
    })

    el.querySelectorAll<HTMLElement>('.cue').forEach(node =>
      node.addEventListener('click', event => {
        // Клик по полю правит реплику, а не перематывает: перемотка — это клик по самой карточке.
        if ((event.target as HTMLElement).closest('input, textarea, button')) return
        const cue = cues()[Number(node.dataset.cue)]
        if (cue) handlers.onSeek(cue.start)
      }),
    )

    el.querySelectorAll<HTMLTextAreaElement>('textarea[data-text]').forEach(field =>
      field.addEventListener('change', () => {
        const index = Number(field.dataset.text)
        const next = cues().slice()
        next[index] = { ...next[index], text: field.value }
        handlers.onChange(next)
      }),
    )

    el.querySelectorAll<HTMLInputElement>('input[data-start], input[data-end]').forEach(field =>
      field.addEventListener('change', () => {
        const isStart = field.dataset.start !== undefined
        const index = Number(isStart ? field.dataset.start : field.dataset.end)
        const seconds = parseTimecode(field.value)
        if (seconds === null) {
          // Непонятный ввод не двигает границу: подсвечиваем поле и оставляем прежнее значение.
          field.classList.add('bad')
          return
        }
        const next = cues().slice()
        next[index] = { ...next[index], [isStart ? 'start' : 'end']: seconds }
        handlers.onChange(next)
      }),
    )

    el.querySelectorAll<HTMLButtonElement>('button[data-split]').forEach(b =>
      b.addEventListener('click', () => handlers.onChange(splitCue(cues(), Number(b.dataset.split)))),
    )
    el.querySelectorAll<HTMLButtonElement>('button[data-remove]').forEach(b =>
      b.addEventListener('click', () => {
        const next = cues().slice()
        next.splice(Number(b.dataset.remove), 1)
        handlers.onChange(next)
      }),
    )
  }

  async function transcribe(): Promise<void> {
    if (!assetId) return
    try {
      const started = await startTranscribe(assetId)
      jobId = started.job_id
      draw()
      poll()
    } catch (e) {
      if (e instanceof ApiError && e.code === 'already_queued') {
        jobId = 'unknown'
        draw()
        poll()
        return
      }
      showError(e)
    }
  }

  function poll(): void {
    window.clearTimeout(timer)
    if (stopped || !jobId) return
    timer = window.setTimeout(() => void tick(), POLL_MS)
  }

  async function tick(): Promise<void> {
    if (stopped || !jobId || !assetId) return
    try {
      // Задание своё — смотрим его; чужое (расшифровку заказали в другой вкладке) видно
      // только по появлению файла у записи.
      if (jobId !== 'unknown') {
        const job = await loadJob(jobId)
        const bar = el.querySelector<HTMLElement>('#sub-bar')
        if (bar) bar.style.width = `${Math.round(job.progress * 100)}%`
        if (job.status === 'failed' || job.status === 'canceled') {
          jobId = null
          draw()
          showError(job.error || 'Расшифровка не удалась')
          return
        }
        if (job.status !== 'done') return poll()
      }
      const asset = await loadAsset(assetId)
      if (stopped) return
      if (asset.files.transcript) {
        jobId = null
        hasTranscript = true
        draw()
        return
      }
      poll()
    } catch (e) {
      showError(e)
      if (isRetryable(e)) poll()
      else {
        jobId = null
        draw()
      }
    }
  }

  async function build(): Promise<void> {
    if (!assetId) return
    try {
      const mode = project?.doc.subtitles?.mode ?? 'burn'
      handlers.onProject(await generateSubtitles(projectId, assetId, mode))
    } catch (e) {
      showError(e)
    }
  }

  return {
    /** Проект изменился: перерисовать карточки. */
    setProject(next: Project): void {
      project = next
      draw()
    },
    /** Запись, из которой берётся расшифровка. */
    setAsset(id: string | null, transcript: boolean): void {
      if (id === assetId && transcript === hasTranscript) return
      assetId = id
      hasTranscript = transcript
      draw()
    },
    /** Время плеера: подсветить реплику, которая сейчас в кадре. */
    setTime(seconds: number): void {
      const was = cues().findIndex(c => time >= c.start && time < c.end)
      time = seconds
      const now = cues().findIndex(c => seconds >= c.start && seconds < c.end)
      if (was === now) return
      el.querySelectorAll<HTMLElement>('.cue').forEach(node =>
        node.classList.toggle('now', Number(node.dataset.cue) === now),
      )
    },
    stop(): void {
      stopped = true
      window.clearTimeout(timer)
    },
    alive,
  }
}
