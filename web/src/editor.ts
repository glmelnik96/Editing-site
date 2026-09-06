/**
 * Экран редактора: панель исходника, шкала, плеер склейки, автосохранение.
 *
 * Состояние — один документ проекта плюс версия. Любая правка идёт через applyClips: он кладёт
 * новый список, перерисовывает и просит сохранить. Ответ сервера заменяет документ целиком:
 * там уже подтянутые резы, флаги подтверждения и новая версия.
 */
import { api, ApiError } from './api'
import type { Asset } from './assets'
import { createHistory } from './history'
import { escapeHtml } from './html'
import { aspectRatio, musicVolume, seekPlan, stepPlan } from './playback'
import { createCheckpoint, createSaver, loadProject, type FieldError, type Project, type ProjectDoc } from './project'
import { assetData, type AssetData } from './strip'
import { formatTimecode, parseTimecode } from './timecode'
import { insertClip, ms, newClipId, removeClip, splitAt, totalDuration, type Clip } from './timeline/model'
import { mountRender } from './render'
import { mountSource } from './source'
import { mountTranscript } from './transcript'
import { mountTimeline, type AssetInfo } from './timeline/view'
import { mountVersions } from './versions'

const STATE_TEXT = {
  idle: 'сохранено',
  pending: 'правки не сохранены',
  saving: 'сохраняю…',
  failed: 'не сохранено, попробуйте ещё правку',
}

export function mountEditor(el: HTMLElement, projectId: string) {
  el.innerHTML = `
    <header class="bar">
      <a class="button" href="#/">← к файлам</a>
      <strong id="ed-name">Проект</strong>
      <span class="save-state" id="ed-state">загрузка…</span>
      <span id="ed-notice" class="muted"></span>
      <span class="muted">пробел — играть, стрелки — шаг, Shift — точнее, Ctrl+Z — отменить</span>
    </header>
    <div class="editor">
      <section class="side">
        <div id="ed-source"></div>
        <div id="ed-transcript"></div>
        <div id="ed-versions"></div>
        <section id="ed-renders"></section>
      </section>
      <section>
        <div class="stage" id="ed-stage"></div>
        <div class="row">
          <button id="ed-undo" type="button" disabled title="Отменить последнее действие (Ctrl+Z)">Отменить</button>
          <button id="ed-play" type="button">▶</button>
          <button id="ed-split" type="button">Разрезать</button>
          <button id="ed-delete" type="button">Удалить клип</button>
          <button id="ed-save" type="button">Сохранить точку</button>
          <button id="ed-zoom-in" type="button">+</button>
          <button id="ed-zoom-out" type="button">−</button>
          <select id="ed-aspect">
            <option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option>
          </select>
          <input id="ed-goto" class="tc" inputmode="decimal" title="Перейти к таймкоду" />
          <span class="muted" id="ed-total"></span>
        </div>
        <div id="ed-timeline"></div>
      </section>
    </div>
    <pre id="ed-error" hidden></pre>`

  const nameBox = el.querySelector('#ed-name') as HTMLElement
  const stateBox = el.querySelector('#ed-state') as HTMLElement
  const noticeBox = el.querySelector('#ed-notice') as HTMLElement
  const stage = el.querySelector('#ed-stage') as HTMLElement
  const totalBox = el.querySelector('#ed-total') as HTMLElement
  const errorBox = el.querySelector('#ed-error') as HTMLPreElement
  const aspectPick = el.querySelector('#ed-aspect') as HTMLSelectElement
  const history = createHistory<ProjectDoc>(5)
  const undoButton = el.querySelector('#ed-undo') as HTMLButtonElement
  const gotoInput = el.querySelector('#ed-goto') as HTMLInputElement

  let project: Project | null = null
  let assets = new Map<string, AssetInfo>()
  let assetList: Asset[] = []
  const dataCache = new Map<string, Promise<AssetData>>()
  const data = new Map<string, AssetData>()
  let playing = false
  let playIndex = 0
  let timelineTime = 0
  let stopped = false
  let versions: { refresh: () => Promise<void> } | null = null
  let renders: { stop: () => void } | null = null

  /** Запомнить состояние ДО правки: именно к нему вернёт кнопка «Отменить». */
  function remember(): void {
    if (project) history.push(project.doc)
    undoButton.disabled = !history.canUndo()
  }

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  const notice = (text: string) => {
    noticeBox.textContent = text
    if (text) window.setTimeout(() => (noticeBox.textContent = ''), 6000)
  }

  function showTime(): void {
    if (document.activeElement !== gotoInput) {
      gotoInput.value = formatTimecode(timelineTime)
      gotoInput.classList.remove('bad')
    }
    totalBox.textContent = project ? `из ${formatTimecode(totalDuration(project.doc.clips))}` : ''
  }

  const saver = createSaver({
    onSaved: saved => {
      if (stopped) return
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
      notice(`Не сохранено: ${errors.map(e => `${e.field} — ${e.message}`).join('; ')}`)
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

  const proxyOf = (assetId: string): string | null => assetList.find(a => a.id === assetId)?.files.proxy ?? null

  function swap(): void {
    const hidden = active === videoA ? videoB : videoA
    active.pause()
    active.style.display = 'none'
    hidden.style.display = ''
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
    if (plan.kind === 'advance') {
      if (!proxyOf(plan.assetId)) {
        // У следующего клипа ещё нет прокси: показывать пустой кадр хуже, чем честно встать.
        playing = false
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
      playing = false
      active.pause()
      music.pause()
    }
    if (project.doc.music) {
      music.volume = musicVolume(project.doc.music, timelineTime, totalDuration(project.doc.clips))
    }
  }
  videoA.addEventListener('timeupdate', onTimeUpdate)
  videoB.addEventListener('timeupdate', onTimeUpdate)

  const timeline = mountTimeline(el.querySelector('#ed-timeline') as HTMLElement, {
    onChange: applyClips,
    onSeek: seek,
    onSelect: () => {},
  })

  /** Кусок исходника в конец шкалы: приходит и от полосы файла, и от выделения в тексте. */
  function addClip(assetId: string, from: number, to: number, snap: boolean): void {
    const clips = project?.doc.clips ?? []
    const clip: Clip = {
      id: newClipId(clips),
      asset_id: assetId,
      in: ms(from),
      out: ms(to),
      snap_to_pauses: snap,
      in_verified: false,
      out_verified: false,
    }
    applyClips(insertClip(clips, clip))
  }

  const sourceBox = el.querySelector('#ed-source') as HTMLElement
  const source = mountSource(sourceBox, {
    onAdd: (asset, range) => addClip(asset.id, range.from, range.to, false),
  })

  const sourcePlayer = (): HTMLMediaElement | null => sourceBox.querySelector('video, audio')

  const transcript = mountTranscript(el.querySelector('#ed-transcript') as HTMLElement, {
    onSeek: seconds => {
      const player = sourcePlayer()
      if (player) player.currentTime = seconds
    },
    // Времена слов интерполированы (±0.3 с), поэтому клип идёт со снэпом: границы досадит на
    // измеренные паузы сервер. Это тот же путь, которым кладёт кусок агент через API.
    onTake: (start, end) => {
      const asset = source.current()
      if (asset) addClip(asset.id, start, end, true)
    },
  })

  // Панель текста работает с тем же файлом, что и панель исходника. О смене файла узнаём по
  // событию из самой панели: change от её списка всплывает до контейнера, и лезть внутрь чужой
  // панели за её select не приходится. Тот же файл setAsset пропускает, так что change от полей
  // таймкода панель текста не тревожит.
  sourceBox.addEventListener('change', () => transcript.setAsset(source.current()))
  // timeupdate не всплывает — слушаем на фазе перехвата: плеер исходника панель исходника
  // пересоздаёт при каждом выборе файла, а этот слушатель переживает пересоздание.
  sourceBox.addEventListener('timeupdate', event => transcript.setTime((event.target as HTMLMediaElement).currentTime), true)

  function applyClips(clips: Clip[]): void {
    if (!project) return
    remember()
    project = { ...project, doc: { ...project.doc, clips } }
    render()
    saver.schedule(project)
    // Список изменился под играющим клипом: номер клипа больше ничего не значит, встаём заново
    // по времени шкалы. Иначе плеер продолжил бы мерить время удалённого клипа.
    if (playing) seek(Math.min(timelineTime, totalDuration(clips)))
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
    const ratio = aspectRatio(project.doc.output.aspect)
    stage.style.aspectRatio = String(ratio)
    stage.style.maxWidth = `calc(${ratio} * var(--stage-h))`
    stage.classList.toggle('crop', project.doc.output.fit === 'crop')
    timeline.render({ clips: project.doc.clips, assets, data })
    timeline.setPlayhead(timelineTime)
    showTime()
    void ensureData(project.doc.clips)
  }

  el.querySelector('#ed-play')!.addEventListener('click', () => {
    if (!project || !project.doc.clips.length) return
    playing = !playing
    if (playing) {
      if (!active.src) seek(timelineTime)
      void active.play().catch(showError)
      if (project.doc.music) void music.play().catch(() => {})
    } else {
      active.pause()
      music.pause()
    }
  })

  el.querySelector('#ed-split')!.addEventListener('click', () => {
    if (!project) return
    const next = splitAt(project.doc.clips, timelineTime)
    if (next === project.doc.clips) notice('Здесь резать нечего: курсор на краю клипа')
    else applyClips(next)
  })

  el.querySelector('#ed-delete')!.addEventListener('click', () => {
    const id = timeline.selected()
    if (!project || !id) return notice('Сначала выберите клип на шкале')
    applyClips(removeClip(project.doc.clips, id))
  })

  el.querySelector('#ed-save')!.addEventListener('click', async () => {
    if (!project) return
    try {
      // Сначала дописываем несохранённое: снимок должен поймать то, что видит человек.
      if (saver.pending()) await saver.flush(project)
      await createCheckpoint(projectId, '')
      await versions?.refresh()
      notice('Точка сохранена')
    } catch (e) {
      showError(e)
    }
  })

  el.querySelector('#ed-zoom-in')!.addEventListener('click', () => timeline.setZoom(timeline.zoom() * 1.5))
  el.querySelector('#ed-zoom-out')!.addEventListener('click', () => timeline.setZoom(timeline.zoom() / 1.5))

  aspectPick.addEventListener('change', () => {
    if (!project) return
    remember()
    const aspect = aspectPick.value as '16:9' | '9:16' | '1:1'
    project = { ...project, doc: { ...project.doc, output: { ...project.doc.output, aspect } } }
    render()
    saver.schedule(project)
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
  function onKey(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null
    if (target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return
    if ((event.ctrlKey || event.metaKey) && event.code === 'KeyZ') {
      event.preventDefault()
      undo()
      return
    }
    if (!project) return
    const total = totalDuration(project.doc.clips)
    const step = event.shiftKey ? 0.1 : 1
    if (event.code === 'Space') {
      event.preventDefault()
      ;(el.querySelector('#ed-play') as HTMLButtonElement).click()
    } else if (event.code === 'ArrowLeft') {
      event.preventDefault()
      seek(Math.max(0, timelineTime - step))
    } else if (event.code === 'ArrowRight') {
      event.preventDefault()
      seek(Math.min(total, timelineTime + step))
    } else if (event.code === 'Home') {
      seek(0)
    } else if (event.code === 'End') {
      seek(Math.max(0, total - 0.05))
    }
  }
  document.addEventListener('keydown', onKey)

  async function boot(): Promise<void> {
    const [loaded, list] = await Promise.all([
      loadProject(projectId),
      api<{ assets: Asset[] }>('/api/v1/assets'),
    ])
    if (stopped) return
    project = loaded
    assetList = list.assets
    assets = new Map(list.assets.map(a => [a.id, { duration: a.duration, files: { thumbs: a.files.thumbs } }]))
    source.setAssets(list.assets)
    if (project.doc.music) {
      const musicAsset = list.assets.find(a => a.id === project?.doc.music?.asset_id)
      if (musicAsset?.files.proxy) music.src = musicAsset.files.proxy
    }
    stateBox.textContent = STATE_TEXT.idle
    versions = mountVersions(el.querySelector('#ed-versions') as HTMLElement, projectId, restored => {
      remember()
      project = restored
      timelineTime = 0
      render()
      // Документ подменили целиком: играющий клип мог исчезнуть, встаём заново на начало.
      if (playing) seek(0)
      notice('Вернулись к сохранённой точке')
    })
    renders = mountRender(el.querySelector('#ed-renders') as HTMLElement, projectId, async () => {
      if (project && saver.pending()) await saver.flush(project)
    })
    render()
  }

  void boot().catch(showError)

  return {
    stop(): void {
      stopped = true
      document.removeEventListener('keydown', onKey)
      renders?.stop()
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
