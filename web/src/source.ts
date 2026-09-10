/**
 * Панель исходников: выбор готового файла, плеер прокси, выделение куска и кнопка «в шкалу».
 *
 * Выделение хранится числами, а не в DOM: кнопка отдаёт наверх готовый диапазон, а редактор
 * решает, что с ним делать.
 */
import { escapeHtml } from './html'
import type { Asset } from './assets'
import { foldHtml, wireFold } from './fold'
import { formatTimecode, parseTimecode } from './timecode'

export type SourceHandlers = {
  onAdd: (asset: Asset, range: { from: number; to: number }) => void
  /** Сменился выбранный файл: рядом живёт панель текста, она показывает расшифровку этого же. */
  onPick?: (asset: Asset | null) => void
  /** Время плеера исходника: панель текста подсвечивает звучащее слово. */
  onTime?: (seconds: number) => void
  /** Щёлкнули по заголовку складки. Открыть или закрыть решает редактор: складок две. */
  onFold?: () => void
}

const READY = new Set(['ready', 'proxy_ready'])
const MIN_PIECE = 0.1
const STILL_DEFAULT = 5
// Столько же стоит в настройках сервера (max_still_sec). Здесь предел нужен полю ввода, а
// решает всё равно сервер: набранное сверх этого он отвергнет с внятным отказом.
const STILL_MAX = 600

export function sourcePoolNote(readyCount: number): string {
  return readyCount === 0
    ? 'Нет готовых записей. <a href="#/files">Загрузите видео, картинку или звук</a>'
    : ''
}

/**
 * Можно ли положить запись на шкалу. Видео и картинка идут в клипы, звук — на звуковую
 * дорожку под ними. Картинка годится, хотя своей длительности у неё нет.
 */
export function isPlaceable(a: Asset): boolean {
  return (a.kind === 'video' || a.kind === 'image' || a.kind === 'audio') && READY.has(a.status)
}

/** Надпись кнопки: куда ляжет кусок. Звук не встаёт в очередь клипов, и кнопка обязана это сказать. */
export function addLabel(kind: string | undefined): string {
  return kind === 'audio' ? 'Положить на звуковую дорожку' : 'Добавить в шкалу'
}

export function mountSource(el: HTMLElement, handlers: SourceHandlers) {
  // Выбор файла и плеер стоят над складкой, а не внутри: файл у обоих способов монтажа один и
  // тот же, разный только способ отрезать. Спрятанный вместе с полосой выбор пришлось бы
  // разворачивать, чтобы просто сменить запись.
  el.innerHTML = `
    <main class="card">
      <h3>Исходники</h3>
      <select id="src-pick"><option value="">— выберите файл —</option></select>
      <div id="src-player"></div>
      <p class="muted" id="src-note"></p>
      ${foldHtml(
        'src-cut',
        'Взять фрагмент',
        `<div id="src-piece">
          <div class="src-strip" id="src-strip" title="Клик — перемотка, ручки — границы куска">
            <div class="src-sel" id="src-sel"></div>
            <b class="src-handle src-handle-in" id="src-h-in"></b>
            <b class="src-handle src-handle-out" id="src-h-out"></b>
            <i class="src-cursor" id="src-cursor"></i>
          </div>
          <div class="row">
            <button id="src-mark-in" type="button" title="Взять начало с плеера">Начало</button>
            <input id="src-in" class="tc" inputmode="decimal" placeholder="0:00.0" />
            <button id="src-mark-out" type="button" title="Взять конец с плеера">Конец</button>
            <input id="src-out" class="tc" inputmode="decimal" placeholder="0:00.0" />
          </div>
        </div>
        <!-- У картинки резать нечего: вместо границ куска — сколько секунд держать её в кадре.
             hidden стоит на обёртке без класса: у .row своё display:flex, и оно перебивает
             hidden — строка торчала бы под полосой ролика, когда картинка не выбрана. -->
        <div id="src-still" hidden>
          <div class="row">
            <label class="clip-vol">Секунд в кадре
              <input id="src-secs" class="tc" type="number" min="0.5" max="${STILL_MAX}" step="0.5" />
            </label>
          </div>
        </div>
        <div class="row">
          <span id="src-range" class="muted">весь файл</span>
          <button id="src-add" type="button" disabled>Добавить в шкалу</button>
        </div>`,
      )}
    </main>`
  const showCut = wireFold(el, 'src-cut', () => handlers.onFold?.())
  const pick = el.querySelector('#src-pick') as HTMLSelectElement
  const playerBox = el.querySelector('#src-player') as HTMLElement
  const strip = el.querySelector('#src-strip') as HTMLElement
  const sel = el.querySelector('#src-sel') as HTMLElement
  const handleIn = el.querySelector('#src-h-in') as HTMLElement
  const handleOut = el.querySelector('#src-h-out') as HTMLElement
  const cursor = el.querySelector('#src-cursor') as HTMLElement
  const pieceBox = el.querySelector('#src-piece') as HTMLElement
  const stillBox = el.querySelector('#src-still') as HTMLElement
  const secsInput = el.querySelector('#src-secs') as HTMLInputElement
  const inputIn = el.querySelector('#src-in') as HTMLInputElement
  const inputOut = el.querySelector('#src-out') as HTMLInputElement
  const rangeLabel = el.querySelector('#src-range') as HTMLElement
  const addButton = el.querySelector('#src-add') as HTMLButtonElement
  const note = el.querySelector('#src-note') as HTMLElement

  let assets: Asset[] = []
  let current: Asset | null = null
  let from = 0
  let to = 0
  // Действуют ли ворота куска: прыжок по слову за его пределы их снимает.
  let inPiece = true

  const video = (): HTMLMediaElement | null => playerBox.querySelector('video, audio')

  const isStill = (): boolean => current?.kind === 'image'

  /** Полоса, поля и подпись показывают одно и то же состояние: from, to и длительность файла. */
  function refreshRange(): void {
    // У картинки нет ни полосы, ни границ: from всегда 0, а to — сколько секунд её держать.
    const still = isStill()
    pieceBox.hidden = still
    stillBox.hidden = !still
    if (still) {
      if (document.activeElement !== secsInput) secsInput.value = String(to)
      rangeLabel.textContent = `картинка на ${formatTimecode(to)}`
      addButton.disabled = to < MIN_PIECE
      return
    }
    const total = current?.duration ?? 0
    const pct = (value: number) => (total > 0 ? `${(value / total) * 100}%` : '0%')
    sel.style.left = pct(from)
    sel.style.width = total > 0 ? `${((to - from) / total) * 100}%` : '0%'
    handleIn.style.left = pct(from)
    handleOut.style.left = pct(to)
    strip.classList.toggle('empty', !current)
    if (document.activeElement !== inputIn) inputIn.value = formatTimecode(from)
    if (document.activeElement !== inputOut) inputOut.value = formatTimecode(to)
    inputIn.classList.remove('bad')
    inputOut.classList.remove('bad')
    // Звук ляжет туда, где стоит курсор шкалы, а не в конец: так озвучку кладут под нужное место.
    rangeLabel.textContent = current
      ? `кусок ${formatTimecode(to - from)} из ${formatTimecode(total)}` +
        (current.kind === 'audio' ? ' — ляжет под курсор' : '')
      : 'весь файл'
    addButton.disabled = !current || to - from < MIN_PIECE
  }

  /** Ставит границу, не давая ей вывернуться наизнанку или выйти за длительность файла. */
  function setEdge(edge: 'in' | 'out', value: number): void {
    const total = current?.duration ?? 0
    if (edge === 'in') from = Math.max(0, Math.min(value, to - MIN_PIECE))
    else to = Math.min(total, Math.max(value, from + MIN_PIECE))
    // Тронули границу — снова говорим о куске, и ворота возвращаются.
    inPiece = true
    refreshRange()
  }

  function readInput(input: HTMLInputElement, edge: 'in' | 'out'): void {
    if (!current) return
    const parsed = parseTimecode(input.value)
    if (parsed === null) {
      // Непонятный ввод не двигает границу: подсвечиваем поле и оставляем прежнее значение.
      input.classList.add('bad')
      return
    }
    setEdge(edge, parsed)
  }

  for (const [input, edge] of [[inputIn, 'in'], [inputOut, 'out']] as const) {
    input.addEventListener('change', () => readInput(input, edge))
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault()
        readInput(input, edge)
      }
    })
  }

  const timeAtStrip = (clientX: number): number => {
    const rect = strip.getBoundingClientRect()
    const total = current?.duration ?? 0
    if (rect.width <= 0 || total <= 0) return 0
    return Math.max(0, Math.min(total, ((clientX - rect.left) / rect.width) * total))
  }

  let dragEdge: 'in' | 'out' | null = null
  strip.addEventListener('pointerdown', event => {
    if (!current) return
    const target = event.target as HTMLElement
    strip.setPointerCapture(event.pointerId)
    if (target === handleIn || target === handleOut) {
      dragEdge = target === handleIn ? 'in' : 'out'
      return
    }
    const player = video()
    if (player) player.currentTime = timeAtStrip(event.clientX)
  })
  strip.addEventListener('pointermove', event => {
    if (dragEdge) setEdge(dragEdge, timeAtStrip(event.clientX))
  })
  const endStripDrag = () => {
    dragEdge = null
  }
  strip.addEventListener('pointerup', endStripDrag)
  strip.addEventListener('pointercancel', endStripDrag)

  function choose(asset: Asset | null): void {
    current = asset
    inPiece = true
    handlers.onPick?.(asset)
    from = 0
    // Картинка приходит без длительности, поэтому длину ей назначаем: пять секунд — столько
    // держится в кадре заставка, и это заметно, но не утомительно.
    to = asset?.kind === 'image' ? STILL_DEFAULT : (asset?.duration ?? 0)
    // Прокси картинки — неподвижный ролик. Без кнопок и звука он выглядит как сама картинка, а
    // шкала плеера на десять минут одного кадра только сбивала бы с толку.
    playerBox.innerHTML = !asset?.files.proxy
      ? ''
      : asset.kind === 'audio'
        ? `<audio class="player" controls preload="metadata" src="${escapeHtml(asset.files.proxy)}"></audio>`
        : asset.kind === 'image'
        ? `<video class="player" muted playsinline preload="auto" src="${escapeHtml(asset.files.proxy)}"></video>`
        : `<video class="player" controls preload="metadata" src="${escapeHtml(asset.files.proxy)}"></video>`
    if (!assets.length) {
      note.innerHTML = sourcePoolNote(0)
    } else {
      note.textContent = asset && !asset.files.proxy ? 'Прокси ещё готовится: выделять можно будет после обработки.' : ''
    }
    if (asset?.kind === 'image') secsInput.value = String(STILL_DEFAULT)
    addButton.textContent = addLabel(asset?.kind)
    const player = video()
    if (player) {
      // Останавливаемся на конце выделения только во время просмотра. Раньше время зажималось
      // и при перемотке руками, и «Конец» превращался в храповик: назад двигать можно, вперёд
      // неоткуда — за границу выделения плеер просто не пускал, и остаток файла было не посмотреть.
      player.addEventListener('timeupdate', () => {
        if (inPiece && !player.paused && player.currentTime >= to) player.pause()
        const total = current?.duration ?? 0
        cursor.style.left = total > 0 ? `${(player.currentTime / total) * 100}%` : '0%'
        handlers.onTime?.(player.currentTime)
      })
      // Кнопка «играть» показывает выделение: курсор вне него — начинаем с начала куска. Иначе
      // после досмотра до конца плеер вставал намертво, потому что каждый пуск гасили на месте.
      player.addEventListener('play', () => {
        if (inPiece && (player.currentTime < from || player.currentTime >= to)) {
          player.currentTime = from
        }
      })
    }
    refreshRange()
  }

  pick.addEventListener('change', () => {
    choose(assets.find(a => a.id === pick.value) ?? null)
  })

  function readSeconds(): void {
    const parsed = Number(secsInput.value.replace(',', '.'))
    if (!Number.isFinite(parsed) || parsed < MIN_PIECE) {
      secsInput.classList.add('bad')
      return
    }
    secsInput.classList.remove('bad')
    to = Math.round(Math.min(STILL_MAX, parsed) * 1000) / 1000
    refreshRange()
  }

  secsInput.addEventListener('change', readSeconds)
  secsInput.addEventListener('input', readSeconds)

  el.querySelector('#src-mark-in')!.addEventListener('click', () => {
    const player = video()
    if (player && current) setEdge('in', player.currentTime)
  })
  el.querySelector('#src-mark-out')!.addEventListener('click', () => {
    const player = video()
    if (player && current) setEdge('out', player.currentTime)
  })

  addButton.addEventListener('click', () => {
    if (!current) return
    handlers.onAdd(current, { from, to })
  })

  return {
    /** Показать или спрятать инструмент фрагмента. Решение принимает редактор. */
    setFold(open: boolean): void {
      showCut(open)
    },
    /** Список файлов: на шкалу годятся готовые видео, картинки и звук. */
    setAssets(list: Asset[]): void {
      assets = list.filter(isPlaceable)
      const keep = current?.id ?? ''
      pick.innerHTML =
        '<option value="">— выберите файл —</option>' +
        assets
          .map(a => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.original_name)}</option>`)
          .join('')
      if (assets.some(a => a.id === keep)) pick.value = keep
      else choose(null)
      if (!assets.length) note.innerHTML = sourcePoolNote(0)
      else if (!current || current.files.proxy) note.textContent = ''
    },
    current(): Asset | null {
      return current
    },
    /**
     * Перемотать плеер исходника: панель текста зовёт это по клику на слове.
     *
     * Слово может лежать далеко за отмеченным куском, а ворота куска останавливают плеер на его
     * конце и отматывают пуск к началу — клик по слову оказывался бы бесполезен, как только кусок
     * отмечен. Поэтому прыжок наружу ворота снимает: слушать даём везде, а вернутся они, как
     * только человек снова тронет границы куска.
     */
    seek(seconds: number): void {
      const player = video()
      if (!player) return
      const at = Math.max(0, seconds)
      inPiece = at >= from && at < to
      player.currentTime = at
    },
  }
}
