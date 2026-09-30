/**
 * Экран записей: загрузка файлов и список того, что уже лежит.
 *
 * Список общий: записи всей команды, свежие сверху, на карточке автор — «вы» у своей. Удалить
 * может любой, кроме записи, которая стоит в чьём-либо проекте: тут сервер отказывает.
 *
 * Обработка идёт на сервере минутами, поэтому список сам перечитывается, пока хоть одна запись
 * не доехала до конечного состояния. Опрос прекращается вместе с экраном: иначе он продолжил бы
 * стучаться и рисовать в разобранную разметку.
 */
import { ApiError } from './api'
import {
  deleteAsset,
  fmtDuration,
  loadLimits,
  fmtSize,
  frameHtml,
  listAssets,
  needsPolling,
  paintFrames,
  POLL_MS,
  statusText,
  type Asset,
} from './assets'
import { escapeHtml } from './html'
import { authorLabel, ownedBy, ownerLabel } from './overview'
import type { Me, Shell } from './shell'
import { type AssetData } from './strip'
import { UploadAborted, uploadFile } from './upload'

const KIND_WORD: Record<string, string> = {
  video: 'Видео',
  audio: 'Звук',
  image: 'Картинки',
  subtitle: 'Субтитры',
}
/** Порядок перечисления: от того, чего грузят больше всего, к тому, чего меньше. */
const KIND_ORDER = ['video', 'audio', 'image', 'subtitle']

/** Строка для окна выбора файла: «.mp4,.mov,…». Браузер по ней прячет заведомо чужие файлы. */
export function acceptAttr(formats: Record<string, string[]>): string {
  return KIND_ORDER.flatMap(kind => formats[kind] ?? []).map(ext => `.${ext}`).join(',')
}

/** Разбор списка форматов по видам: «Видео — mp4, mov, …». */
export function formatRows(formats: Record<string, string[]>): { name: string; exts: string }[] {
  return KIND_ORDER.filter(kind => (formats[kind] ?? []).length).map(kind => ({
    name: KIND_WORD[kind] ?? kind,
    exts: (formats[kind] ?? []).join(', '),
  }))
}

/** Подпись под именем записи: автор («вы» у своей), длительность или «картинка», вес. */
export function assetMetaText(a: Asset, myEmail: string): string {
  return `${authorLabel(a, myEmail)} · ${a.kind === 'image' ? 'картинка' : fmtDuration(a.duration)} · ${fmtSize(a.size)}`
}

/** Вопрос перед удалением. Удаление необратимо: у чужой записи называем автора и что уйдёт с ней. */
export function dropAssetQuestion(a: Asset, myEmail: string): string {
  if (ownedBy(a, myEmail)) return `Удалить «${a.original_name}» без возможности восстановления?`
  return (
    `Удалить чужую запись «${a.original_name}» (автор — ${ownerLabel(a)}) без возможности ` +
    'восстановления? Вместе с ней пропадут её расшифровка и конвертации.'
  )
}

export type InUse = { id: string; name: string; owner_email: string; owner_name: string }

/** Отказ 409: запись стоит в проектах — называем их и авторов, чтобы было ясно, к кому идти. */
export function inUseText(projects: InUse[], myEmail: string): string {
  const names = projects.map(p => `«${p.name}» (${authorLabel(p, myEmail)})`).join(', ')
  return `Запись стоит в проекте: ${names}. Сначала уберите её оттуда.`
}

export function mountFiles(el: HTMLElement, onChanged?: () => void, work?: Shell['work'], me?: Me) {
  const myEmail = me?.email ?? ''
  el.innerHTML = `
    <div class="screen stack">
      <h1 class="display-l" style="margin:0">Записи</h1>
      <label class="card dropzone" id="f-drop">
        <input id="f-input" type="file" multiple hidden />
        <span class="display-m">Перетащите запись сюда</span>
        <span class="lead">или нажмите, чтобы выбрать. Прерванная загрузка продолжится
          с места разрыва, если выбрать тот же файл снова</span>
      </label>
      <p class="meta" style="margin:0">Записи общие: загруженное видит вся команда</p>
      <!-- Список форматов стоит вне рамки загрузки: она целиком — метка скрытого поля файла,
           и любой щелчок внутри неё открыл бы окно выбора вместо раскрытия списка. -->
      <details class="formats">
        <summary class="meta" id="f-formats">Загружаю список форматов…</summary>
        <div class="stack" id="f-formats-body" style="--stack-gap:4px"></div>
      </details>
      <!-- Ошибка — над списком: под длинным общим списком её не видно. -->
      <pre id="f-error" hidden></pre>
      <div id="f-list" class="stack"></div>
    </div>`

  const drop = el.querySelector('#f-drop') as HTMLElement
  const input = el.querySelector('#f-input') as HTMLInputElement
  const formatsHead = el.querySelector('#f-formats') as HTMLElement
  const formatsBody = el.querySelector('#f-formats-body') as HTMLElement
  const list = el.querySelector('#f-list') as HTMLElement
  const errorBox = el.querySelector('#f-error') as HTMLPreElement
  const frames = new Map<string, Promise<AssetData>>()
  let timer: number | undefined
  let stopped = false
  let shown: Asset[] = []

  const alive = () => !stopped
  const showError = (e: unknown) => {
    errorBox.hidden = false
    if (e instanceof ApiError && e.code === 'asset_in_use') {
      const projects = (e.details as { projects?: InUse[] } | null)?.projects ?? []
      errorBox.textContent = inUseText(projects, myEmail)
      return
    }
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  function card(a: Asset): string {
    const note = a.error ? a.error : ''
    const state = a.status === 'failed' ? ' pill-bad' : ''
    return `<article class="card stack asset-card" style="gap:12px">
      <div class="row" style="margin:0;align-items:center;gap:16px">
        ${frameHtml(a)}
        <div class="stack asset-name">
          <span>${escapeHtml(a.original_name)}</span>
          <span class="meta">${escapeHtml(assetMetaText(a, myEmail))}</span>
        </div>
        <span class="pill${state}">${escapeHtml(statusText(a.status))}</span>
        ${note ? `<span class="meta">${escapeHtml(note)}</span>` : ''}
        <span class="row asset-actions">
          <button class="btn btn-ghost" data-drop-asset="${escapeHtml(a.id)}">Удалить</button>
        </span>
      </div>
    </article>`
  }

  async function refresh(): Promise<void> {
    if (stopped) return
    const { assets } = await listAssets()
    if (stopped) return
    shown = assets
    list.innerHTML = assets.length
      ? assets.map(card).join('')
      : '<p class="lead" style="margin:0">Загрузите первую запись — дальше из неё соберётся ролик</p>'
    paintFrames(list, frames, alive)
    wire()
    window.clearTimeout(timer)
    if (!stopped && needsPolling(assets)) {
      timer = window.setTimeout(() => void refresh().catch(showError), POLL_MS)
    }
  }

  function wire(): void {
    list.querySelectorAll<HTMLButtonElement>('button[data-drop-asset]').forEach(b =>
      b.addEventListener('click', async () => {
        const asset = shown.find(a => a.id === b.dataset.dropAsset)
        if (!asset || !window.confirm(dropAssetQuestion(asset, myEmail))) return
        b.disabled = true
        try {
          await deleteAsset(asset.id)
        } catch (e) {
          b.disabled = false
          showError(e)
          return
        }
        errorBox.hidden = true
        onChanged?.()
        await refresh().catch(showError)
      }),
    )
  }

  async function take(files: File[]): Promise<void> {
    for (const file of files) {
      const handle = work?.trackUpload(file.name)
      try {
        await uploadFile(file, {
          onProgress: (d, t) => handle?.setProgress(d, t),
          signal: handle?.signal,
        })
        handle?.succeed()
      } catch (e) {
        if (e instanceof UploadAborted) {
          handle?.abort()
          continue
        }
        handle?.fail(e instanceof Error ? e.message : String(e))
        showError(e)
        continue
      }
      onChanged?.()
      await refresh().catch(showError)
    }
  }

  input.addEventListener('change', () => {
    const files = Array.from(input.files ?? [])
    input.value = ''
    void take(files)
  })

  // Перетаскивание: браузер по умолчанию открывает брошенный файл вместо страницы.
  drop.addEventListener('dragover', event => {
    event.preventDefault()
    drop.classList.add('over')
  })
  drop.addEventListener('dragleave', () => drop.classList.remove('over'))
  drop.addEventListener('drop', event => {
    event.preventDefault()
    drop.classList.remove('over')
    void take(Array.from(event.dataTransfer?.files ?? []))
  })

  /**
   * Форматы и предел размера приходят с сервера, а не написаны здесь.
   *
   * Не доехали — молчим о них совсем: обещание «до 5 ГБ, mp4 и mov», разошедшееся с сервером,
   * хуже отсутствия подписи. Загрузку это не блокирует, отвечать за формат всё равно серверу.
   */
  async function showFormats(): Promise<void> {
    const limits = await loadLimits()
    if (stopped) return
    input.accept = acceptAttr(limits.formats)
    formatsHead.textContent =
      `Видео, звук, картинки и субтитры — до ${fmtSize(limits.max_upload_bytes)} на файл.` +
      ' Какие именно?'
    formatsBody.innerHTML = formatRows(limits.formats)
      .map(row => `<span class="meta">${escapeHtml(row.name)} — ${escapeHtml(row.exts)}</span>`)
      .join('')
  }

  void showFormats().catch(() => {
    formatsHead.textContent = 'Видео, звук, картинки и субтитры'
  })

  void refresh().catch(showError)

  return {
    stop(): void {
      stopped = true
      window.clearTimeout(timer)
    },
  }
}
