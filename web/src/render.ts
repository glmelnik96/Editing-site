/** Панель сборки: настройки ролика, запуск, ход задания, список готовых роликов со скачиванием. */
import { ApiError, isRetryable } from './api'
import { downloadFileName, fmtDuration, fmtSize, fmtWhen, type Asset } from './assets'
import { escapeHtml } from './html'
import {
  cancelJob,
  deleteRender,
  listRenders,
  loadJob,
  overlaysOf,
  soundsOf,
  startRender,
  type JobView,
  type ProjectDoc,
  type RenderCard,
  type RenderFormat,
  type RenderOptions,
  type RenderQuality,
} from './project'
import { fadeInto, totalDuration } from './timeline/model'

const POLL_MS = 2000
// Во сколько раз быстрее реального времени собирает свободный воркер (замерено на ВМ): пресет
// ultrafast — как прежний черновик, veryfast — как прежний финал.
const FAST_K = 1.36
const SLOW_K = 1.04
// VP9 даже в режиме realtime заметно медленнее x264: для webm честнее удвоить оценку.
const WEBM_SLOWDOWN = 2
// «Только звук» картинку не трогает вовсе и собирается за секунды.
const AUDIO_ONLY_K = 20
// Каждое наложение — ещё один декодер и масштабирование поверх основы. Замер на VM: картинка во
// весь кадр +55 %, видео в углу +30 %; берём 40 % на наложение и не больше четырёх — дальше и
// так долго, а точность оценки уже не важна.
const OVERLAY_SLOWDOWN = 0.4
const OVERLAY_SLOWDOWN_CAP = 4
const AUDIO_ONLY_KBPS = 192
// Звук при целевом качестве: столько добавляет к весу файла дорожка AAC.
const TARGET_AUDIO_KBPS = 160
const PREFS_KEY = 'rnd.opts'

export const SHORT_SIDES = [360, 480, 720, 1080, 1440, 2160] as const
const DEFAULT_OPTIONS: RenderOptions = { quality: 'medium', format: 'mp4', short_side: 720, bitrate_kbps: 4000 }

export const QUALITY_WORD: Record<RenderQuality, string> = {
  draft: 'черновик',
  final: 'финал',
  preview: 'превью',
  medium: 'среднее',
  high: 'высокое',
  target: 'целевое',
}
const JOB_TEXT: Record<string, string> = {
  queued: 'в очереди',
  running: 'собираю',
  done: 'готово',
  failed: 'не собралось',
  canceled: 'отменено',
}
const RUNNING = new Set(['queued', 'running'])
const ASPECTS: Record<string, [number, number]> = { '16:9': [16, 9], '9:16': [9, 16], '1:1': [1, 1] }

/** Чётная сторона, как на сервере: yuv420p не кодируется при нечётной. */
function even(value: number): number {
  return Math.max(2, Math.round(value / 2) * 2)
}

/** Размер кадра по пропорции и короткой стороне — та же формула, что output_size на сервере. */
export function outputSize(aspect: string, shortSide: number): { width: number; height: number } {
  const [w, h] = ASPECTS[aspect] ?? ASPECTS['16:9']
  return w >= h
    ? { width: even((shortSide * w) / h), height: even(shortSide) }
    : { width: even(shortSide), height: even((shortSide * h) / w) }
}

export type SizeOption = { short: number; label: string }

/**
 * Разрешения для пропорции проекта: человек выбирает кадр целиком («1280×720»), а не короткую
 * сторону. Больше исходников собрать можно, но картинка от этого чётче не станет — так и пишем.
 */
export function sizeOptions(aspect: string, maxShort: number | null): SizeOption[] {
  return SHORT_SIDES.map(short => {
    const { width, height } = outputSize(aspect, short)
    const above = maxShort !== null && short > maxShort ? ' — больше исходников' : ''
    return { short, label: `${width}×${height}${above}` }
  })
}

export type SourceStats = { maxBitrate: number | null; maxShort: number | null }

/**
 * Что известно об исходниках клипов: самый плотный битрейт и самый крупный кадр.
 *
 * Битрейт — тот же, что берёт сервер потолком для «высокого»: записанный при анализе, а у
 * записей старше этой колонки — вес файла, делённый на длительность.
 */
export function sourceStats(doc: ProjectDoc, assets: Asset[]): SourceStats {
  const ids = new Set(doc.clips.map(clip => clip.asset_id))
  let maxBitrate: number | null = null
  let maxShort: number | null = null
  for (const asset of assets) {
    if (!ids.has(asset.id) || asset.kind !== 'video') continue
    const rate = asset.bit_rate ?? (asset.duration ? Math.round((asset.size * 8) / asset.duration) : null)
    if (rate !== null) maxBitrate = Math.max(maxBitrate ?? 0, rate)
    if (asset.width && asset.height) maxShort = Math.max(maxShort ?? 0, Math.min(asset.width, asset.height))
  }
  return { maxBitrate, maxShort }
}

export function fmtBitrate(bitsPerSec: number): string {
  return bitsPerSec >= 1_000_000
    ? `${(bitsPerSec / 1_000_000).toFixed(1)} Мбит/с`
    : `${Math.round(bitsPerSec / 1000)} кбит/с`
}

/** Подпись об исходниках под настройками: какой у них был кадр и битрейт. */
export function sourcesLine(stats: SourceStats): string {
  const parts: string[] = []
  if (stats.maxShort !== null) parts.push(`кадр до ${stats.maxShort}p`)
  if (stats.maxBitrate !== null) parts.push(`битрейт до ${fmtBitrate(stats.maxBitrate)}`)
  return parts.length ? `Исходники: ${parts.join(', ')}.` : ''
}

/** Минуты сборки при свободном воркере: ceil(длительность / k), не меньше одной. */
export function estimateRenderMinutes(
  durationSec: number,
  quality: RenderQuality,
  format: RenderFormat = 'mp4',
  overlays = 0,
): number {
  const speed = quality === 'draft' || quality === 'preview' ? FAST_K : SLOW_K
  const k = format === 'm4a' ? AUDIO_ONLY_K : format === 'webm' ? speed / WEBM_SLOWDOWN : speed
  // У «только звука» картинки нет, и наложения ему ничего не стоят.
  const slow = format === 'm4a' ? 1 : 1 + OVERLAY_SLOWDOWN * Math.min(overlays, OVERLAY_SLOWDOWN_CAP)
  return Math.max(1, Math.ceil(((durationSec / k) * slow) / 60))
}

/**
 * Вес файла — только там, где его можно предсказать: у целевого качества и у «только звука».
 * У остальных качество постоянное, а вес зависит от картинки: пейзаж и говорящая голова при
 * одном и том же crf расходятся в разы, и число здесь было бы выдумкой.
 */
export function estimateRenderBytes(durationSec: number, options: RenderOptions): number | null {
  if (options.format === 'm4a') return Math.round(((AUDIO_ONLY_KBPS * 1000) / 8) * durationSec)
  if (options.quality === 'target') {
    return Math.round((((options.bitrate_kbps + TARGET_AUDIO_KBPS) * 1000) / 8) * durationSec)
  }
  return null
}

function fitWord(fit: string): string {
  return fit === 'crop' ? 'обрезка' : 'поля'
}

function plural(n: number, one: string, few: string, many: string): string {
  const tens = n % 100
  const units = n % 10
  if (tens >= 11 && tens <= 14) return many
  if (units === 1) return one
  if (units >= 2 && units <= 4) return few
  return many
}

function fadeLine(doc: ProjectDoc): string | null {
  const n = doc.clips.filter((clip, index) => fadeInto(clip, index) > 0).length
  if (!n) return null
  return n === 1 ? 'Переход между клипами.' : 'Переходы между клипами.'
}

function duckLine(doc: ProjectDoc): string | null {
  return soundsOf(doc).some(sound => sound.duck) ? 'Фон приглушается под речь.' : null
}

function soundsLine(doc: ProjectDoc): string | null {
  const n = soundsOf(doc).length
  if (!n) return null
  return `Звуковая дорожка: ${n} ${plural(n, 'звук', 'звука', 'звуков')}.`
}

function overlaysLine(doc: ProjectDoc): string | null {
  const n = overlaysOf(doc).length
  if (!n) return null
  return `Поверх основы: ${n} ${plural(n, 'наложение', 'наложения', 'наложений')}.`
}

function subsLine(doc: ProjectDoc): string | null {
  const subs = doc.subtitles
  if (!subs || subs.enabled === false) return null
  // Галочку снимают, а реплики удаляют по одной — и документ остаётся с enabled: true и пустым
  // списком. Обещать субтитры, которых нет, сводка не должна.
  if (subs.source === 'cues' && !subs.cues?.length) return null
  return subs.mode === 'soft' ? 'Субтитры отдельной дорожкой.' : 'Субтитры впечатаны в кадр.'
}

function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** Сводка человеческим языком: каким выйдет файл и сколько ждать. */
export function renderSummary(doc: ProjectDoc, options: RenderOptions, durationSec: number): string {
  const parts: string[] = []
  if (options.format === 'm4a') {
    parts.push(`Только звук: m4a, ${AUDIO_ONLY_KBPS} кбит/с.`)
  } else {
    const { width, height } = outputSize(doc.output.aspect, options.short_side)
    const rate = options.quality === 'target' ? `, ${options.bitrate_kbps} кбит/с` : ''
    parts.push(
      `${capital(QUALITY_WORD[options.quality])}: ${width}×${height}, ${doc.output.aspect}, ` +
        `${fitWord(doc.output.fit)}, ${doc.output.fps} к/с, ${options.format}${rate}.`,
    )
  }
  for (const line of [soundsLine(doc), duckLine(doc), overlaysLine(doc), fadeLine(doc)]) {
    if (line) parts.push(line)
  }
  // У «только звука» нет кадра, куда вжигать субтитры, и дорожки под них в m4a нет.
  const subs = options.format === 'm4a' ? null : subsLine(doc)
  if (subs) parts.push(subs)
  const minutes = estimateRenderMinutes(durationSec, options.quality, options.format, overlaysOf(doc).length)
  const bytes = estimateRenderBytes(durationSec, options)
  parts.push(`Около ${minutes} мин, если воркер свободен${bytes ? `; файл около ${fmtSize(bytes)}` : ''}.`)
  return parts.join(' ')
}

/** Подпись готового ролика в списке: качество, кадр, формат. */
export function renderLabel(card: RenderCard): string {
  const parts = [QUALITY_WORD[card.quality] ?? card.quality]
  if (card.width && card.height) parts.push(`${card.width}×${card.height}`)
  parts.push(card.format ?? 'mp4')
  return parts.join(' · ')
}

/** Настройки из прошлого раза. Они про привычку человека, а не про проект, поэтому в браузере. */
function readOptions(): RenderOptions {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) ?? 'null') as Partial<RenderOptions> | null
    if (!raw || typeof raw !== 'object') return { ...DEFAULT_OPTIONS }
    const quality = raw.quality && raw.quality in QUALITY_WORD ? raw.quality : DEFAULT_OPTIONS.quality
    const format = raw.format === 'webm' || raw.format === 'm4a' ? raw.format : 'mp4'
    const short = SHORT_SIDES.find(side => side === raw.short_side) ?? DEFAULT_OPTIONS.short_side
    const kbps = Number(raw.bitrate_kbps)
    return {
      quality,
      format,
      short_side: short,
      bitrate_kbps: Number.isFinite(kbps) && kbps >= 300 && kbps <= 50_000 ? kbps : DEFAULT_OPTIONS.bitrate_kbps,
    }
  } catch {
    return { ...DEFAULT_OPTIONS } // приватный режим или испорченная запись: начинаем с обычного
  }
}

function saveOptions(options: RenderOptions): void {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(options))
  } catch {
    /* см. readOptions */
  }
}

export function mountRender(
  el: HTMLElement,
  projectId: string,
  onBeforeStart: () => Promise<void>,
  onReady?: () => void,
  onCount?: (n: number) => void,
) {
  let docName = ''
  let doc: ProjectDoc | null = null
  let assets: Asset[] = []
  let options = readOptions()
  // Пустая шкала: вкладка живёт ради скачивания старых роликов, но собирать из ничего нельзя.
  let empty = true
  el.innerHTML = `
    <main class="card">
      <h3>Сборка</h3>
      <div class="render-opts">
        <label>Формат
          <select id="rnd-format">
            <option value="mp4">MP4 — откроется везде</option>
            <option value="webm">WebM — для сайта</option>
            <option value="m4a">Только звук (m4a)</option>
          </select>
        </label>
        <label>Качество
          <select id="rnd-quality">
            <option value="preview">Превью — быстро и легко</option>
            <option value="medium">Среднее</option>
            <option value="high">Высокое — не хуже исходников</option>
            <option value="target">Целевое — свой битрейт</option>
          </select>
        </label>
        <label>Битрейт, кбит/с
          <input id="rnd-bitrate" class="tc" type="number" min="300" max="50000" step="100" />
        </label>
        <label>Разрешение
          <select id="rnd-size"></select>
        </label>
      </div>
      <p class="meta" id="rnd-sources"></p>
      <div class="muted stack" id="rnd-summary"></div>
      <div class="row">
        <button id="rnd-start" type="button">Собрать</button>
      </div>
      <div id="rnd-job" hidden>
        <span class="muted" id="rnd-status"></span>
        <button id="rnd-cancel" type="button">Отменить сборку</button>
      </div>
      <ul id="rnd-list" class="versions"><li class="muted">Пока нет</li></ul>
      <pre id="rnd-error" hidden></pre>
    </main>`
  const formatPick = el.querySelector('#rnd-format') as HTMLSelectElement
  const qualityPick = el.querySelector('#rnd-quality') as HTMLSelectElement
  const bitrateInput = el.querySelector('#rnd-bitrate') as HTMLInputElement
  const sizePick = el.querySelector('#rnd-size') as HTMLSelectElement
  const sourcesBox = el.querySelector('#rnd-sources') as HTMLElement
  const summaryBox = el.querySelector('#rnd-summary') as HTMLElement
  const startButton = el.querySelector('#rnd-start') as HTMLButtonElement
  const jobBox = el.querySelector('#rnd-job') as HTMLElement
  const statusBox = el.querySelector('#rnd-status') as HTMLElement
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

  /**
   * Настройки, сводка и подпись об исходниках — из одного состояния.
   *
   * Недоступное гасим, а не прячем: битрейт виден и при «среднем», просто серый, и так понятно,
   * при каком качестве он заработает. Появляющееся поле переставляло бы панель под руками.
   */
  function sync(): void {
    formatPick.value = options.format
    qualityPick.value = options.quality
    bitrateInput.value = String(options.bitrate_kbps)
    const audioOnly = options.format === 'm4a'
    // У «только звука» качество видео ничего не значит: звук всегда 192 кбит/с.
    qualityPick.disabled = audioOnly
    bitrateInput.disabled = audioOnly || options.quality !== 'target'
    sizePick.disabled = audioOnly
    const stats = doc ? sourceStats(doc, assets) : { maxBitrate: null, maxShort: null }
    const aspect = doc?.output.aspect ?? '16:9'
    sizePick.innerHTML = sizeOptions(aspect, stats.maxShort)
      .map(o => `<option value="${o.short}">${escapeHtml(o.label)}</option>`)
      .join('')
    sizePick.value = String(options.short_side)
    sourcesBox.textContent = sourcesLine(stats)
    if (!doc || empty) {
      // Раньше панель обещала «Черновик: 720p… около 1 мин» пустому проекту, а сервер отвечал
      // 422 сырым текстом в <pre>. Отказ понятнее до нажатия, чем после.
      summaryBox.innerHTML = `<p>Собирать нечего: на шкале нет ни одного куска.
        Положите запись во вкладке «Исходники»</p>`
    } else {
      summaryBox.innerHTML = `<p>${escapeHtml(renderSummary(doc, options, totalDuration(doc.clips)))}</p>`
    }
    startButton.disabled = empty || jobId !== null
  }

  function change(patch: Partial<RenderOptions>): void {
    options = { ...options, ...patch }
    saveOptions(options)
    sync()
  }

  formatPick.addEventListener('change', () => change({ format: formatPick.value as RenderFormat }))
  qualityPick.addEventListener('change', () => change({ quality: qualityPick.value as RenderQuality }))
  sizePick.addEventListener('change', () => change({ short_side: Number(sizePick.value) }))
  bitrateInput.addEventListener('change', () => {
    const kbps = Math.round(Number(bitrateInput.value))
    if (!Number.isFinite(kbps) || kbps < 300 || kbps > 50_000) {
      // Непонятное число не принимаем молча: подсвечиваем и оставляем прежнее значение.
      bitrateInput.classList.add('bad')
      return
    }
    bitrateInput.classList.remove('bad')
    change({ bitrate_kbps: kbps })
  })

  function showJob(status: JobView['status']): void {
    jobBox.hidden = false
    statusBox.textContent = RUNNING.has(status) ? 'Собираю — ход вверху' : (JOB_TEXT[status] ?? status)
    cancelButton.hidden = !RUNNING.has(status)
    startButton.disabled = RUNNING.has(status) || empty
  }

  /** Вернуть панель в исходный вид: задания больше нет, собрать можно заново. */
  function releaseControls(): void {
    jobId = null
    window.clearTimeout(timer)
    jobBox.hidden = true
    cancelButton.hidden = true
    startButton.disabled = empty
  }

  function row(r: RenderCard): string {
    const label = renderLabel(r)
    const name = downloadFileName(`${docName} (${label})`, r.format ?? 'mp4')
    return `<li>
      <span>${escapeHtml(label)} · ${fmtDuration(r.duration)} · ${fmtSize(r.size)} · до ${fmtWhen(r.expires_at)}</span>
      <span class="render-actions">
        <a href="${escapeHtml(r.download)}" download="${escapeHtml(name)}">Скачать</a>
        <button data-drop="${escapeHtml(r.id)}">Удалить</button>
      </span></li>`
  }

  async function refresh(): Promise<void> {
    if (stopped) return
    const { renders } = await listRenders(projectId)
    if (stopped) return
    onCount?.(renders.length)
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
      // две секунды вечно, и кнопка сборки осталась бы заблокированной навсегда.
      if (isRetryable(e)) scheduleNext()
      else releaseControls()
      return
    }
    if (stopped || job.id !== jobId) return
    clearError() // опрос снова доходит: жалобу на прошлый оборванный запрос убираем
    showJob(job.status)
    if (RUNNING.has(job.status)) {
      scheduleNext()
      return
    }
    jobId = null
    window.clearTimeout(timer)
    if (job.status === 'failed') showError(job.error || 'Сборка не удалась')
    if (job.status === 'done') {
      onReady?.()
      await refresh().catch(showError)
    }
  }

  async function start(): Promise<void> {
    clearError()
    startButton.disabled = true
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
      const { job_id } = await startRender(projectId, options)
      if (stopped) return
      jobId = job_id
      showJob('queued')
      scheduleNext()
    } catch (e) {
      showError(e)
      releaseControls()
    }
  }

  startButton.addEventListener('click', () => void start())

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

  sync()
  void refresh().catch(showError)

  return {
    /** Имя проекта нужно ссылке скачивания: без него файл сохраняется под внутренним номером. */
    setDoc(next: ProjectDoc, name?: string): void {
      if (name !== undefined && name !== docName) {
        docName = name
        void refresh().catch(showError)
      }
      doc = next
      empty = next.clips.length === 0
      sync()
    },
    /** Записи проекта: по ним подпись называет битрейт и кадр исходников. */
    setAssets(list: Asset[]): void {
      assets = list
      sync()
    },
    /** Остановить опрос: редактор зовёт при уходе с экрана. */
    stop(): void {
      stopped = true
      window.clearTimeout(timer)
    },
  }
}
