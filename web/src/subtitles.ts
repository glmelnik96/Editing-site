/**
 * Панель субтитров: реплики карточками до того, как они попадут в кадр.
 *
 * Расшифровка ошибается, и ошибку надо править до вжигания, а не после. Поэтому реплики живут
 * в документе проекта: правка карточки — обычная правка документа, а значит работают откат,
 * точки сохранения и защита от одновременной работы.
 */
import { ApiError, isRetryable } from './api'
import { loadAsset, type Asset } from './assets'
import { escapeHtml } from './html'
import { generateSubtitles, loadJob, startTranscribe, type Cue, type Project, type Subtitles } from './project'
import { formatTimecode, parseTimecode } from './timecode'
import { clipAssetIds, totalDuration, type Clip } from './timeline/model'

const POLL_MS = 2000

export type SubtitleHandlers = {
  /** Правка реплик: редактор кладёт её в документ и планирует сохранение. */
  onChange: (cues: Cue[]) => void
  /** Собранный сервером проект: у него уже новая версия, редактор берёт его целиком. */
  onProject: (project: Project) => void
  /** Дождаться, пока очередь правок доедет: иначе сборка реплик получит конфликт версий. */
  flush: () => Promise<void>
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

/** Правка клипа реплик не меняет: карточки незачем пересобирать, набор в textarea живёт до блюра. */
export function sameSubtitleView(a: Project | null, b: Project | null): boolean {
  if (!a || !b) return a === b
  const left = a.doc.subtitles
  const right = b.doc.subtitles
  if (left === right) return true
  if (!left || !right) return !left && !right
  return left.source === right.source
    && left.mode === right.mode
    && JSON.stringify(left.cues ?? []) === JSON.stringify(right.cues ?? [])
}

/** Галочка живая, только когда в документе есть реплики, которые можно вжечь. */
export function cuesReady(subs: Subtitles | null | undefined): subs is Subtitles {
  return Boolean(subs && subs.source === 'cues' && (subs.cues?.length ?? 0) > 0)
}

/** Снятая галочка — явный false; нет ключа у старого проекта считается включённым. */
export function burnEnabled(subs: Subtitles | null | undefined): boolean {
  return cuesReady(subs) && subs.enabled !== false
}

/** Правка карточек не сбрасывает галочку: иначе выключенные субтитры снова попали бы в ролик.
 *
 * Режим не выбираем: панель собирает только вжигание. Уже стоящий `soft` сохраняем как есть —
 * его мог поставить агент через API, и правка реплик человеком не повод менять ему решение.
 */
export function patchCues(previous: Subtitles | null | undefined, cues: Cue[]): Subtitles {
  return {
    source: 'cues',
    asset_id: null,
    mode: previous?.mode ?? 'burn',
    style: previous?.style ?? 'default',
    enabled: previous?.enabled !== false,
    cues,
  }
}

export type TimelineSubAsset = {
  id: string
  name: string
  hasTranscript: boolean
}

/** Записи, которые реально лежат на шкале — в том порядке, в каком появляются в ролике. */
export function timelineSubtitleAssets(
  clips: Pick<Clip, 'asset_id'>[],
  assets: { id: string; original_name: string; files: { transcript?: string | null } }[],
): TimelineSubAsset[] {
  const byId = new Map(assets.map(a => [a.id, a]))
  return clipAssetIds(clips).map(id => {
    const asset = byId.get(id)
    return {
      id,
      name: asset?.original_name ?? id,
      hasTranscript: Boolean(asset?.files.transcript),
    }
  })
}

function sameTimeline(a: TimelineSubAsset[], b: TimelineSubAsset[]): boolean {
  return a.length === b.length && a.every((item, i) =>
    item.id === b[i].id && item.name === b[i].name && item.hasTranscript === b[i].hasTranscript)
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

/** «1 реплика», «3 реплики», «11 реплик»: счётчик на экране не должен спотыкаться о падеж. */
export function plural(count: number): string {
  const tail = count % 100
  const last = count % 10
  if (tail >= 11 && tail <= 14) return `${count} реплик`
  if (last === 1) return `${count} реплика`
  if (last >= 2 && last <= 4) return `${count} реплики`
  return `${count} реплик`
}

export function mountSubtitles(el: HTMLElement, projectId: string, handlers: SubtitleHandlers) {
  el.innerHTML = `
    <main class="card stack">
      <h3 class="display-m" style="margin:0">Субтитры</h3>
      <div id="sub-cards" class="stack"></div>
      <pre id="sub-error" hidden></pre>
    </main>`

  const cardsBox = el.querySelector('#sub-cards') as HTMLElement
  const errorBox = el.querySelector('#sub-error') as HTMLPreElement

  let stopped = false
  let project: Project | null = null
  let timelineAssets: TimelineSubAsset[] = []
  let transcribeId: string | null = null
  let jobId: string | null = null
  let timer: number | undefined
  let time = 0

  const alive = () => !stopped
  const cues = (): Cue[] => project?.doc.subtitles?.cues ?? []
  // Через общий счётчик шкалы: он вычитает переходы, а простая сумма кусков давала длину
  // больше настоящей, и реплики, выехавшие за конец ролика, не помечались.
  const total = (): number => totalDuration(project?.doc.clips ?? [])
  const missing = () => timelineAssets.filter(a => !a.hasTranscript)

  const showNote = (text: string) => {
    errorBox.hidden = false
    errorBox.textContent = text
  }

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
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
    if (jobId) {
      const name = timelineAssets.find(a => a.id === transcribeId)?.name
      paint(`<p class="lead" style="margin:0">Расшифровываю${name ? ` «${escapeHtml(name)}»` : ''} — ход вверху. Можно уйти
        на другую вкладку, работа не прервётся</p>`)
      return
    }
    if (!list.length && timelineAssets.length === 0) {
      paint(`<p class="lead" style="margin:0">Субтитры собираются из кусков на шкале,
        а не из пула исходников. Добавьте запись в ролик</p>`)
      return
    }
    const need = missing()
    if (!list.length && need.length) {
      const names = need.map(a => `«${escapeHtml(a.name)}»`).join(', ')
      paint(`<p class="lead" style="margin:0">На шкале без расшифровки: ${names}.
        Расшифровка занимает несколько минут и делается один раз на файл</p>
        <button class="btn btn-key" id="sub-transcribe">Расшифровать</button>`)
      el.querySelector('#sub-transcribe')?.addEventListener('click', () => void transcribe())
      return
    }
    if (!list.length) {
      paint(`<p class="lead" style="margin:0">Расшифровка шкалы готова. Соберите из неё
        реплики — потом их можно будет поправить</p>
        <button class="btn btn-key" id="sub-build">Собрать субтитры</button>`)
      el.querySelector('#sub-build')?.addEventListener('click', () => void build())
      return
    }
    // Реплики собраны, но на шкалу добавили запись без расшифровки: «Собрать заново» её не
    // возьмёт и молча ничего не сделает, а другой двери к расшифровке в редакторе нет.
    const late = need.length
      ? `<p class="lead" style="margin:0">На шкале без расшифровки:
          ${need.map(a => `«${escapeHtml(a.name)}»`).join(', ')}. Пока эти куски пойдут в ролик
          без субтитров</p>
          <button class="btn btn-key" id="sub-transcribe">Расшифровать</button>`
      : ''
    paint(`
      ${late}
      <div class="row">
        <span class="small">${plural(list.length)}</span>
        <button class="btn btn-ghost" id="sub-rebuild">Собрать заново</button>
      </div>
      <div class="stack">${list.map(card).join('')}</div>`)
    wire()
  }

  /**
   * Карточки живут в своём ящике, а не переписывают панель целиком: рядом с ними стоит режим
   * «по словам» с плеером и загруженным текстом, и общий innerHTML сносил бы его на каждый тик.
   */
  function paint(inner: string): void {
    cardsBox.innerHTML = inner
    errorBox.hidden = true
    errorBox.textContent = ''
  }

  function wire(): void {
    el.querySelector('#sub-transcribe')?.addEventListener('click', () => void transcribe())
    el.querySelector('#sub-rebuild')?.addEventListener('click', () => {
      // Сначала проверяем, выполнимо ли это вообще, и только потом спрашиваем про потерю правок:
      // заставлять человека соглашаться расстаться с текстом ради действия, которое всё равно не
      // состоится, — обман.
      const need = missing()
      if (need.length) {
        return showNote(`Сначала расшифруйте: ${need.map(a => `«${a.name}»`).join(', ')}`)
      }
      if (!window.confirm('Собрать реплики заново? Ваши правки текста и времени пропадут.')) return
      void build()
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
    const next = missing()[0]
    if (!next) return
    transcribeId = next.id
    try {
      const started = await startTranscribe(next.id)
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
      if (e instanceof ApiError && e.code === 'transcript_exists') {
        timelineAssets = timelineAssets.map(a => a.id === next.id ? { ...a, hasTranscript: true } : a)
        transcribeId = null
        if (missing().length) {
          await transcribe()
          return
        }
        draw()
        return
      }
      transcribeId = null
      showError(e)
    }
  }

  function poll(): void {
    window.clearTimeout(timer)
    if (stopped || !jobId) return
    timer = window.setTimeout(() => void tick(), POLL_MS)
  }

  async function tick(): Promise<void> {
    if (stopped || !jobId || !transcribeId) return
    try {
      // Задание своё — смотрим его; чужое (расшифровку заказали в другой вкладке) видно
      // только по появлению файла у записи.
      if (jobId !== 'unknown') {
        const job = await loadJob(jobId)
        if (job.status === 'failed' || job.status === 'canceled') {
          jobId = null
          transcribeId = null
          draw()
          showError(job.error || 'Расшифровка не удалась')
          return
        }
        if (job.status !== 'done') return poll()
      }
      const asset = await loadAsset(transcribeId)
      if (stopped) return
      if (asset.files.transcript) {
        const doneId = transcribeId
        jobId = null
        transcribeId = null
        timelineAssets = timelineAssets.map(a => a.id === doneId ? { ...a, hasTranscript: true } : a)
        if (missing().length) {
          await transcribe()
          return
        }
        draw()
        return
      }
      poll()
    } catch (e) {
      showError(e)
      if (isRetryable(e)) poll()
      else {
        jobId = null
        transcribeId = null
        draw()
      }
    }
  }

  async function build(): Promise<void> {
    if (!timelineAssets.length) return
    const need = missing()
    if (need.length) {
      // Раньше здесь был молчаливый выход: человек подтверждал «правки пропадут» и не получал
      // ни реплик, ни объяснения.
      showNote(`Сначала расшифруйте: ${need.map(a => `«${a.name}»`).join(', ')}`)
      return
    }
    try {
      await handlers.flush()
      handlers.onProject(await generateSubtitles(projectId, 'burn'))
    } catch (e) {
      showError(e)
    }
  }

  draw()

  return {
    /** Проект изменился: перерисовать карточки, если реплики или режим другие. */
    setProject(next: Project): void {
      if (sameSubtitleView(project, next)) {
        project = next
        return
      }
      project = next
      draw()
    },
    /**
     * Подставить документ без перерисовки: текст уже в textarea, а innerHTML стёр бы набор
     * в соседней карточке. Редактор зовёт это после правки текста, когда число реплик и
     * времена те же.
     */
    adopt(next: Project): void {
      project = next
    },
    /** Клипы шкалы и пул записей: реплики только из того, что в ролике. */
    setTimeline(clips: Pick<Clip, 'asset_id'>[], assets: Asset[]): void {
      const next = timelineSubtitleAssets(clips, assets)
      const finishedCurrent = Boolean(
        jobId && transcribeId && next.find(a => a.id === transcribeId)?.hasTranscript,
      )
      const same = sameTimeline(timelineAssets, next)
      timelineAssets = next
      if (finishedCurrent) {
        jobId = null
        transcribeId = null
        if (missing().length) {
          void transcribe()
          return
        }
        draw()
        return
      }
      if (same) return
      // Карточки не пересобираем, пока их правят: innerHTML стёр бы набор в textarea. Но
      // появившийся на шкале файл без расшифровки — повод перерисовать: иначе о нём негде
      // узнать и нечем его расшифровать.
      if (cues().length > 0 && !jobId && !missing().length) return
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
    /** Расшифровка заказана и ещё не доехала: редактор продолжает опрашивать записи. */
    busy(): boolean {
      return jobId !== null
    },
    alive,
  }
}
