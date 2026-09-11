/**
 * Экран редактора: панель исходников, шкала, плеер склейки, автосохранение.
 *
 * Состояние — один документ проекта плюс версия. Любая правка идёт через applyClips: он кладёт
 * новый список, перерисовывает и просит сохранить. Ответ сервера заменяет документ целиком:
 * там уже подтянутые резы, флаги подтверждения и новая версия.
 */
import { ApiError } from './api'
import { POLL_MS, listProjectAssets, needsPolling, type Asset } from './assets'
import { createHistory } from './history'
import { escapeHtml } from './html'
import {
  aspectRatio,
  incomingAt,
  previewClipVolume,
  resumePlan,
  seekPlan,
  stepPlan,
  type Incoming,
  soundPlan,
  duckFactor,
  type Ducking,
} from './playback'
import {
  createSaver,
  listRenders,
  loadProject,
  type Cue,
  type FieldError,
  type Project,
  type ProjectDoc,
  soundsOf,
  type Sound,
  overlaysOf,
  type Overlay,
  type OverlayPlace,
} from './project'
import { assetData, type AssetData } from './strip'
import { formatTimecode, parseTimecode } from './timecode'
import { clampTransitions, clipAt, clipAssetIds, clipDuration, fadeInto, insertClip, maxFade, ms, newClipId, percentToZoom, removeClip, splitAt, timelineStart, totalDuration, trimClip, zoomToPercent, type Clip } from './timeline/model'
import { mountInspector, type Selected } from './inspector'
import { mountRender } from './render'
import { resolveTab, tabEnabled, type EditorTab } from './editor-tabs'
import { HOTKEYS, needsClip, shortcutFor, type Shortcut } from './hotkeys'
import { mountSource } from './source'
import { burnEnabled, cuesReady, mountSubtitles, patchCues } from './subtitles'
import { mountTranscript } from './transcript'
import { newOverlayId, OVERLAY_SIZE_DEFAULT, overlayBox, overlayPlan, removeOverlay, updateOverlay } from './timeline/overlays'
import { newSoundId, removeSound, updateSound } from './timeline/sounds'
import { mountTimeline, type AssetInfo } from './timeline/view'
import { mountVersions } from './versions'

/** Шаг опроса записей, когда ничего не обрабатывается и не расшифровывается. */
const IDLE_POLL_MS = 20000

// Вид редактора — привычка человека, а не свойство проекта: свёрнутая панель и открытая складка
// живут в браузере. В документе им делать нечего, там их увидели бы все, кто откроет проект.
const SIDE_KEY = 'ed.side'
const PROPS_KEY = 'ed.props'
const FOLD_KEY = 'ed.fold'

function readPref(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback
  } catch {
    return fallback // приватный режим: вид просто не запомнится
  }
}

function savePref(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* см. readPref */
  }
}

const STATE_TEXT = {
  idle: 'сохранено',
  pending: 'правки не сохранены',
  saving: 'сохраняю…',
  failed: 'не сохранено, попробуйте ещё правку',
}

export function mountEditor(el: HTMLElement, projectId: string) {
  el.innerHTML = `
    <div class="project-bar row">
      <a class="btn btn-ghost" href="#/projects">← Проекты</a>
      <strong id="ed-name" class="display-m project-name">Проект</strong>
      <span class="small" id="ed-state">загрузка…</span>
      <span id="ed-notice" class="meta"></span>
      <div class="saves" id="ed-saves">
        <button type="button" class="btn btn-ghost" id="ed-saves-toggle" aria-expanded="false">
          Сохранения проекта
        </button>
        <div class="saves-panel" id="ed-saves-panel" hidden></div>
      </div>
    </div>
    <div class="editor" id="ed-grid">
      <section class="side">
        <!-- Голова панели не прокручивается вместе с её содержимым: вкладки нужны с любого места
             списка, а кнопке сворачивания уезжать нельзя вовсе — свернув панель, ею же и
             разворачивают обратно. -->
        <div class="side-head">
          <nav class="tabs" id="ed-tabs">
            <button type="button" class="tab" data-tab="source">Исходники</button>
            <button type="button" class="tab" data-tab="subtitles">Субтитры</button>
            <button type="button" class="tab" data-tab="renders">Рендер</button>
          </nav>
          <button type="button" class="side-fold" id="ed-side-toggle" aria-expanded="true"
            aria-controls="ed-side-body" title="Свернуть панель: ролик и шкала станут шире">‹</button>
        </div>
        <div class="side-body" id="ed-side-body">
          <div id="ed-source" data-panel="source">
            <div id="ed-source-main"></div>
            <div id="ed-transcript"></div>
          </div>
          <div id="ed-subtitles" data-panel="subtitles" hidden></div>
          <section id="ed-renders" data-panel="renders" hidden></section>
        </div>
      </section>
      <section>
        <div class="stage" id="ed-stage"></div>
        <!-- Три группы, разделённые чертой: просмотр, правка шкалы, каким выйдет файл. Раньше
             все четырнадцать кнопок стояли одной строкой одинаковой громкости, и было не видно,
             что настройка кадра и разрез клипа — разговоры о разном. -->
        <div class="row bar-edit">
          <span class="bar-group" id="ed-undo-group">
            <button id="ed-undo" type="button" disabled title="Отменить последнее действие (Ctrl+Z)">Отменить</button>
          </span>
          <span class="bar-group" id="ed-play-group">
            <button id="ed-play" type="button" title="Играть (пробел)">▶</button>
            <input id="ed-goto" class="tc" inputmode="decimal" title="Перейти к таймкоду" />
            <span class="muted" id="ed-total"></span>
          </span>
          <span class="bar-group" id="ed-edit-group">
            <button id="ed-split" type="button" title="Разрезать по курсору (S)">Разрезать</button>
            <button id="ed-copy" type="button" title="Копия клипа встанет следом (Ctrl+D)">Дублировать</button>
            <button id="ed-delete" type="button" title="Удалить выбранный клип (Del)">Удалить клип</button>
            <!-- Подпись нужна: три контрола без неё читались как «минус, ползунок, плюс» к чему угодно
                 — к громкости, к переходу, — а не к масштабу шкалы. -->
            <label class="zoom-label" for="ed-zoom">Масштаб</label>
            <button id="ed-zoom-out" type="button" title="Мельче (−)">−</button>
            <input id="ed-zoom" class="zoom" type="range" min="0" max="100" step="1"
              title="Масштаб шкалы" />
            <button id="ed-zoom-in" type="button" title="Крупнее (+)">+</button>
          </span>
          <span class="bar-group" id="ed-help-group">
            <button id="ed-help" type="button" title="Показать список (?)"
              aria-expanded="false">Горячие клавиши</button>
          </span>
          <span class="bar-group" id="ed-out-group">
            <select id="ed-aspect" title="Пропорция кадра">
              <option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option>
            </select>
            <select id="ed-fit" title="Вписывание">
              <option value="pad">поля</option>
              <option value="crop">обрезка</option>
            </select>
            <select id="ed-fps" title="Кадры в секунду">
              <option value="25">25 к/с</option>
              <option value="30">30 к/с</option>
              <option value="50">50 к/с</option>
              <option value="60">60 к/с</option>
            </select>
            <label class="burn">
              <input id="ed-burn" type="checkbox" disabled />
              Субтитры
            </label>
          </span>
        </div>
        <div id="ed-timeline"></div>
      </section>
      <!-- Свойства выбранного — справа. Раньше стояли строкой над шкалой и гасли по выбору; панель
           читается сверху вниз, показывает только то, что у куска есть, и сворачивается. -->
      <section class="props" id="ed-props">
        <div class="side-head">
          <button type="button" class="side-fold" id="ed-props-toggle" aria-expanded="true"
            aria-controls="ed-props-body" title="Свернуть свойства: сцена и шкала станут шире">›</button>
          <h3 class="props-title">Свойства</h3>
        </div>
        <div class="props-body" id="ed-props-body"></div>
      </section>
    </div>
    <div class="card keys" id="ed-keys" hidden>
      <h3 class="display-m" style="margin:0">Горячие клавиши</h3>
      <dl class="keys-list">${HOTKEYS.map(
        row => `<dt><kbd>${escapeHtml(row.keys)}</kbd></dt><dd>${escapeHtml(row.what)}</dd>`,
      ).join('')}</dl>
    </div>
    <pre id="ed-error" hidden></pre>`

  const nameBox = el.querySelector('#ed-name') as HTMLElement
  const stateBox = el.querySelector('#ed-state') as HTMLElement
  const noticeBox = el.querySelector('#ed-notice') as HTMLElement
  const stage = el.querySelector('#ed-stage') as HTMLElement
  const totalBox = el.querySelector('#ed-total') as HTMLElement
  const errorBox = el.querySelector('#ed-error') as HTMLPreElement
  const aspectPick = el.querySelector('#ed-aspect') as HTMLSelectElement
  const fitPick = el.querySelector('#ed-fit') as HTMLSelectElement
  const fpsPick = el.querySelector('#ed-fps') as HTMLSelectElement
  const propsBody = el.querySelector('#ed-props-body') as HTMLElement
  const propsToggle = el.querySelector('#ed-props-toggle') as HTMLButtonElement
  const history = createHistory<ProjectDoc>(5)
  const undoButton = el.querySelector('#ed-undo') as HTMLButtonElement
  const playButton = el.querySelector('#ed-play') as HTMLButtonElement
  const splitButton = el.querySelector('#ed-split') as HTMLButtonElement
  const copyButton = el.querySelector('#ed-copy') as HTMLButtonElement
  const deleteButton = el.querySelector('#ed-delete') as HTMLButtonElement
  const zoomInButton = el.querySelector('#ed-zoom-in') as HTMLButtonElement
  const zoomSlider = el.querySelector('#ed-zoom') as HTMLInputElement
  const zoomOutButton = el.querySelector('#ed-zoom-out') as HTMLButtonElement
  const helpButton = el.querySelector('#ed-help') as HTMLButtonElement
  const keysCard = el.querySelector('#ed-keys') as HTMLElement
  const gotoInput = el.querySelector('#ed-goto') as HTMLInputElement
  const burnBox = el.querySelector('#ed-burn') as HTMLInputElement
  const saves = el.querySelector('#ed-saves') as HTMLElement
  const savesToggle = el.querySelector('#ed-saves-toggle') as HTMLButtonElement
  const grid = el.querySelector('#ed-grid') as HTMLElement
  const sideBody = el.querySelector('#ed-side-body') as HTMLElement
  const sideToggle = el.querySelector('#ed-side-toggle') as HTMLButtonElement
  const savesPanel = el.querySelector('#ed-saves-panel') as HTMLElement

  let project: Project | null = null
  let assets = new Map<string, AssetInfo>()
  let assetList: Asset[] = []
  // Первый список сравнивать не с чем: без этого флага точка «пришла расшифровка» зажигалась
  // бы на каждом открытии проекта с уже расшифрованным файлом.
  let assetsSeen = false
  // Буфер клипа живёт в странице, а не в системном буфере: класть туда кусок шкалы нечем —
  // это не текст и не файл, а строка документа проекта, понятная только этому редактору.
  let clipboard: Clip | null = null
  const dataCache = new Map<string, Promise<AssetData>>()
  const data = new Map<string, AssetData>()
  let playing = false
  /** Одна кнопка на два состояния: без смены значка непонятно, идёт просмотр или стоит. */
  function setPlaying(value: boolean): void {
    playing = value
    playButton.textContent = value ? '⏸' : '▶'
    playButton.title = value ? 'Пауза (пробел)' : 'Играть (пробел)'
  }
  let playIndex = 0
  let timelineTime = 0
  let stopped = false
  let versions: { refresh: () => Promise<void> } | null = null
  let renders: {
    stop: () => void
    setDoc: (doc: ProjectDoc, name?: string) => void
    setAssets: (list: Asset[]) => void
  } | null = null
  let assetTimer = 0
  const analysisCache = new Map<string, { start: number; end: number }[] | null>()
  const analysisPending = new Set<string>()

  /**
   * Кто сейчас расшифровывается — одна правда на обе панели.
   *
   * Расшифровка принадлежит записи, а не вкладке: её заказывают и из «Текста» в исходниках, и из
   * субтитров. Пока каждая панель знала только своё задание, вторая в ту же секунду предлагала
   * начать заново — и «работало» это лишь потому, что сервер отвечал already_queued.
   */
  const transcribing = new Set<string>()
  function markTranscribing(assetId: string, running: boolean): void {
    if (running === transcribing.has(assetId)) return
    if (running) transcribing.add(assetId)
    else transcribing.delete(assetId)
    transcript.setTranscribing(transcribing)
    subtitles.setTranscribing(transcribing)
  }

  function markNews(name: string): void {
    if (tab === name) return
    tabsBar.querySelector<HTMLButtonElement>(`.tab[data-tab="${name}"]`)?.classList.add('news')
  }

  function applyAssets(list: Asset[]): void {
    const first = !assetsSeen
    assetsSeen = true
    const previous = new Map(assetList.map(a => [a.id, a]))
    assetList = list
    assets = new Map(
      list.map(a => [
        a.id,
        { kind: a.kind, hasAudio: a.has_audio ?? null, duration: a.duration, files: { thumbs: a.files.thumbs } },
      ]),
    )
    source.setAssets(list)
    // Панель исходника при том же выбранном файле обработчик не дёргает, поэтому свежую карточку
    // в панель текста передаём сами. Иначе доехавшая расшифровка (её мог заказать другой экран,
    // вторая вкладка или агент) не доходила бы до неё никогда: у панели свой опрос выключен, и
    // она вечно писала бы «Расшифровки ещё нет».
    const picked = source.current()
    if (picked) transcript.setAsset(list.find(a => a.id === picked.id) ?? null)
    renders?.setAssets(list)
    if (project) {
      const ids = clipAssetIds(project.doc.clips)
      for (const id of ids) {
        const hadTranscript = Boolean(previous.get(id)?.files.transcript)
        const hasTranscript = Boolean(list.find(a => a.id === id)?.files.transcript)
        if (hasTranscript && !hadTranscript && !first) markNews('subtitles')
      }
      subtitles.setTimeline(project.doc.clips, list)
    }
    // Свойствам выбранного нужны записи: есть ли у клипа звук, картинка ли наложение.
    syncSelection()
  }

  function pollAssets(): void {
    window.clearTimeout(assetTimer)
    if (stopped) return
    // Опрос живёт, пока открыт редактор: расшифровку заказывают уже после того, как файлы
    // дошли до proxy_ready, и без повторного тика вкладка субтитров об этом не узнает.
    // Когда ждать нечего, шаг растягиваем: раньше открытая вкладка редактора стучалась в сервер
    // сорок раз в минуту вхолостую, а ВМ у нас общая с двумя соседями.
    const soon = needsPolling(assetList) || subtitles.busy() || transcript.busy()
    assetTimer = window.setTimeout(() => {
      void listProjectAssets(projectId)
        .then(r => {
          if (stopped) return
          applyAssets(r.assets)
          pollAssets()
        })
        .catch(() => {
          if (!stopped) pollAssets()
        })
    }, soon ? POLL_MS : IDLE_POLL_MS)
  }

  /** Запомнить состояние ДО правки: именно к нему вернёт кнопка «Отменить». */
  function remember(): void {
    if (project) history.push(project.doc)
    undoButton.disabled = !history.canUndo()
  }

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  // Таймер держим за ручку: без неё сообщение, показанное секунду назад, гасил чужой таймер от
  // предыдущего — человек видел подсказку меньше секунды и решал, что её не было.
  /** Сказанное про шкалу говорим под шкалой, а не в шапке: там на это смотрят. */
  const underTrack = (text: string) => timeline.say(text)

  let noticeTimer = 0
  const notice = (text: string) => {
    noticeBox.textContent = text
    window.clearTimeout(noticeTimer)
    if (text) noticeTimer = window.setTimeout(() => (noticeBox.textContent = ''), 6000)
  }

  function showTime(): void {
    if (document.activeElement !== gotoInput) {
      gotoInput.value = formatTimecode(timelineTime)
      gotoInput.classList.remove('bad')
    }
    totalBox.textContent = project ? `из ${formatTimecode(totalDuration(project.doc.clips))}` : ''
    // Панель субтитров подсвечивает реплику, которая сейчас в кадре: время шкалы обновляется
    // отсюда при любом движении курсора — и при воспроизведении, и при перемотке.
    subtitles.setTime(timelineTime)
  }

  const saver = createSaver({
    onSaved: saved => {
      if (stopped) return
      errorBox.hidden = true
      project = saved
      render()
    },
    onConflict: fresh => {
      project = fresh
      history.clear()
      undoButton.disabled = true
      render()
      notice('Проект изменился в другом месте, показана свежая версия')
    },
    onInvalid: (errors: FieldError[]) => {
      // Причина не гаснет по таймеру: отклонённый документ надо поправить, а не переждать.
      errorBox.hidden = false
      errorBox.textContent = `Не сохранено: ${errors.map(e => `${e.field} — ${e.message}`).join('; ')}`
    },
    onError: showError,
    onStateChange: state => (stateBox.textContent = STATE_TEXT[state]),
  })

  // Плеер склейки: активный элемент играет, скрытый держит следующий клип на его точке входа.
  const videoA = document.createElement('video')
  const videoB = document.createElement('video')
  let active = videoA
  ;[videoA, videoB].forEach(v => {
    v.preload = 'auto'
    v.playsInline = true
    stage.appendChild(v)
  })
  videoB.style.display = 'none'
  // Слой наложений поверх обоих элементов основы. У каждого наложения свой <video>: прокси
  // картинки — неподвижный ролик, поэтому картинки и видео идут одним путём.
  const overlayLayer = document.createElement('div')
  overlayLayer.className = 'ov-layer'
  stage.appendChild(overlayLayer)

  const proxyOf = (assetId: string): string | null => assetList.find(a => a.id === assetId)?.files.proxy ?? null

  // Звуки дорожки в превью: по элементу audio на звук, а не на запись — два куска одной записи
  // могут звучать одновременно. Часы у них свои, поэтому на каждом тике сверяем с временем
  // шкалы и подводим, если разошлись заметно: мелкий разнобой на слух не слышен, а перемотка
  // на каждом тике щёлкала бы.
  const SOUND_DRIFT_SEC = 0.3
  const soundPlayers = new Map<string, HTMLAudioElement>()

  function syncSounds(): void {
    if (!project) return
    const sounds = soundsOf(project.doc)
    const cues = playing ? soundPlan(sounds, timelineTime, totalDuration(project.doc.clips)) : []
    const live = new Set(cues.map(cue => cue.id))
    for (const [id, player] of soundPlayers) {
      if (!live.has(id) && !player.paused) player.pause()
      if (!sounds.some(sound => sound.id === id)) {
        player.removeAttribute('src')
        soundPlayers.delete(id)
      }
    }
    // Приглушение считается один раз на тик: кто говорит, известно по клипу под курсором.
    const duck = cues.some(cue => cue.duck) ? duckFactor(speechDucking()) : 1
    for (const cue of cues) {
      const src = proxyOf(cue.assetId)
      if (!src) continue
      let player = soundPlayers.get(cue.id)
      if (!player) {
        player = new Audio()
        player.preload = 'auto'
        soundPlayers.set(cue.id, player)
      }
      if (!player.src.endsWith(src)) player.src = src
      if (Math.abs(player.currentTime - cue.time) > SOUND_DRIFT_SEC) player.currentTime = cue.time
      player.volume = previewClipVolume(cue.gain * (cue.duck ? duck : 1))
      if (player.paused) void player.play().catch(() => {})
    }
  }

  function pauseSounds(): void {
    soundPlayers.forEach(player => {
      if (!player.paused) player.pause()
    })
  }

  /**
   * Наложения в превью: по <video> на наложение поверх сцены, в коробке своего пресета.
   *
   * Показываются и на паузе — это картинка, а не звук: человек ставит курсор и смотрит, что
   * лежит поверх кадра. Часы у элементов свои, поэтому на каждом тике подводим их к времени
   * шкалы, как звуки. Прозрачность — появление и исчезновение, посчитанные по тем же полям,
   * что в сборке. Порядок в списке — порядок слоёв: последнее наложение сверху.
   */
  const overlayPlayers = new Map<string, HTMLVideoElement>()

  function syncOverlays(): void {
    if (!project) return
    const overlays = overlaysOf(project.doc)
    const cues = overlayPlan(overlays, timelineTime, totalDuration(project.doc.clips))
    const live = new Set(cues.map(cue => cue.id))
    for (const [id, player] of overlayPlayers) {
      if (!overlays.some(overlay => overlay.id === id)) {
        player.remove()
        player.removeAttribute('src')
        overlayPlayers.delete(id)
      } else if (!live.has(id)) {
        if (!player.paused) player.pause()
        player.hidden = true
      }
    }
    const aspect = aspectRatio(project.doc.output.aspect)
    const shown: HTMLVideoElement[] = []
    for (const cue of cues) {
      const overlay = overlays.find(item => item.id === cue.id)
      const src = overlay ? proxyOf(overlay.asset_id) : null
      if (!overlay || !src) continue
      let player = overlayPlayers.get(cue.id)
      if (!player) {
        player = document.createElement('video')
        player.className = 'ov'
        player.preload = 'auto'
        player.playsInline = true
        overlayPlayers.set(cue.id, player)
      }
      if (!player.src.endsWith(src)) player.src = src
      const box = overlayBox(overlay.place, overlay.size, aspect)
      player.style.left = `${box.left}%`
      player.style.top = `${box.top}%`
      player.style.width = `${box.width}%`
      player.style.height = `${box.height}%`
      player.style.objectPosition = box.align
      player.style.opacity = String(cue.opacity)
      player.hidden = false
      player.muted = cue.volume <= 0
      player.volume = previewClipVolume(cue.volume)
      if (Math.abs(player.currentTime - cue.time) > SOUND_DRIFT_SEC) player.currentTime = cue.time
      if (playing && player.paused) void player.play().catch(() => {})
      if (!playing && !player.paused) player.pause()
      shown.push(player)
    }
    // Переставляем только когда порядок разошёлся: перестановка на каждом тике — лишняя работа.
    const inLayer = Array.from(overlayLayer.children)
    if (shown.some((player, i) => inLayer[i] !== player)) shown.forEach(player => overlayLayer.appendChild(player))
  }

  function pauseOverlays(): void {
    overlayPlayers.forEach(player => {
      if (!player.paused) player.pause()
    })
  }

  function swap(): void {
    const hidden = active === videoA ? videoB : videoA
    active.pause()
    active.style.display = 'none'
    active.style.opacity = '1'
    hidden.style.display = ''
    hidden.style.opacity = '1'
    active = hidden
  }

  function prepareNext(index: number): void {
    const clips = project?.doc.clips ?? []
    const next = clips[index + 1]
    const hidden = active === videoA ? videoB : videoA
    if (!next) return
    const src = proxyOf(next.asset_id)
    if (!src) return
    if (!hidden.src.endsWith(src)) hidden.src = src
    hidden.currentTime = next.in
  }

  function applyIncoming(incoming?: Incoming, fromTick = false): void {
    const hidden = active === videoA ? videoB : videoA
    if (!incoming) {
      active.style.opacity = '1'
      hidden.style.display = 'none'
      hidden.style.opacity = '0'
      if (!hidden.paused) hidden.pause()
      return
    }
    const src = proxyOf(incoming.assetId)
    if (src && !hidden.src.endsWith(src)) hidden.src = src
    hidden.style.display = ''
    active.style.opacity = String(1 - incoming.mix)
    hidden.style.opacity = String(incoming.mix)
    if (!fromTick) hidden.currentTime = incoming.time
    if (playing && hidden.paused) void hidden.play().catch(() => {})
  }

  function seek(time: number): void {
    const clips = project?.doc.clips ?? []
    const plan = seekPlan(clips, time)
    if (!plan) return
    const src = proxyOf(plan.assetId)
    if (!src) {
      underTrack('Файл ещё обрабатывается, перемотка недоступна')
      return
    }
    playIndex = plan.index
    timelineTime = plan.timelineTime
    if (!active.src.endsWith(src)) active.src = src
    active.currentTime = plan.time
    timeline.setPlayhead(timelineTime)
    showTime()
    prepareNext(plan.index)
    applyIncoming(plan.incoming)
    applyPreviewVolumes()
  }

  // Слушатель общий для обоих элементов video: после свопа активным становится другой элемент,
  // а обработчик, повешенный один раз на конкретный узел, со свопом не переезжает. Проверка
  // currentTarget === active отсекает случайный тик от скрытого элемента (например, после
  // программной перестановки currentTime в prepareNext).
  function onTimeUpdate(event: Event): void {
    if (event.currentTarget !== active) return
    if (!project) return
    const plan = stepPlan(project.doc.clips, { index: playIndex, sourceTime: active.currentTime })
    timelineTime = plan.timelineTime
    timeline.setPlayhead(timelineTime)
    showTime()
    if (plan.kind === 'playing') applyIncoming(plan.incoming, true)
    if (plan.kind === 'advance') {
      if (!proxyOf(plan.assetId)) {
        // У следующего клипа ещё нет прокси: показывать пустой кадр хуже, чем честно встать.
        setPlaying(false)
        active.pause()
        pauseSounds()
        pauseOverlays()
        underTrack('Следующий файл ещё обрабатывается, воспроизведение остановлено')
        return
      }
      swap()
      playIndex = plan.index
      active.currentTime = plan.time
      if (playing) void active.play().catch(() => {})
      prepareNext(plan.index)
    } else if (plan.kind === 'end') {
      setPlaying(false)
      const clip = project.doc.clips[playIndex]
      if (clip) active.currentTime = clip.out
      active.pause()
      applyIncoming()
    }
    applyPreviewVolumes()
  }
  videoA.addEventListener('timeupdate', onTimeUpdate)
  videoB.addEventListener('timeupdate', onTimeUpdate)

  const timeline = mountTimeline(el.querySelector('#ed-timeline') as HTMLElement, {
    onChange: applyClips,
    onSoundsChange: applySounds,
    onOverlaysChange: applyOverlays,
    onSeek: seek,
    onSelect: syncSelection,
  })

  /** Кусок исходника в конец шкалы: приходит и от полосы файла, и от выделения в тексте. */
  function addClip(assetId: string, from: number, to: number, snap: boolean): void {
    const clips = project?.doc.clips ?? []
    const clip: Clip = {
      id: newClipId(clips),
      asset_id: assetId,
      in: ms(from),
      out: ms(to),
      volume: 1,
      snap_to_pauses: snap,
      in_verified: false,
      out_verified: false,
    }
    const next = insertClip(clips, clip)
    applyClips(next)
    // Кусок встаёт в конец шкалы — часто за краем видимого. Молчать нельзя: человек решает, что
    // нажатие не сработало, и кладёт второй раз. Ставим курсор на его начало: шкала подтягивает
    // прокрутку сама, а на сцене сразу первый кадр добавленного.
    selectClip(clip.id)
    seek(timelineStart(next, next.length - 1))
    underTrack('Кусок на шкале')
  }

  /**
   * Положить звук на его дорожку под курсор.
   *
   * Под курсор, а не в конец: звук кладут под определённое место картинки — озвучить этот кадр,
   * подложить шум под эту сцену, — и курсор человек ставит туда заранее.
   */
  function addSound(assetId: string, from: number, to: number): void {
    if (!project) return
    const sounds = soundsOf(project.doc)
    const sound: Sound = {
      id: newSoundId(project.doc.clips, sounds),
      asset_id: assetId,
      at: ms(timelineTime),
      in: ms(from),
      out: ms(to),
      volume: 1,
      loop: false,
      duck: false,
      fade_in: 0,
      fade_out: 0,
    }
    applySounds([...sounds, sound])
    selectClip(sound.id)
    underTrack(`Звук на звуковой дорожке с ${timelineTime.toFixed(1)} с`)
  }

  /** Выбранный звук, если выбран звук, а не клип: выделение у дорожек общее. */
  function chosenSound(): Sound | null {
    const id = timeline.selected()
    if (!project || !id) return null
    return soundsOf(project.doc).find(sound => sound.id === id) ?? null
  }

  /**
   * Положить наложение поверх основы под курсор.
   *
   * По умолчанию во весь кадр: картинку поверх речи чаще всего кладут как перебивку, а угол,
   * размер и появление выбирают потом в строке свойств. Звук выключен, пока его не включат.
   */
  function addOverlay(assetId: string, from: number, to: number): void {
    if (!project) return
    const overlays = overlaysOf(project.doc)
    const overlay: Overlay = {
      id: newOverlayId(project.doc.clips, soundsOf(project.doc), overlays),
      asset_id: assetId,
      at: ms(timelineTime),
      in: ms(from),
      out: ms(to),
      place: 'full',
      size: OVERLAY_SIZE_DEFAULT,
      volume: 0,
      fade_in: 0,
      fade_out: 0,
    }
    applyOverlays([...overlays, overlay])
    selectClip(overlay.id)
    underTrack(`Наложение поверх основы с ${timelineTime.toFixed(1)} с`)
  }

  /** Выбранное наложение, если выбрано оно: выделение у всех дорожек общее. */
  function chosenOverlay(): Overlay | null {
    const id = timeline.selected()
    if (!project || !id) return null
    return overlaysOf(project.doc).find(overlay => overlay.id === id) ?? null
  }

  /** Честный отказ вместо молчания: со звуком и наложением пока умеем не всё, что с клипом. */
  function laneCannot(): void {
    underTrack(
      chosenOverlay()
        ? 'Наложение пока можно двигать, менять ему место, размер, появление и удалять'
        : 'Звук пока можно двигать, менять ему громкость и удалять',
    )
  }

  const sourceMain = el.querySelector('#ed-source-main') as HTMLElement
  // Монтаж по тексту стоит под плеером исходника: слова выбирают на слух по нему, а сцена справа
  // играет уже собранный ролик. Времена слов интерполированы (±0.3 с), поэтому клип ставим со
  // snap_to_pauses — настоящий рез подтянет сервер по измеренным паузам. С полосы исходника кусок
  // берут по таймкоду, там подтягивать нечего, и snap выключен.
  const transcript = mountTranscript(el.querySelector('#ed-transcript') as HTMLElement, {
    onTranscribe: markTranscribing,
    onSeek: seconds => source.seek(seconds),
    onTake: (from, to) => {
      const asset = source.current()
      if (asset) addClip(asset.id, from, to, true)
    },
    onFold: () => setFold(openFold === 'words' ? null : 'words'),
  })
  const source = mountSource(sourceMain, {
    // Звук идёт на свою дорожку под курсор, видео и картинка — в очередь клипов.
    onAdd: (asset, range) =>
      asset.kind === 'audio'
        ? addSound(asset.id, range.from, range.to)
        : addClip(asset.id, range.from, range.to, false),
    onOverlay: (asset, range) => addOverlay(asset.id, range.from, range.to),
    onPick: asset => transcript.setAsset(asset),
    onTime: seconds => transcript.setTime(seconds),
    onFold: () => setFold(openFold === 'cut' ? null : 'cut'),
  })

  /**
   * Способ отрезать кусок: по времени или по словам. Открыт ровно один или ни одного.
   *
   * Файл у обоих способов общий, и два развёрнутых инструмента над одним плеером читались бы
   * как две независимые заготовки. Щелчок по открытому заголовку закрывает его и не открывает
   * соседа: свернув оба, человек получает панель, где виден только выбор файла и плеер.
   */
  type Fold = 'cut' | 'words'
  let openFold: Fold | null = null

  function setFold(next: Fold | null): void {
    openFold = next
    source.setFold(next === 'cut')
    transcript.setFold(next === 'words')
    savePref(FOLD_KEY, next ?? 'none')
  }

  const savedFold = readPref(FOLD_KEY, 'cut')
  setFold(savedFold === 'words' ? 'words' : savedFold === 'none' ? null : 'cut')

  const subtitles = mountSubtitles(el.querySelector('#ed-subtitles') as HTMLElement, projectId, {
    onChange: cues => applySubtitles(cues),
    onProject: fresh => {
      remember()
      project = fresh
      render()
      notice('Реплики собраны')
    },
    flush: async () => {
      if (project && saver.pending()) await saver.flush(project)
    },
    onSeek: seconds => {
      timelineTime = Math.max(0, seconds)
      seek(timelineTime)
      timeline.setPlayhead(timelineTime)
      showTime()
    },
    onTranscribe: markTranscribing,
  })

  /** Правка реплик — обычная правка документа: с откатом, точками сохранения и автосохранением. */
  function applySubtitles(cues: Cue[]): void {
    if (!project) return
    remember()
    const было = project.doc.subtitles
    const subs = patchCues(было, cues)
    const previous = было?.cues ?? []
    // Текст живёт в textarea до блюра: полный render сотрёт набор в соседней карточке.
    // Времена и число реплик меняют вёрстку — там перерисовка нужна.
    const textOnly =
      cues.length === previous.length
      && cues.every((c, i) => {
        const prev = previous[i]
        return prev !== undefined && c.start === prev.start && c.end === prev.end
      })
    project = { ...project, doc: { ...project.doc, subtitles: subs } }
    if (textOnly) subtitles.adopt(project)
    else render()
    saver.schedule(project)
  }

  function syncBurn(): void {
    const subs = project?.doc.subtitles
    burnBox.disabled = !cuesReady(subs)
    burnBox.checked = burnEnabled(subs)
  }

  function applyEnabled(on: boolean): void {
    if (!project || !cuesReady(project.doc.subtitles)) return
    remember()
    project = {
      ...project,
      doc: { ...project.doc, subtitles: { ...project.doc.subtitles, enabled: on } },
    }
    syncBurn()
    saver.schedule(project)
  }

  burnBox.addEventListener('change', () => applyEnabled(burnBox.checked))

  /* ═══ Вкладки левой колонки ═══════════════════════════════════════════════
   *
   * Одна вкладка на экране. Исходники и музыка только на «Исходниках».
   * «Субтитры» и «Рендер» серые, пока нет клипа; рендер жив, если уже есть готовый файл.
   */
  const tabsBar = el.querySelector('#ed-tabs') as HTMLElement
  const panels = new Map<string, HTMLElement>(
    Array.from(el.querySelectorAll<HTMLElement>('[data-panel]')).map(node => [node.dataset.panel ?? '', node]),
  )
  const mounted = new Set<string>(['source'])
  let tab: EditorTab = 'source'
  let hasReadyRender = false
  let booted = false

  function clipCount(): number {
    return project?.doc.clips.length ?? 0
  }

  function syncTabs(): void {
    const clips = clipCount()
    tabsBar.querySelectorAll<HTMLButtonElement>('.tab').forEach(button => {
      const name = (button.dataset.tab ?? 'source') as EditorTab
      button.disabled = !tabEnabled(name, clips, hasReadyRender)
    })
    // showTab зовём всегда, а не только при смене вкладки: он же ставит класс .on, и без этого
    // открытый редактор показывал панель «Исходники», не подсветив ни одной кнопки.
    showTab(resolveTab(tab, clips, hasReadyRender))
  }

  function openPanel(name: string): void {
    if (mounted.has(name) || !booted) return
    mounted.add(name)
    if (name === 'renders') {
      renders = mountRender(
        panels.get('renders') as HTMLElement,
        projectId,
        async () => {
          if (project && saver.pending()) await saver.flush(project)
        },
        () => markNews('renders'),
        n => {
          hasReadyRender = n > 0
          syncTabs()
        },
      )
      if (project) renders.setDoc(project.doc, project.name)
      // Записи нужны подписи о битрейте и кадре исходников: панель рождается позже списка.
      renders.setAssets(assetList)
    }
  }

  function showTab(name: EditorTab): void {
    if (!tabEnabled(name, clipCount(), hasReadyRender)) return
    tab = name
    panels.forEach((panel, key) => {
      panel.hidden = key !== name
    })
    tabsBar.querySelectorAll<HTMLButtonElement>('.tab').forEach(button => {
      button.classList.toggle('on', button.dataset.tab === name)
      if (button.dataset.tab === name) button.classList.remove('news')
    })
    openPanel(name)
  }

  tabsBar.querySelectorAll<HTMLButtonElement>('.tab').forEach(button =>
    button.addEventListener('click', () => {
      if (button.disabled) return
      showTab((button.dataset.tab ?? 'source') as EditorTab)
    }),
  )
  syncTabs()

  function closeSaves(): void {
    savesPanel.hidden = true
    savesToggle.setAttribute('aria-expanded', 'false')
    document.removeEventListener('pointerdown', onSavesPointerDown, true)
  }

  function onSavesPointerDown(event: PointerEvent): void {
    if (!saves.contains(event.target as Node)) closeSaves()
  }

  /**
   * Свернуть левую колонку: ролик и шкала занимают её место, сцена становится выше.
   *
   * Монтируют, глядя на шкалу и на кадр, а исходники нужны только когда берут новый кусок.
   * Кнопка остаётся на месте свёрнутой панели — иначе развернуть её было бы нечем.
   */
  function setSide(open: boolean): void {
    grid.classList.toggle('side-off', !open)
    sideBody.hidden = !open
    sideToggle.textContent = open ? '‹' : '›'
    sideToggle.setAttribute('aria-expanded', String(open))
    sideToggle.title = open
      ? 'Свернуть панель: ролик и шкала станут шире'
      : 'Развернуть панель исходников'
    savePref(SIDE_KEY, open ? 'on' : 'off')
  }

  sideToggle.addEventListener('click', () => setSide(sideBody.hidden))
  setSide(readPref(SIDE_KEY, 'on') !== 'off')

  savesToggle.addEventListener('click', () => {
    if (!savesPanel.hidden) {
      closeSaves()
      return
    }
    if (!versions) {
      versions = mountVersions(
        savesPanel,
        projectId,
        restored => {
          remember()
          project = restored
          timelineTime = 0
          render()
          if (playing) seek(0)
          notice('Вернулись к сохранённой точке')
          closeSaves()
        },
        async () => {
          if (project && saver.pending()) await saver.flush(project)
        },
      )
    } else {
      void versions.refresh()
    }
    savesPanel.hidden = false
    savesToggle.setAttribute('aria-expanded', 'true')
    document.addEventListener('pointerdown', onSavesPointerDown, true)
  })

  function applyClips(clips: Clip[]): void {
    if (!project) return
    remember()
    project = { ...project, doc: { ...project.doc, clips: clampTransitions(clips) } }
    render()
    saver.schedule(project)
    // Список изменился под играющим клипом: номер клипа больше ничего не значит, встаём заново
    // по времени шкалы. Иначе плеер продолжил бы мерить время удалённого клипа.
    if (playing) seek(Math.min(timelineTime, totalDuration(clips)))
  }

  /** Правка звуковой дорожки: как у клипов — в историю, на шкалу, в сохранение и в превью. */
  function applySounds(sounds: Sound[]): void {
    if (!project) return
    remember()
    project = { ...project, doc: { ...project.doc, sounds } }
    render()
    saver.schedule(project)
  }

  /** Правка наложений: тот же путь, что у звуков. */
  function applyOverlays(overlays: Overlay[]): void {
    if (!project) return
    remember()
    project = { ...project, doc: { ...project.doc, overlays } }
    render()
    saver.schedule(project)
  }

  function parseSilences(data: unknown): { start: number; end: number }[] {
    if (!data || typeof data !== 'object' || !('silences' in data)) return []
    const raw = (data as { silences: unknown }).silences
    if (!Array.isArray(raw)) return []
    const out: { start: number; end: number }[] = []
    for (const item of raw) {
      if (!item || typeof item !== 'object') continue
      const start = (item as { start?: unknown }).start
      const end = (item as { end?: unknown }).end
      if (typeof start === 'number' && typeof end === 'number') out.push({ start, end })
    }
    return out
  }

  async function ensureAnalysis(assetId: string): Promise<void> {
    if (analysisCache.has(assetId) || analysisPending.has(assetId)) return
    const url = assetList.find(a => a.id === assetId)?.files.analysis
    if (!url) {
      analysisCache.set(assetId, null)
      return
    }
    analysisPending.add(assetId)
    try {
      const response = await fetch(url)
      if (!response.ok) {
        analysisCache.set(assetId, null)
        return
      }
      analysisCache.set(assetId, parseSilences(await response.json()))
      if (!stopped) applyPreviewVolumes()
    } catch {
      analysisCache.set(assetId, null)
    } finally {
      analysisPending.delete(assetId)
    }
  }

  function applyPreviewVolumes(): void {
    if (!project) return
    // Звуки и наложения сверяются здесь же: этот вызов стоит на каждом тике, перемотке и пуске.
    syncSounds()
    syncOverlays()
    const clips = project.doc.clips
    const found = clipAt(clips, timelineTime)
    const hidden = active === videoA ? videoB : videoA
    const incoming = incomingAt(clips, timelineTime)
    const mix = incoming?.mix ?? 0
    active.volume = previewClipVolume(found?.clip.volume ?? 1) * (1 - mix)
    const incomingClip = incoming ? clips[incoming.index] : clips[playIndex + 1]
    hidden.volume = previewClipVolume(incomingClip?.volume ?? 1) * (incoming ? mix : 1)
  }

  /** Карта пауз клипа под курсором: по ней фон приглушается под речь и отпускается в паузах. */
  function speechDucking(): Ducking | null {
    if (!project) return null
    const found = clipAt(project.doc.clips, timelineTime)
    if (!found) return null
    const map = analysisCache.get(found.clip.asset_id)
    if (map === undefined) {
      void ensureAnalysis(found.clip.asset_id)
      return null
    }
    return map ? { sourceTime: found.clip.in + found.offset, silences: map } : null
  }

  /**
   * Что сейчас можно, а что нет. Гасим, но не прячем.
   *
   * Появляющиеся и исчезающие кнопки переставляют панель под руками и не дают её выучить: место
   * контрола должно быть постоянным, а доступность — меняться. Серая кнопка ещё и отвечает на
   * вопрос «почему нельзя» — исчезнувшая не отвечает ни на что.
   */
  function syncBar(): void {
    const has = clipCount() > 0
    const chosen = Boolean(timeline.selected())
    playButton.disabled = !has
    gotoInput.disabled = !has
    splitButton.disabled = !has
    zoomInButton.disabled = !has
    zoomOutButton.disabled = !has
    const sound = chosenSound()
    const overlay = chosenOverlay()
    copyButton.disabled = !chosen || sound !== null || overlay !== null
    deleteButton.disabled = !chosen
  }

  /** Волны и кадры записей шкалы: клипов, звуков и наложений. */
  async function ensureData(clips: { asset_id: string }[]): Promise<void> {
    const ids = new Set(clips.map(c => c.asset_id))
    await Promise.all(
      Array.from(ids).map(async id => {
        if (data.has(id)) return
        const asset = assetList.find(a => a.id === id)
        if (!asset) return
        const files = asset.files as { peaks?: string | null; thumbs_meta?: string | null }
        const loaded = await assetData(id, { peaks: files.peaks ?? null, thumbs_meta: files.thumbs_meta ?? null }, dataCache)
        data.set(id, loaded)
      }),
    )
    if (!stopped) timeline.render({ data })
  }

  function render(): void {
    if (!project) return
    nameBox.textContent = project.name
    aspectPick.value = project.doc.output.aspect
    fitPick.value = project.doc.output.fit
    fpsPick.value = String(project.doc.output.fps)
    const ratio = aspectRatio(project.doc.output.aspect)
    stage.style.aspectRatio = String(ratio)
    stage.style.maxWidth = `calc(${ratio} * var(--stage-h))`
    stage.classList.toggle('crop', project.doc.output.fit === 'crop')
    timeline.render({
      clips: project.doc.clips,
      sounds: soundsOf(project.doc),
      overlays: overlaysOf(project.doc),
      assets,
      data,
    })
    timeline.setPlayhead(timelineTime)
    subtitles.setProject(project)
    subtitles.setTimeline(project.doc.clips, assetList)
    syncBurn()
    syncSelection()
    showTime()
    applyPreviewVolumes()
    renders?.setDoc(project.doc, project.name)
    void ensureData([...project.doc.clips, ...soundsOf(project.doc), ...overlaysOf(project.doc)])
    syncTabs()
  }

  playButton.addEventListener('click', () => {
    if (!project || !project.doc.clips.length) return
    if (playing) {
      setPlaying(false)
      active.pause()
      const hidden = active === videoA ? videoB : videoA
      hidden.pause()
      pauseSounds()
      pauseOverlays()
      return
    }
    const plan = resumePlan(project.doc.clips, { index: playIndex, sourceTime: active.currentTime })
    if (plan.kind === 'stop') {
      const clip = project.doc.clips[playIndex]
      if (clip) active.currentTime = clip.out
      timelineTime = plan.timelineTime
      timeline.setPlayhead(timelineTime)
      showTime()
      applyIncoming()
      return
    }
    setPlaying(true)
    if (plan.kind === 'advance') {
      const src = proxyOf(plan.assetId)
      if (!src) {
        setPlaying(false)
        underTrack('Следующий файл ещё обрабатывается, воспроизведение остановлено')
        return
      }
      if (!active.src.endsWith(src)) active.src = src
      playIndex = plan.index
      timelineTime = plan.timelineTime
      active.currentTime = plan.time
      prepareNext(plan.index)
    } else if (!active.src) {
      seek(timelineTime)
    }
    void active.play().catch(showError)
    applyIncoming(incomingAt(project.doc.clips, timelineTime), true)
    applyPreviewVolumes()
  })

  /**
   * Выбрать клип и подтянуть за ним панель свойств.
   *
   * Шкала сама о выборе редактору сообщает только при нажатии мышью, поэтому всё, что выбирает
   * клип из кода — дублирование, вставка, стрелки, удаление, — обязано синхронизировать панель
   * само. Иначе строка «Выбранный клип» остаётся от прежнего: показывает громкость и переход
   * не того куска.
   */
  function selectClip(id: string | null): void {
    timeline.select(id)
    syncSelection()
  }

  /**
   * Подтянуть всё, что зависит от выбранного: панель кнопок, громкость, переход, свойства
   * наложения. Одной функцией, потому что выбор меняется из трёх мест — щелчком по шкале, из
   * кода и при перерисовке, — и однажды одно из них забыло про свойства наложения: у выбранного
   * звука список «Место» остался доступным от прежнего наложения.
   */
  function syncSelection(): void {
    syncBar()
    inspector.set(currentSelection())
  }

  /** Что выбрано на шкале — глазами панели свойств: вид, сам кусок и что у него есть. */
  function currentSelection(): Selected {
    const id = timeline.selected()
    if (!project || !id) return { kind: 'none' }
    const clips = project.doc.clips
    const index = clips.findIndex(c => c.id === id)
    if (index >= 0) {
      const clip = clips[index]
      const asset = assetList.find(a => a.id === clip.asset_id)
      return {
        kind: 'clip',
        clip,
        index,
        // Пока записи не приехали, звук считаем есть: спрятать ползунок и вернуть его хуже.
        hasAudio: asset ? asset.kind !== 'image' && asset.has_audio !== false : true,
        maxFade: index > 0 ? maxFade(clips[index - 1], clip) : 0,
      }
    }
    const sound = soundsOf(project.doc).find(s => s.id === id)
    if (sound) return { kind: 'sound', sound }
    const overlay = overlaysOf(project.doc).find(o => o.id === id)
    if (overlay) {
      const asset = assetList.find(a => a.id === overlay.asset_id)
      return { kind: 'overlay', overlay, isImage: asset?.kind === 'image' }
    }
    return { kind: 'none' }
  }

  /**
   * Одна запись в историю на всё движение ползунка: live-тики её не плодят, а первый кладёт
   * состояние до движения. Обычная правка (без live) пишет в историю как всегда.
   */
  let liveRemembered = false
  function rememberFor(live: boolean): void {
    if (live) {
      if (!liveRemembered) {
        remember()
        liveRemembered = true
      }
      return
    }
    if (!liveRemembered) remember()
    liveRemembered = false
  }

  /** Правка клипа из панели: переход перестраивает шкалу, громкость слышна сразу. */
  function patchClip(patch: Partial<Clip>, live: boolean): void {
    const id = timeline.selected()
    if (!project || !id) return
    const clips = project.doc.clips.map(clip => {
      if (clip.id !== id) return clip
      const next = { ...clip, ...patch }
      if ('transition' in patch && !patch.transition) delete next.transition
      return next
    })
    if ('transition' in patch) {
      applyClips(clips)
      return
    }
    rememberFor(live)
    project = { ...project, doc: { ...project.doc, clips } }
    applyPreviewVolumes()
    syncSelection()
    saver.schedule(project)
  }

  /** Правка звука: повтор меняет длину блока на шкале, остальное слышно сразу и так. */
  function patchSound(patch: Partial<Sound>, live: boolean): void {
    const id = timeline.selected()
    if (!project || !id) return
    const sounds = updateSound(soundsOf(project.doc), id, patch)
    if ('loop' in patch) {
      applySounds(sounds)
      return
    }
    rememberFor(live)
    project = { ...project, doc: { ...project.doc, sounds } }
    applyPreviewVolumes()
    syncSelection()
    saver.schedule(project)
  }

  /** Правка наложения: место и размер видны на сцене сразу, шкалу они не трогают. */
  function patchOverlay(patch: Partial<Overlay>, live: boolean): void {
    const id = timeline.selected()
    if (!project || !id) return
    rememberFor(live)
    project = { ...project, doc: { ...project.doc, overlays: updateOverlay(overlaysOf(project.doc), id, patch) } }
    applyPreviewVolumes()
    syncSelection()
    saver.schedule(project)
  }

  const inspector = mountInspector(propsBody, {
    onClip: patchClip,
    onSound: patchSound,
    onOverlay: patchOverlay,
    onRefuse: underTrack,
  })

  /** Свернуть панель свойств — как левую: кнопка остаётся, чтобы было чем развернуть. */
  function setProps(open: boolean): void {
    grid.classList.toggle('props-off', !open)
    propsBody.hidden = !open
    propsToggle.textContent = open ? '›' : '‹'
    propsToggle.setAttribute('aria-expanded', String(open))
    propsToggle.title = open ? 'Свернуть свойства: сцена и шкала станут шире' : 'Развернуть свойства'
    savePref(PROPS_KEY, open ? 'on' : 'off')
  }

  propsToggle.addEventListener('click', () => setProps(propsBody.hidden))
  setProps(readPref(PROPS_KEY, 'on') !== 'off')

  function splitHere(): void {
    if (!project) return
    const next = splitAt(project.doc.clips, timelineTime)
    if (next === project.doc.clips) underTrack('Здесь резать нечего: курсор на краю клипа')
    else applyClips(next)
  }

  function removeSelected(): void {
    const id = timeline.selected()
    if (!project || !id) return underTrack('Сначала выберите клип на шкале')
    if (chosenSound()) applySounds(removeSound(soundsOf(project.doc), id))
    else if (chosenOverlay()) applyOverlays(removeOverlay(overlaysOf(project.doc), id))
    else applyClips(removeClip(project.doc.clips, id))
    selectClip(null)
  }

  /** Выбранный клип и его номер: половине действий нужны оба. */
  function picked(): { clip: Clip; index: number } | null {
    const id = timeline.selected()
    const clips = project?.doc.clips ?? []
    const index = clips.findIndex(c => c.id === id)
    return index < 0 ? null : { clip: clips[index], index }
  }

  /**
   * Копия клипа для вставки: новый номер и без перехода.
   *
   * Переход не переносим: он описывает стык с тем клипом, который стоял слева от оригинала, а у
   * копии сосед другой. Оставить его — значит получить растворение из неизвестно чего.
   */
  function copyOf(clip: Clip, clips: Clip[]): Clip {
    const { transition: _drop, ...rest } = clip
    return { ...rest, id: newClipId(clips) }
  }

  function duplicateSelected(): void {
    if (!project) return
    const found = picked()
    if (!found) return underTrack('Сначала выберите клип на шкале')
    const clips = project.doc.clips
    const copy = copyOf(found.clip, clips)
    applyClips(insertClip(clips, copy, found.index + 1))
    selectClip(copy.id)
    underTrack('Клип продублирован')
  }

  function copySelected(): void {
    const found = picked()
    if (!found) return underTrack('Сначала выберите клип на шкале')
    clipboard = found.clip
    underTrack('Клип скопирован — Ctrl+V поставит копию')
  }

  function pasteClip(): void {
    if (!project || !clipboard) return underTrack('Сначала скопируйте клип: Ctrl+C')
    const clips = project.doc.clips
    // Вставляем за выбранным, а без выбора — в конец: так же, как кладут кусок из исходников.
    const found = picked()
    const copy = copyOf(clipboard, clips)
    applyClips(insertClip(clips, copy, found ? found.index + 1 : undefined))
    selectClip(copy.id)
  }

  /**
   * Подрезать край выбранного клипа под курсор.
   *
   * Курсор живёт во времени ролика, а `in`/`out` — во времени исходника, поэтому считаем сдвиг
   * от начала клипа на шкале. Курсор вне клипа резать нечему: молча подрезать до нуля хуже,
   * чем сказать об этом.
   */
  function trimToPlayhead(edge: 'in' | 'out'): void {
    if (!project) return
    const found = picked()
    if (!found) return underTrack('Сначала выберите клип на шкале')
    const clips = project.doc.clips
    const start = timelineStart(clips, found.index)
    const offset = timelineTime - start
    if (offset <= 0 || offset >= clipDuration(found.clip)) {
      return underTrack('Поставьте курсор внутри выбранного клипа')
    }
    const at = ms(found.clip.in + offset)
    const duration = assets.get(found.clip.asset_id)?.duration ?? undefined
    const next = trimClip(clips, found.clip.id, edge === 'in' ? { in: at } : { out: at }, {
      duration: duration ?? undefined,
    })
    // trimClip не отказывает, а зажимает край к минимальной длине, и прежняя проверка «вернулся
    // тот же список» не срабатывала никогда: клип молча становился стоминутным огрызком.
    const after = next.find(c => c.id === found.clip.id)
    const got = edge === 'in' ? after?.in : after?.out
    if (got === undefined || Math.abs(got - at) > 0.001) {
      return underTrack('Так клип станет короче допустимого')
    }
    applyClips(next)
  }

  /** Соседний клип: выбрать и встать на его начало, чтобы сразу видеть, о чём речь. */
  function stepClip(delta: 1 | -1): void {
    const clips = project?.doc.clips ?? []
    if (!clips.length) return
    const found = picked()
    const next = found ? found.index + delta : delta > 0 ? 0 : clips.length - 1
    const index = Math.max(0, Math.min(next, clips.length - 1))
    selectClip(clips[index].id)
    // Начало клипа на шкале лежит внутри нахлёста перехода, и там курсор принадлежит ещё
    // предыдущему клипу: сцена показала бы не тот кусок, что подсвечен. Встаём за переходом.
    seek(timelineStart(clips, index) + fadeInto(clips[index], index))
  }

  /** Границы клипов на шкале: по ним прыгает Alt со стрелкой. */
  function edges(): number[] {
    const clips = project?.doc.clips ?? []
    return [...clips.map((_, i) => timelineStart(clips, i)), totalDuration(clips)]
  }

  function stepEdge(delta: 1 | -1): void {
    const marks = edges()
    const near = delta > 0
      ? marks.find(mark => mark > timelineTime + 0.001)
      : [...marks].reverse().find(mark => mark < timelineTime - 0.001)
    if (near !== undefined) seek(near)
  }

  function showKeys(open: boolean): void {
    keysCard.hidden = !open
    helpButton.setAttribute('aria-expanded', String(open))
  }

  el.querySelector('#ed-split')!.addEventListener('click', splitHere)
  el.querySelector('#ed-copy')!.addEventListener('click', duplicateSelected)
  el.querySelector('#ed-delete')!.addEventListener('click', removeSelected)
  helpButton.addEventListener('click', () => showKeys(keysCard.hidden))
  /** Масштаб меняют и кнопками, и ползунком: ползунок обязан показывать то, что вышло. */
  function setZoom(pxPerSec: number): void {
    timeline.setZoom(pxPerSec)
    zoomSlider.value = String(zoomToPercent(timeline.zoom()))
  }

  zoomInButton.addEventListener('click', () => setZoom(timeline.zoom() * 1.5))
  zoomOutButton.addEventListener('click', () => setZoom(timeline.zoom() / 1.5))
  zoomSlider.addEventListener('input', () => setZoom(percentToZoom(Number(zoomSlider.value))))
  zoomSlider.value = String(zoomToPercent(timeline.zoom()))

  function applyOutput(patch: Partial<ProjectDoc['output']>): void {
    if (!project) return
    remember()
    project = { ...project, doc: { ...project.doc, output: { ...project.doc.output, ...patch } } }
    render()
    saver.schedule(project)
  }

  aspectPick.addEventListener('change', () => {
    applyOutput({ aspect: aspectPick.value as '16:9' | '9:16' | '1:1' })
  })
  fitPick.addEventListener('change', () => {
    applyOutput({ fit: fitPick.value as 'pad' | 'crop' })
  })
  fpsPick.addEventListener('change', () => {
    applyOutput({ fps: Number(fpsPick.value) })
  })


  function undo(): void {
    if (!project) return
    const previous = history.undo()
    undoButton.disabled = !history.canUndo()
    if (!previous) return
    project = { ...project, doc: previous }
    render()
    saver.schedule(project)
    if (playing) seek(Math.min(timelineTime, totalDuration(previous.clips)))
    notice('Действие отменено')
  }
  undoButton.addEventListener('click', undo)

  function applyGoto(): void {
    if (!project) return
    const parsed = parseTimecode(gotoInput.value)
    if (parsed === null) {
      gotoInput.classList.add('bad')
      return
    }
    gotoInput.classList.remove('bad')
    seek(Math.max(0, Math.min(parsed, totalDuration(project.doc.clips))))
  }
  gotoInput.addEventListener('change', applyGoto)
  gotoInput.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault()
      applyGoto()
    }
  })

  // Клавиши монтажа: пробел — играть/стоп, стрелки — шаг курсора, Home/End — края.
  // Не работают, если фокус в поле ввода (например, в имени точки сохранения).
  /**
   * Клавиши, которые редактор забирает у браузера. Остальные не трогаем: Ctrl+S должен остаться
   * сохранением страницы, F5 — перезагрузкой, а Tab внутри полей — переходом по полям.
   */
  const GRABBED = new Set<Shortcut>([
    'play', 'back', 'forward', 'edgeBack', 'edgeForward', 'split', 'remove', 'duplicate',
    'copy', 'paste', 'trimIn', 'trimOut', 'prevClip', 'nextClip', 'zoomIn', 'zoomOut',
  ])

  function onKey(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null
    // В поле ввода клавиши принадлежат полю: Delete стирает букву, а не клип со шкалы.
    if (target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return
    if (target?.isContentEditable) return
    const what = shortcutFor(event)
    if (!what) return
    if (what === 'undo') {
      event.preventDefault()
      undo()
      return
    }
    if (what === 'help') {
      event.preventDefault()
      showKeys(keysCard.hidden)
      return
    }
    if (what === 'deselect') {
      // Esc сначала закрывает подсказку, и только потом снимает выделение: иначе одно нажатие
      // делает два дела разом.
      if (!keysCard.hidden) return showKeys(false)
      selectClip(null)
      return
    }
    // Стрелки вверх и вниз — ещё и прокрутка. В левой колонке и в тексте расшифровки прокрутка
    // важнее перехода по клипам: там читают, а не монтируют, и колонка со своей прокруткой иначе
    // становится неподвижной.
    // target бывает и не элементом (событие, посланное самому документу), а closest есть только
    // у элементов — без проверки обработчик клавиш падал целиком.
    const inSide = target instanceof Element && target.closest('.side') !== null
    if ((what === 'prevClip' || what === 'nextClip') && inSide) return
    // Ctrl+C при выделенном тексте принадлежит тексту: человек копирует сообщение об ошибке,
    // чтобы отправить его нам, а не клип. Ctrl+V без скопированного клипа тоже отдаём браузеру.
    if (what === 'copy' && !(window.getSelection()?.isCollapsed ?? true)) return
    if (what === 'paste' && !clipboard) return
    if (!project) return
    // Без выбранного клипа отнимать Delete у браузера незачем — и сказать об этом честнее,
    // чем промолчать.
    if (needsClip(what) && !timeline.selected()) return underTrack('Сначала выберите клип на шкале')
    if (GRABBED.has(what)) event.preventDefault()
    const total = totalDuration(project.doc.clips)
    const step = event.shiftKey ? 0.1 : 1
    switch (what) {
      case 'play':
        playButton.click()
        break
      case 'back':
        seek(Math.max(0, timelineTime - step))
        break
      case 'forward':
        seek(Math.min(total, timelineTime + step))
        break
      case 'start':
        seek(0)
        break
      case 'end':
        seek(Math.max(0, total - 0.05))
        break
      case 'edgeBack':
        stepEdge(-1)
        break
      case 'edgeForward':
        stepEdge(1)
        break
      case 'split':
        splitHere()
        break
      case 'remove':
        removeSelected()
        break
      case 'duplicate':
        if (chosenSound() || chosenOverlay()) laneCannot()
        else duplicateSelected()
        break
      case 'copy':
        if (chosenSound() || chosenOverlay()) laneCannot()
        else copySelected()
        break
      case 'paste':
        pasteClip()
        break
      case 'trimIn':
        if (chosenSound() || chosenOverlay()) laneCannot()
        else trimToPlayhead('in')
        break
      case 'trimOut':
        if (chosenSound() || chosenOverlay()) laneCannot()
        else trimToPlayhead('out')
        break
      case 'prevClip':
        stepClip(-1)
        break
      case 'nextClip':
        stepClip(1)
        break
      case 'zoomIn':
        setZoom(timeline.zoom() * 1.5)
        break
      case 'zoomOut':
        setZoom(timeline.zoom() / 1.5)
        break
    }
  }
  document.addEventListener('keydown', onKey)

  async function boot(): Promise<void> {
    const [loaded, list, ready] = await Promise.all([
      loadProject(projectId),
      listProjectAssets(projectId),
      // Список готовых роликов нужен ровно для одного: включать ли вкладку «Рендер». Его отказ —
      // не повод хоронить весь редактор: без него панель просто останется закрытой, а раньше
      // проект вообще не открывался и висел на «загрузка…» до перезагрузки страницы.
      listRenders(projectId).catch(() => ({ renders: [] })),
    ])
    if (stopped) return
    project = loaded
    hasReadyRender = ready.renders.length > 0
    applyAssets(list.assets)
    stateBox.textContent = STATE_TEXT.idle
    booted = true
    syncTabs()
    render()
    // Без перемотки оба элемента video остаются без src, и открытый проект встречает человека
    // чёрным прямоугольником под полной шкалой.
    if (loaded.doc.clips.length) seek(0)
    pollAssets()
  }

  void boot().catch(showError)

  return {
    stop(): void {
      pauseSounds()
      pauseOverlays()
      soundPlayers.forEach(player => player.removeAttribute('src'))
      overlayPlayers.forEach(player => player.removeAttribute('src'))
      stopped = true
      window.clearTimeout(assetTimer)
      document.removeEventListener('keydown', onKey)
      closeSaves()
      renders?.stop()
      subtitles.stop()
      transcript.stop()
      // Уход с экрана не повод терять последнюю правку: она могла не дожить до конца задержки.
      // Отказ здесь гасим: экран уже разбирается, показывать ошибку некому, а необработанный
      // отказ промиса всплыл бы в консоль. О сбое уже сказал onError.
      if (project && saver.pending()) void saver.flush(project).catch(() => {})
      else saver.cancel()
      active.pause()
    },
  }
}
