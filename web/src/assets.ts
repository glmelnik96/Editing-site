/**
 * Записи (ассеты): типы, общее форматирование и запросы к API.
 *
 * Разметка списка записей живёт в `files.ts`, выбор записи — в `newproject.ts`. Здесь остаётся
 * только то, что нужно нескольким экранам сразу: формат размера и времени, состояние обработки,
 * кадр из полоски и память о выбранной записи.
 */
import { api } from './api'
import { assetData, type AssetData } from './strip'

export type Asset = {
  id: string
  kind: string
  original_name: string
  size: number
  status: string
  duration: number | null
  progress?: number | null
  error: string | null
  files: {
    proxy: string | null
    thumbs: string | null
    thumbs_meta: string | null
    peaks: string | null
    analysis: string | null
    vtt: string | null
    // Ссылка на ручку транскрипта, если расшифровка уже есть: по ней панель текста решает,
    // показывать текст или кнопку «Расшифровать».
    transcript: string | null
  }
}

const STATUS: Record<string, string> = {
  uploaded: 'загружен, ждёт анализа',
  analyzing: 'анализ',
  ready: 'звук и полоска готовы, прокси в работе',
  proxy_ready: 'готов',
  failed: 'ошибка',
}
const FINAL = new Set(['proxy_ready', 'failed']) // 'ready' — промежуточный: звук и полоска готовы, прокси ещё собирается
const READY = new Set(['ready', 'proxy_ready']) // из такой записи уже можно резать клип
export const POLL_MS = 3000

export function fmtSize(bytes: number): string {
  const units = ['Б', 'КБ', 'МБ', 'ГБ']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return i === 0 ? `${v} ${units[i]}` : `${v.toFixed(1)} ${units[i]}`
}

export function fmtDuration(sec: number | null): string {
  if (sec === null || !Number.isFinite(sec)) return '—'
  const s = Math.floor(sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  const mm = h ? String(m).padStart(2, '0') : String(m)
  return `${h ? h + ':' : ''}${mm}:${String(r).padStart(2, '0')}`
}

/** Время в поясе человека: сервер отдаёт UTC с Z, и «создан 03:20» в чужом поясе просто врёт. */
export function fmtWhen(ts: string | null): string {
  if (!ts) return '—'
  const at = new Date(ts)
  if (Number.isNaN(at.getTime())) return ts
  return at.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function statusText(status: string): string {
  return STATUS[status] ?? status
}

export function needsPolling(assets: { status: string }[]): boolean {
  return assets.some(a => !FINAL.has(a.status))
}

/** Годится ли запись в клип: длительность известна, обработка дошла хотя бы до звука и полоски. */
export function isReady(a: Asset): boolean {
  return READY.has(a.status) && a.duration !== null && a.duration > 0
}

export function listAssets(): Promise<{ assets: Asset[] }> {
  return api<{ assets: Asset[] }>('/api/v1/assets')
}

export function deleteAsset(id: string): Promise<void> {
  return api<void>(`/api/v1/assets/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** Карточка одного ассета: по ней панель текста видит, не появился ли транскрипт. */
export function loadAsset(id: string): Promise<Asset> {
  return api<Asset>(`/api/v1/assets/${encodeURIComponent(id)}`)
}

/* ═══ Кадр из полоски ═══════════════════════════════════════════════════════ */

const FRAME_H = 90 // высота места под кадр; ширину задаёт пропорция самого кадра
const FRAME_AT = 0.1 // кадр берём не с нуля: первый кадр записи слишком часто чёрный

const FRAME_STYLE = [
  `height:${FRAME_H}px`,
  'width:160px',
  'flex:0 0 auto',
  'border-radius:12px',
  'background-color:var(--line)',
  'background-repeat:no-repeat',
].join(';')

/**
 * Место под кадр из полоски. Сам кадр приезжает позже: раскладку спрайта надо сначала загрузить,
 * а список должен появиться сразу — поэтому здесь только пустая рамка и ссылки для `paintFrames`.
 */
export function frameHtml(a: Asset): string {
  if (!a.files.thumbs || !a.files.thumbs_meta) return `<span style="${FRAME_STYLE}"></span>`
  return `<span style="${FRAME_STYLE}" data-frame="${a.id}"
    data-sprite="${a.files.thumbs}" data-meta="${a.files.thumbs_meta}" data-at="${(a.duration ?? 0) * FRAME_AT}"></span>`
}

/**
 * Дорисовать кадры в уже нарисованной разметке.
 *
 * Клетка спрайта задаётся долями, а не пикселями: спрайт нарезан воркером под свою ширину кадра
 * (настройка сервера), а в карточке место другое — в процентах кадр встаёт в клетку при любой.
 */
export function paintFrames(root: ParentNode, cache: Map<string, Promise<AssetData>>, alive: () => boolean): void {
  root.querySelectorAll<HTMLElement>('[data-frame]').forEach(box => {
    const id = box.dataset.frame ?? ''
    const sprite = box.dataset.sprite ?? ''
    void assetData(id, { peaks: null, thumbs_meta: box.dataset.meta ?? null }, cache)
      .then(({ thumbs }) => {
        if (!alive() || !thumbs) return
        const raw = Math.floor(Math.max(0, Number(box.dataset.at ?? 0)) / thumbs.interval)
        const index = Math.min(thumbs.count - 1, Math.max(0, raw))
        const col = index % thumbs.cols
        const row = Math.floor(index / thumbs.cols)
        box.style.width = `${Math.round((FRAME_H * thumbs.width) / thumbs.height)}px`
        box.style.backgroundImage = `url('${sprite}')`
        box.style.backgroundSize = `${thumbs.cols * 100}% ${thumbs.rows * 100}%`
        box.style.backgroundPosition = `${thumbs.cols > 1 ? (col / (thumbs.cols - 1)) * 100 : 0}%
          ${thumbs.rows > 1 ? (row / (thumbs.rows - 1)) * 100 : 0}%`
      })
      .catch(() => {}) // нет раскладки — карточка живёт с пустой рамкой, это не повод шуметь
  })
}

/* ═══ Память о выбранной записи ═════════════════════════════════════════════ */

const PICK_KEY = 'newproject:asset'

/**
 * Запомнить запись, выбранную кнопкой «В проект».
 *
 * Через адрес выбор не передать: `parseRoute` разбирает только путь и о параметрах не знает,
 * а трогать маршрутизатор ради одной кнопки дороже, чем положить выбор в сессию вкладки.
 */
export function rememberPick(assetId: string): void {
  try {
    sessionStorage.setItem(PICK_KEY, assetId)
  } catch {
    // Приватный режим или запрет на хранилище: экран нового проекта просто спросит выбор заново.
  }
}

/** Прочитать и забыть: выбор одноразовый, иначе он всплывёт при следующем заходе на #/new. */
export function takePick(): string | null {
  try {
    const id = sessionStorage.getItem(PICK_KEY)
    sessionStorage.removeItem(PICK_KEY)
    return id
  } catch {
    return null
  }
}
