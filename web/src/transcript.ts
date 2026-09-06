/**
 * Панель транскрипта: монтаж по тексту рядом с плеером исходника.
 *
 * Человек находит нужное глазами в тексте, а не тычет в шкалу: клик по слову перематывает плеер,
 * выделение от слова до слова кладёт кусок на шкалу. Времена слов интерполированы (±0.3 с),
 * поэтому границы отдаём наверх как есть, а досаживает их на измеренные паузы сервер — редактор
 * ставит клип со snap_to_pauses.
 *
 * Пометки качества тише текста: подозрительная реплика — слабая полоска у края, неподтверждённая
 * граница — пунктир под таймкодом, подробности в подсказке при наведении. Панель показывает речь,
 * а не диагностику.
 */
import { ApiError, isRetryable } from './api'
import { loadAsset, type Asset } from './assets'
import { escapeHtml } from './html'
import {
  loadJob,
  loadTranscript,
  startTranscribe,
  type Transcript,
  type TranscriptSegment,
} from './project'
import { formatTimecode } from './timecode'

const POLL_MS = 2000
const LABEL_LIMIT = 28 // длиннее подпись кнопки в узкой колонке переносится на третью строку
// Слепое ожидание без номера задания не может длиться вечно: сорвавшаяся чужая расшифровка
// иначе оставила бы опрос стучаться в сервер до конца сессии.
const BLIND_WAIT_MS = 15 * 60 * 1000

const JOB_TEXT: Record<string, string> = {
  queued: 'в очереди',
  running: 'расшифровываю',
  done: 'готово',
  failed: 'не расшифровалось',
  canceled: 'отменено',
}
const RUNNING = new Set(['queued', 'running'])

export type TranscriptHandlers = {
  /** Перемотать плеер исходника на время слова. */
  onSeek: (seconds: number) => void
  /** Положить кусок на шкалу: времена крайних выделенных слов. */
  onTake: (start: number, end: number) => void
}

/** Слово с координатами; segment — номер реплики, по нему слова раскладываются обратно в абзацы. */
export type FlatWord = { w: string; s: number; e: number; segment: number }

/**
 * Слова всех сегментов одним списком по возрастанию времени.
 *
 * У чужого транскрипта пословных времён может не быть: такая реплика даёт одно «слово» во весь
 * свой текст. По нему всё ещё можно кликнуть и взять кусок, только точность будет пофразной.
 */
export function flattenWords(segments: TranscriptSegment[]): FlatWord[] {
  const out: FlatWord[] = []
  segments.forEach((segment, index) => {
    const words = segment.words ?? []
    if (words.length === 0) {
      out.push({ w: segment.text, s: segment.start, e: segment.end, segment: index })
      return
    }
    for (const word of words) out.push({ w: word.w, s: word.s, e: word.e, segment: index })
  })
  return out
}

/**
 * Номер звучащего сейчас слова или -1.
 *
 * Ищем делением пополам: на часовой расшифровке это тысячи слов, а звать поиск приходится на
 * каждый тик плеера. В паузе между словами не подсвечиваем ничего — подсветка на молчании врала бы,
 * что слово ещё звучит.
 */
export function wordAtTime(words: FlatWord[], time: number): number {
  let low = 0
  let high = words.length - 1
  while (low <= high) {
    const middle = (low + high) >> 1
    if (time < words[middle].s) high = middle - 1
    else if (time >= words[middle].e) low = middle + 1
    else return middle
  }
  return -1
}

/** Границы выделения из двух кликов: в каком порядке кликали — неважно. */
export function selectionBounds(anchor: number, focus: number): { from: number; to: number } {
  return anchor <= focus ? { from: anchor, to: focus } : { from: focus, to: anchor }
}

/**
 * Выделенные слова одной строкой для подписи кнопки.
 *
 * Длинное выделение сворачивается по краям, а не обрезается с хвоста: по первому и последнему
 * слову человек узнаёт свой кусок, а середину он и так видит подсвеченной в тексте.
 */
export function selectionText(words: FlatWord[], from: number, to: number, limit = LABEL_LIMIT): string {
  const picked = words.slice(Math.max(0, from), to + 1).map(word => word.w)
  const full = picked.join(' ')
  if (full.length <= limit || picked.length < 3) return full
  return `${picked[0]} … ${picked[picked.length - 1]}`
}

/** Подсказка о качестве реплики: пусто, если сомнений нет. */
function edgeNote(segment: TranscriptSegment): string {
  const edges = [
    segment.start_verified ? '' : 'начало',
    segment.end_verified ? '' : 'конец',
  ].filter(Boolean)
  return edges.length ? `Не подтверждено паузой: ${edges.join(', ')}` : ''
}

export function mountTranscript(el: HTMLElement, handlers: TranscriptHandlers) {
  el.innerHTML = `
    <main class="card">
      <h3>Текст</h3>
      <p class="muted" id="tr-hint">Выберите файл, чтобы монтировать по тексту.</p>
      <div id="tr-start" hidden>
        <div class="row">
          <button id="tr-run" type="button">Расшифровать</button>
        </div>
      </div>
      <div id="tr-job" hidden>
        <span class="muted" id="tr-status"></span>
        <div class="progress"><i id="tr-bar" style="width:0%"></i></div>
      </div>
      <div class="transcript" id="tr-text" hidden></div>
      <!-- Кнопка куска стоит под текстом: появись она над ним, текст съезжал бы вниз прямо
           под указателем в тот момент, когда выделение только протаскивают. -->
      <div id="tr-take" hidden>
        <div class="row">
          <button id="tr-take-run" type="button">Взять кусок</button>
          <span class="muted" id="tr-take-note"></span>
        </div>
      </div>
      <pre id="tr-error" hidden></pre>
    </main>`
  const hint = el.querySelector('#tr-hint') as HTMLElement
  const startBox = el.querySelector('#tr-start') as HTMLElement
  const runButton = el.querySelector('#tr-run') as HTMLButtonElement
  const jobBox = el.querySelector('#tr-job') as HTMLElement
  const statusBox = el.querySelector('#tr-status') as HTMLElement
  const bar = el.querySelector('#tr-bar') as HTMLElement
  const takeBox = el.querySelector('#tr-take') as HTMLElement
  const takeButton = el.querySelector('#tr-take-run') as HTMLButtonElement
  const takeNote = el.querySelector('#tr-take-note') as HTMLElement
  const textBox = el.querySelector('#tr-text') as HTMLElement
  const errorBox = el.querySelector('#tr-error') as HTMLPreElement

  let asset: Asset | null = null
  let words: FlatWord[] = []
  let nodes: HTMLElement[] = []
  let anchor = -1 // слово, с которого начнётся кусок: обычный клик ставит его сюда
  let range: { from: number; to: number } | null = null
  let dragging = false // указатель прижат к тексту: тянем выделение
  let current = -1 // подсвеченное сейчас слово
  let jobId: string | null = null
  let waiting = false // расшифровка идёт: опрос продолжается
  let blindUntil = 0 // докуда ждём транскрипт, когда номера задания у нас нет
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

  function showJob(text: string, progress: number): void {
    startBox.hidden = true
    jobBox.hidden = false
    statusBox.textContent = text
    bar.style.width = `${Math.round(Math.min(1, Math.max(0, progress)) * 100)}%`
  }

  /** Ожидание кончилось ничем: опрос гасим, кнопку возвращаем. */
  function release(): void {
    waiting = false
    jobId = null
    window.clearTimeout(timer)
    jobBox.hidden = true
    startBox.hidden = false
    runButton.disabled = false
  }

  /** Сбросить панель под другой файл: ни текста, ни выделения, ни опроса от прошлого. */
  function reset(): void {
    window.clearTimeout(timer)
    waiting = false
    jobId = null
    words = []
    nodes = []
    anchor = -1
    range = null
    dragging = false
    current = -1
    textBox.innerHTML = ''
    textBox.hidden = true
    startBox.hidden = true
    jobBox.hidden = true
    takeBox.hidden = true
    runButton.disabled = false
    clearError()
  }

  /** Подсветка выделения и кнопка «Взять кусок» под текущее состояние. */
  function paint(): void {
    nodes.forEach((node, index) => {
      node.classList.toggle('picked', range !== null && index >= range.from && index <= range.to)
      node.classList.toggle('anchor', range === null && index === anchor)
    })
    if (!range) {
      takeBox.hidden = true
      return
    }
    takeBox.hidden = false
    takeButton.textContent = `Взять «${selectionText(words, range.from, range.to)}»`
    takeNote.textContent = `${formatTimecode(words[range.from].s)} — ${formatTimecode(words[range.to].e)}`
  }

  function show(data: Transcript): void {
    words = flattenWords(data.segments)
    let cursor = 0
    textBox.innerHTML = data.segments
      .map((segment, index) => {
        const said: string[] = []
        while (cursor < words.length && words[cursor].segment === index) {
          said.push(`<span class="tr-w" data-i="${cursor}">${escapeHtml(words[cursor].w)}</span>`)
          cursor++
        }
        // Подсказка висит на всей реплике, а не на словах: слово со своим title перебивало бы её
        // ровно там, куда человек и наводит указатель.
        const suspect = segment.suspect ? ' suspect' : ''
        const title = segment.suspect ? ' title="Провайдер не уверен в этой реплике"' : ''
        const note = edgeNote(segment)
        const at = note
          ? `<span class="tr-at unsure" title="${escapeHtml(note)}">${formatTimecode(segment.start)}</span>`
          : `<span class="tr-at">${formatTimecode(segment.start)}</span>`
        return `<p class="tr-seg${suspect}"${title}>${at}${said.join(' ')}</p>`
      })
      .join('')
    textBox.hidden = false
    nodes = Array.from(textBox.querySelectorAll<HTMLElement>('.tr-w'))
    hint.textContent = 'Клик по слову — перемотка, протаскивание или Shift+клик — кусок.'
    paint()
  }

  async function load(): Promise<void> {
    const id = asset?.id
    if (!id) return
    let data: Transcript
    try {
      data = await loadTranscript(id)
    } catch (e) {
      showError(e)
      hint.textContent = 'Текст не загрузился.'
      return
    }
    if (stopped || asset?.id !== id) return // пока грузили, выбрали другой файл
    clearError()
    show(data)
  }

  function scheduleNext(): void {
    window.clearTimeout(timer)
    if (stopped || !waiting) return
    timer = window.setTimeout(() => void poll(), POLL_MS)
  }

  async function pollJob(id: string, assetId: string): Promise<void> {
    const job = await loadJob(id)
    if (stopped || jobId !== id || asset?.id !== assetId) return
    clearError() // опрос снова доходит: жалобу на прошлый оборванный запрос убираем
    const pct = Math.round(Math.min(1, Math.max(0, job.progress)) * 100)
    // Под подписью «не расшифровалось» полоса на 80 % врёт: прогресса у сорвавшегося задания нет.
    showJob(
      job.status === 'running' ? `${JOB_TEXT.running}, ${pct} %` : (JOB_TEXT[job.status] ?? job.status),
      RUNNING.has(job.status) || job.status === 'done' ? job.progress : 0,
    )
    if (RUNNING.has(job.status)) {
      scheduleNext()
      return
    }
    if (job.status === 'done') {
      waiting = false
      jobId = null
      jobBox.hidden = true
      await load()
      return
    }
    release()
    if (job.status === 'failed') showError(job.error || 'Расшифровка не удалась')
  }

  /** Расшифровку затеяли не мы, номера задания нет: ждём транскрипт по карточке ассета. */
  async function pollCard(assetId: string): Promise<void> {
    const card = await loadAsset(assetId)
    if (stopped || asset?.id !== assetId) return
    clearError()
    if (card.files.transcript) {
      waiting = false
      jobBox.hidden = true
      asset = card // карточка свежее: в ней уже есть ссылка на транскрипт
      await load()
      return
    }
    if (Date.now() > blindUntil) {
      release()
      hint.textContent = 'Расшифровка идёт дольше обычного. Обновите страницу, чтобы увидеть итог.'
      return
    }
    scheduleNext()
  }

  async function poll(): Promise<void> {
    const assetId = asset?.id
    if (stopped || !waiting || !assetId) return
    try {
      if (jobId) await pollJob(jobId, assetId)
      else await pollCard(assetId)
    } catch (e) {
      showError(e)
      // Оборванный запрос и 5xx — не приговор расшифровке: показываем и ждём следующего круга.
      // А 401 (сессию вытеснил чужой вход) или 404 сами не пройдут: опрос бился бы в дверь каждые
      // две секунды вечно, и кнопка «Расшифровать» осталась бы заблокированной навсегда.
      if (isRetryable(e)) scheduleNext()
      else release()
    }
  }

  async function run(): Promise<void> {
    const id = asset?.id
    if (!id) return
    clearError()
    runButton.disabled = true
    try {
      const { job_id } = await startTranscribe(id)
      if (stopped || asset?.id !== id) return
      jobId = job_id
    } catch (e) {
      if (stopped || asset?.id !== id) return
      const code = e instanceof ApiError ? e.code : ''
      if (code === 'transcript_exists') {
        // Транскрипт появился, пока страница висела открытой: показываем текст, а не жалобу.
        startBox.hidden = true
        await load()
        return
      }
      if (code === 'transcription_unavailable') {
        // Ключа провайдера на сервере нет: повторный клик ничего не изменит.
        hint.textContent = 'Расшифровка на этом сервере не настроена.'
        return
      }
      if (code !== 'already_queued') {
        runButton.disabled = false
        showError(e)
        return
      }
      // Расшифровку уже поставили (другая вкладка или агент), номера задания в отказе нет.
      jobId = null
      blindUntil = Date.now() + BLIND_WAIT_MS
    }
    waiting = true
    showJob(jobId ? JOB_TEXT.queued : 'расшифровка уже идёт', 0)
    scheduleNext()
  }
  runButton.addEventListener('click', () => void run())

  /** Номер слова под указателем или -1, если указатель не на слове. */
  function wordUnder(event: Event): number {
    const hit = (event.target as HTMLElement).closest('.tr-w') as HTMLElement | null
    if (!hit) return -1
    const index = Number(hit.dataset.i)
    return Number.isInteger(index) && index >= 0 && index < words.length ? index : -1
  }

  // Выделение протаскиванием, как в любом тексте: нажали на первом слове, отпустили на последнем.
  // Клик без протаскивания остаётся перемоткой, Shift+клик — вторым краем куска (так удобнее,
  // когда куски длиннее экрана). Собственное выделение панель рисует сама: своё выделение браузера
  // на том же тексте показывало бы вторую, спорящую с нашей, границу куска.
  textBox.addEventListener('pointerdown', event => {
    const index = wordUnder(event)
    if (index < 0) return
    if (event.shiftKey && anchor >= 0) {
      range = selectionBounds(anchor, index)
      paint()
      return
    }
    // Новая точка отсчёта: копить прошлое выделение незачем.
    dragging = true
    anchor = index
    range = null
    paint()
  })
  textBox.addEventListener('pointermove', event => {
    if (!dragging) return
    // Кнопку могли отпустить за пределами панели: отпускания мы там не увидим, и выделение
    // тянулось бы за указателем уже без нажатой кнопки.
    if (event.buttons === 0) {
      dragging = false
      return
    }
    const index = wordUnder(event)
    if (index < 0 || index === anchor) return
    range = selectionBounds(anchor, index)
    paint()
  })
  textBox.addEventListener('pointerup', event => {
    if (!dragging) return
    dragging = false
    const index = wordUnder(event)
    if (index >= 0 && index !== anchor) {
      // Указатель мог доехать до слова и без промежуточного move: последнее слово куска берём
      // с отпускания, иначе быстрое протаскивание теряет край.
      range = selectionBounds(anchor, index)
      paint()
      return
    }
    // Указатель не сдвинулся со слова — это был клик: перематываем плеер.
    if (!range && index === anchor && anchor >= 0) handlers.onSeek(words[anchor].s)
  })
  // Прокрутка пальцем по тексту отменяет нажатие: это не выбор куска и не перемотка.
  textBox.addEventListener('pointercancel', () => (dragging = false))

  takeButton.addEventListener('click', () => {
    if (!range) return
    handlers.onTake(words[range.from].s, words[range.to].e)
    // Кусок уже на шкале: оставить выделение — значит пригласить положить его второй раз.
    anchor = range.to
    range = null
    paint()
  })

  return {
    /** Выбранный в панели исходника файл: тот же самый файл панель не перезагружает. */
    setAsset(next: Asset | null): void {
      if (next?.id === asset?.id) return
      asset = next
      reset()
      if (!next) {
        hint.textContent = 'Выберите файл, чтобы монтировать по тексту.'
        return
      }
      if (next.files.transcript) {
        hint.textContent = 'Загружаю текст…'
        void load()
        return
      }
      hint.textContent = 'Расшифровки ещё нет.'
      startBox.hidden = false
    },

    /** Время плеера исходника: подсвечивает звучащее слово. */
    setTime(seconds: number): void {
      if (!nodes.length) return
      const index = wordAtTime(words, seconds)
      if (index === current) return
      if (current >= 0) nodes[current]?.classList.remove('now')
      current = index
      if (index < 0) return
      const node = nodes[index]
      node.classList.add('now')
      // Панель не должна отставать от речи, но и вырывать прокрутку из-под руки тоже: двигаем
      // текст, только когда слово уже ушло за край окошка.
      const box = textBox.getBoundingClientRect()
      const spot = node.getBoundingClientRect()
      if (spot.top < box.top || spot.bottom > box.bottom) node.scrollIntoView({ block: 'nearest' })
    },

    /** Остановить опрос: редактор зовёт при уходе с экрана. */
    stop(): void {
      stopped = true
      window.clearTimeout(timer)
    },
  }
}
