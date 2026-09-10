/**
 * Экран редактора: панель исходников, шкала, плеер склейки, автосохранение.
 *
 * Состояние — один документ проекта плюс версия. Любая правка идёт через applyClips: он кладёт
 * новый список, перерисовывает и просит сохранить. Ответ сервера заменяет документ целиком:
 * там уже подтянутые резы, флаги подтверждения и новая версия.
 */
import { ApiError } from './api'
import { POLL_MS, listAssets, needsPolling, type Asset } from './assets'
import { createHistory } from './history'
import { escapeHtml } from './html'
import {
  aspectRatio,
  incomingAt,
  musicVolume,
  previewClipVolume,
  previewSpeechGain,
  resumePlan,
  seekPlan,
  stepPlan,
  type Incoming,
} from './playback'
import { createSaver, listRenders, loadProject, type Cue, type FieldError, type Music, type Project, type ProjectDoc } from './project'
import { assetData, type AssetData } from './strip'
import { formatTimecode, parseTimecode } from './timecode'
import { clampTransitions, clipAt, clipAssetIds, clipDuration, fadeInto, insertClip, maxFade, ms, newClipId, removeClip, splitAt, timelineStart, totalDuration, trimClip, type Clip } from './timeline/model'
import { mountMusic } from './music'
import { mountRender } from './render'
import { resolveTab, tabEnabled, type EditorTab } from './editor-tabs'
import { HOTKEYS, needsClip, shortcutFor, type Shortcut } from './hotkeys'
import { mountSource } from './source'
import { burnEnabled, cuesReady, mountSubtitles, patchCues } from './subtitles'
import { mountTranscript } from './transcript'
import { mountTimeline, type AssetInfo } from './timeline/view'
import { mountVersions } from './versions'

/** Шаг опроса записей, когда ничего не обрабатывается и не расшифровывается. */
const IDLE_POLL_MS = 20000

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
    <div class="editor">
      <section class="side">
        <nav class="tabs" id="ed-tabs">
          <button type="button" class="tab" data-tab="source">Исходники</button>
          <button type="button" class="tab" data-tab="subtitles">Субтитры</button>
          <button type="button" class="tab" data-tab="renders">Рендер</button>
        </nav>
        <div id="ed-source" data-panel="source">
          <div id="ed-source-main"></div>
          <div id="ed-transcript"></div>
          <div id="ed-music"></div>
        </div>
        <div id="ed-subtitles" data-panel="subtitles" hidden></div>
        <section id="ed-renders" data-panel="renders" hidden></section>
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
            <button id="ed-zoom-out" type="button" title="Мельче (−)">−</button>
            <button id="ed-zoom-in" type="button" title="Крупнее (+)">+</button>
          </span>
          <span class="bar-group" id="ed-help-group">
            <button id="ed-help" type="button" class="btn-icon" title="Горячие клавиши (?)"
              aria-expanded="false">?</button>
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
        <!-- Свойства одного клипа: строка появляется по выбору. Висеть серой всё время им незачем,
             к ролику целиком они отношения не имеют. -->
        <div class="row bar-clip" id="ed-clip-bar">
          <span class="meta" id="ed-clip-mark">Клип не выбран</span>
          <label class="clip-vol">Громкость
            <input id="ed-volume" type="range" min="0" max="2" step="0.01" disabled />
            <span id="ed-vol-note" class="muted" hidden>в сборке громче превью</span>
          </label>
          <label class="clip-fade">Переход, с
            <input id="ed-fade" class="tc" type="number" min="0" step="0.1" disabled title="В этот клип из предыдущего. Ноль — стык." />
          </label>
        </div>
        <div id="ed-timeline"></div>
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
  const volumeInput = el.querySelector('#ed-volume') as HTMLInputElement
  const volumeNote = el.querySelector('#ed-vol-note') as HTMLElement
  const fadeInput = el.querySelector('#ed-fade') as HTMLInputElement
  const history = createHistory<ProjectDoc>(5)
  const undoButton = el.querySelector('#ed-undo') as HTMLButtonElement
  const playButton = el.querySelector('#ed-play') as HTMLButtonElement
  const clipMark = el.querySelector('#ed-clip-mark') as HTMLElement
  const splitButton = el.querySelector('#ed-split') as HTMLButtonElement
  const copyButton = el.querySelector('#ed-copy') as HTMLButtonElement
  const deleteButton = el.querySelector('#ed-delete') as HTMLButtonElement
  const zoomInButton = el.querySelector('#ed-zoom-in') as HTMLButtonElement
  const zoomOutButton = el.querySelector('#ed-zoom-out') as HTMLButtonElement
  const helpButton = el.querySelector('#ed-help') as HTMLButtonElement
  const keysCard = el.querySelector('#ed-keys') as HTMLElement
  const gotoInput = el.querySelector('#ed-goto') as HTMLInputElement
  const burnBox = el.querySelector('#ed-burn') as HTMLInputElement
  const saves = el.querySelector('#ed-saves') as HTMLElement
  const savesToggle = el.querySelector('#ed-saves-toggle') as HTMLButtonElement
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
  let renders: { stop: () => void; setDoc: (doc: ProjectDoc, name?: string) => void } | null = null
  let assetTimer = 0
  const analysisCache = new Map<string, { start: number; end: number }[] | null>()
  const analysisPending = new Set<string>()

  function markNews(name: string): void {
    if (tab === name) return
    tabsBar.querySelector<HTMLButtonElement>(`.tab[data-tab="${name}"]`)?.classList.add('news')
  }

  function applyAssets(list: Asset[]): void {
    const first = !assetsSeen
    assetsSeen = true
    const previous = new Map(assetList.map(a => [a.id, a]))
    assetList = list
    assets = new Map(list.map(a => [a.id, { duration: a.duration, files: { thumbs: a.files.thumbs } }]))
    source.setAssets(list)
    // Панель исходника при том же выбранном файле обработчик не дёргает, поэтому свежую карточку
    // в панель текста передаём сами. Иначе доехавшая расшифровка (её мог заказать другой экран,
    // вторая вкладка или агент) не доходила бы до неё никогда: у панели свой опрос выключен, и
    // она вечно писала бы «Расшифровки ещё нет».
    const picked = source.current()
    if (picked) transcript.setAsset(list.find(a => a.id === picked.id) ?? null)
    musicPanel.setAssets(list)
    if (project) {
      const ids = clipAssetIds(project.doc.clips)
      for (const id of ids) {
        const hadTranscript = Boolean(previous.get(id)?.files.transcript)
        const hasTranscript = Boolean(list.find(a => a.id === id)?.files.transcript)
        if (hasTranscript && !hadTranscript && !first) markNews('subtitles')
      }
      subtitles.setTimeline(project.doc.clips, list)
    }
    if (project?.doc.music) {
      const musicAsset = list.find(a => a.id === project?.doc.music?.asset_id)
      if (musicAsset?.files.proxy) setMusicSrc(musicAsset.files.proxy)
    }
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
      void listAssets()
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
  const music = document.createElement('audio')
  let active = videoA
  ;[videoA, videoB].forEach(v => {
    v.preload = 'auto'
    v.playsInline = true
    stage.appendChild(v)
  })
  videoB.style.display = 'none'
  music.preload = 'auto'

  /** Тот же адрес, присвоенный заново, всё равно перезапускает загрузку: дорожка встаёт на ноль.
   * Список ассетов перечитывается раз в три секунды, и без этой проверки музыка начиналась заново
   * каждые три секунды прямо во время просмотра. Так же огорожены оба элемента video. */
  function setMusicSrc(src: string): void {
    if (music.src.endsWith(src)) return
    music.src = src
  }

  const proxyOf = (assetId: string): string | null => assetList.find(a => a.id === assetId)?.files.proxy ?? null

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
      notice('Файл ещё обрабатывается, перемотка недоступна')
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
        music.pause()
        notice('Следующий файл ещё обрабатывается, воспроизведение остановлено')
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
      music.pause()
    }
    applyPreviewVolumes()
  }
  videoA.addEventListener('timeupdate', onTimeUpdate)
  videoB.addEventListener('timeupdate', onTimeUpdate)

  const timeline = mountTimeline(el.querySelector('#ed-timeline') as HTMLElement, {
    onChange: applyClips,
    onSeek: seek,
    onSelect: () => {
      syncBar()
      syncClipVolume()
      syncClipFade()
    },
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
    notice('Кусок на шкале')
  }

  const sourceMain = el.querySelector('#ed-source-main') as HTMLElement
  // Монтаж по тексту стоит под плеером исходника: слова выбирают на слух по нему, а сцена справа
  // играет уже собранный ролик. Времена слов интерполированы (±0.3 с), поэтому клип ставим со
  // snap_to_pauses — настоящий рез подтянет сервер по измеренным паузам. С полосы исходника кусок
  // берут по таймкоду, там подтягивать нечего, и snap выключен.
  const transcript = mountTranscript(el.querySelector('#ed-transcript') as HTMLElement, {
    onSeek: seconds => source.seek(seconds),
    onTake: (from, to) => {
      const asset = source.current()
      if (asset) addClip(asset.id, from, to, true)
    },
  })
  const source = mountSource(sourceMain, {
    onAdd: (asset, range) => addClip(asset.id, range.from, range.to, false),
    onPick: asset => transcript.setAsset(asset),
    onTime: seconds => transcript.setTime(seconds),
  })
  const musicPanel = mountMusic(el.querySelector('#ed-music') as HTMLElement, {
    onChange: applyMusic,
  })

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

  function applyMusic(next: Music | null, live = false): void {
    if (!project) return
    // live — очередной тик перетаскивания ползунка. В историю кладём состояние до всего
    // движения: иначе одно перетаскивание вытесняет пять последних настоящих действий.
    if (!live) remember()
    project = { ...project, doc: { ...project.doc, music: next } }
    if (next) {
      const asset = assetList.find(a => a.id === next.asset_id)
      if (asset?.files.proxy) setMusicSrc(asset.files.proxy)
    } else {
      music.pause()
      music.removeAttribute('src')
    }
    applyPreviewVolumes()
    saver.schedule(project)
    renders?.setDoc(project.doc, project.name)
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
    const clips = project.doc.clips
    const found = clipAt(clips, timelineTime)
    const hidden = active === videoA ? videoB : videoA
    const incoming = incomingAt(clips, timelineTime)
    const mix = incoming?.mix ?? 0
    const speech = previewSpeechGain(project.doc.music)
    active.volume = previewClipVolume(found?.clip.volume ?? 1, speech) * (1 - mix)
    const incomingClip = incoming ? clips[incoming.index] : clips[playIndex + 1]
    hidden.volume = previewClipVolume(incomingClip?.volume ?? 1, speech) * (incoming ? mix : 1)
    const musicDoc = project.doc.music
    if (!musicDoc) {
      music.volume = 0
      return
    }
    let ducking: { sourceTime: number; silences: { start: number; end: number }[] } | null = null
    if (musicDoc.duck && found) {
      const map = analysisCache.get(found.clip.asset_id)
      if (map === undefined) void ensureAnalysis(found.clip.asset_id)
      else if (map !== null) {
        ducking = { sourceTime: found.clip.in + found.offset, silences: map }
      }
    }
    music.volume = musicVolume(musicDoc, timelineTime, totalDuration(clips), ducking)
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
    copyButton.disabled = !chosen
    deleteButton.disabled = !chosen
    clipMark.textContent = chosen ? 'Выбранный клип' : 'Клип не выбран'
  }

  function syncClipVolume(): void {
    const id = timeline.selected()
    const clip = project?.doc.clips.find(c => c.id === id)
    volumeInput.disabled = !clip
    // Громкость клипа появилась позже самих клипов: у проекта, сохранённого до неё, ключа нет,
    // и `undefined <= 1` — ложь. Ползунок вставал в "undefined" (браузер молча правил на 1),
    // а подпись «в сборке громче превью» висела над клипом обычной громкости.
    const volume = clip?.volume ?? 1
    if (clip) volumeInput.value = String(volume)
    volumeNote.hidden = !clip || volume <= 1
  }

  function syncClipFade(): void {
    const id = timeline.selected()
    const clips = project?.doc.clips ?? []
    const index = clips.findIndex(c => c.id === id)
    const clip = index >= 0 ? clips[index] : undefined
    const canFade = index > 0 && clip !== undefined
    fadeInput.disabled = !canFade
    if (!clip) {
      fadeInput.value = ''
      return
    }
    fadeInput.value = String(canFade ? fadeInto(clip, index) : 0)
    if (canFade) fadeInput.max = String(maxFade(clips[index - 1], clip))
  }

  async function ensureData(clips: Clip[]): Promise<void> {
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
    timeline.render({ clips: project.doc.clips, assets, data })
    timeline.setPlayhead(timelineTime)
    subtitles.setProject(project)
    subtitles.setTimeline(project.doc.clips, assetList)
    musicPanel.setMusic(project.doc.music)
    syncBurn()
    syncBar()
    syncClipVolume()
    syncClipFade()
    showTime()
    applyPreviewVolumes()
    renders?.setDoc(project.doc, project.name)
    void ensureData(project.doc.clips)
    syncTabs()
  }

  playButton.addEventListener('click', () => {
    if (!project || !project.doc.clips.length) return
    if (playing) {
      setPlaying(false)
      active.pause()
      const hidden = active === videoA ? videoB : videoA
      hidden.pause()
      music.pause()
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
      music.pause()
      return
    }
    setPlaying(true)
    if (plan.kind === 'advance') {
      const src = proxyOf(plan.assetId)
      if (!src) {
        setPlaying(false)
        notice('Следующий файл ещё обрабатывается, воспроизведение остановлено')
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
    if (project.doc.music) void music.play().catch(() => {})
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
    syncBar()
    syncClipVolume()
    syncClipFade()
  }

  function splitHere(): void {
    if (!project) return
    const next = splitAt(project.doc.clips, timelineTime)
    if (next === project.doc.clips) notice('Здесь резать нечего: курсор на краю клипа')
    else applyClips(next)
  }

  function removeSelected(): void {
    const id = timeline.selected()
    if (!project || !id) return notice('Сначала выберите клип на шкале')
    applyClips(removeClip(project.doc.clips, id))
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
    if (!found) return notice('Сначала выберите клип на шкале')
    const clips = project.doc.clips
    const copy = copyOf(found.clip, clips)
    applyClips(insertClip(clips, copy, found.index + 1))
    selectClip(copy.id)
    notice('Клип продублирован')
  }

  function copySelected(): void {
    const found = picked()
    if (!found) return notice('Сначала выберите клип на шкале')
    clipboard = found.clip
    notice('Клип скопирован — Ctrl+V поставит копию')
  }

  function pasteClip(): void {
    if (!project || !clipboard) return notice('Сначала скопируйте клип: Ctrl+C')
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
    if (!found) return notice('Сначала выберите клип на шкале')
    const clips = project.doc.clips
    const start = timelineStart(clips, found.index)
    const offset = timelineTime - start
    if (offset <= 0 || offset >= clipDuration(found.clip)) {
      return notice('Поставьте курсор внутри выбранного клипа')
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
      return notice('Так клип станет короче допустимого')
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
  el.querySelector('#ed-zoom-in')!.addEventListener('click', () => timeline.setZoom(timeline.zoom() * 1.5))
  el.querySelector('#ed-zoom-out')!.addEventListener('click', () => timeline.setZoom(timeline.zoom() / 1.5))

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

  let volumeRemembered = false
  volumeInput.addEventListener('input', () => {
    const id = timeline.selected()
    if (!project || !id) return
    if (!volumeRemembered) {
      remember()
      volumeRemembered = true
    }
    const volume = Math.round(Number(volumeInput.value) * 1000) / 1000
    project = {
      ...project,
      doc: {
        ...project.doc,
        clips: project.doc.clips.map(clip => (clip.id === id ? { ...clip, volume } : clip)),
      },
    }
    volumeNote.hidden = volume <= 1
    applyPreviewVolumes()
    saver.schedule(project)
  })
  volumeInput.addEventListener('change', () => {
    volumeRemembered = false
  })

  fadeInput.addEventListener('change', () => {
    const id = timeline.selected()
    if (!project || !id) return
    const clips = project.doc.clips
    const index = clips.findIndex(c => c.id === id)
    if (index <= 0) return
    const duration = Math.max(0, ms(Number(fadeInput.value) || 0))
    applyClips(clips.map((clip, i) => {
      if (i !== index) return clip
      if (duration <= 0) {
        const cleared = { ...clip }
        delete cleared.transition
        return cleared
      }
      return { ...clip, transition: { kind: 'fade', duration } }
    }))
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
    if (needsClip(what) && !timeline.selected()) return notice('Сначала выберите клип на шкале')
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
        duplicateSelected()
        break
      case 'copy':
        copySelected()
        break
      case 'paste':
        pasteClip()
        break
      case 'trimIn':
        trimToPlayhead('in')
        break
      case 'trimOut':
        trimToPlayhead('out')
        break
      case 'prevClip':
        stepClip(-1)
        break
      case 'nextClip':
        stepClip(1)
        break
      case 'zoomIn':
        timeline.setZoom(timeline.zoom() * 1.5)
        break
      case 'zoomOut':
        timeline.setZoom(timeline.zoom() / 1.5)
        break
    }
  }
  document.addEventListener('keydown', onKey)

  async function boot(): Promise<void> {
    const [loaded, list, ready] = await Promise.all([
      loadProject(projectId),
      listAssets(),
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
      music.pause()
    },
  }
}
