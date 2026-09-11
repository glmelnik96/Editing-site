/**
 * Записи (ассеты): типы, общее форматирование и запросы к API.
 *
 * Разметка списка записей живёт в `files.ts`. Здесь остаётся только то, что нужно нескольким
 * экранам сразу: формат размера и времени, состояние обработки и кадр из полоски.
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
  /** Есть ли звуковая дорожка: у клипа без неё нечего показывать в свойствах и на дорожке звука. */
  has_audio?: boolean | null
  /** Размер кадра: по нему панель сборки предупреждает, что выбранное больше исходников. */
  width?: number | null
  height?: number | null
  /** Битрейт исходника, бит/с. У старых записей его нет: колонку завели позже анализа. */
  bit_rate?: number | null
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

/**
 * Имя для атрибута download у ссылки на готовый файл.
 *
 * На ВМ файл отдаёт Caddy, а он ставит `Content-Disposition: attachment` без имени и затирает
 * заголовок, который собрал бы сам API. Пустой атрибут download в такой паре берёт имя из адреса,
 * и ролик сохранялся как `rnd_9f31c0ab77de.mp4`. Значит имя обязана нести сама ссылка.
 */
export function downloadFileName(base: string, ext: string): string {
  const clean = base.replace(/["\\\/:*?<>|\u0000-\u001f]/g, ' ').replace(/\s+/g, ' ').trim()
  return `${clean || 'файл'}.${ext}`
}

/** Имя исходника без расширения: конвертер называет результат так же, но с новым форматом. */
export function withoutExt(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(0, dot) : name
}

export function statusText(status: string): string {
  return STATUS[status] ?? status
}

export function needsPolling(assets: { status: string }[]): boolean {
  return assets.some(a => !FINAL.has(a.status))
}

/** Годится ли запись в клип: длительность известна, обработка дошла хотя бы до звука и полоски. */
export function isReady(a: Asset): boolean {
  if (!READY.has(a.status)) return false
  // У картинки своей длительности нет и не будет: сколько она висит в кадре, решают при
  // добавлении. Требовать длительность от картинки значило бы не пускать её на шкалу никогда.
  if (a.kind === 'image') return true
  return a.duration !== null && a.duration > 0
}

export function listAssets(): Promise<{ assets: Asset[] }> {
  return api<{ assets: Asset[] }>('/api/v1/assets')
}

/** Что и какого размера принимает сервер. Держать копию списка в браузере нельзя: разойдясь
 * с сервером, подпись пообещала бы формат, который уже не принимают. */
export type Limits = {
  max_upload_bytes: number
  max_still_sec: number
  formats: Record<string, string[]>
}

export function loadLimits(): Promise<Limits> {
  return api<Limits>('/api/v1/limits')
}

/**
 * Записи, из которых собран и может собираться этот проект, — то есть записи его владельца.
 *
 * Редактор спрашивает их через проект, а не общим списком: документ обязан ссылаться на записи
 * владельца, и у админа, открывшего чужой проект, свой список совсем другой — каждый клип
 * выглядел бы необработанным, а сцена осталась бы пустой.
 */
export function listProjectAssets(projectId: string): Promise<{ assets: Asset[] }> {
  return api<{ assets: Asset[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/assets`)
}

export function deleteAsset(id: string): Promise<void> {
  return api<void>(`/api/v1/assets/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** Карточка одного ассета: по ней панель текста видит, не появился ли транскрипт. */
export function loadAsset(id: string): Promise<Asset> {
  return api<Asset>(`/api/v1/assets/${encodeURIComponent(id)}`)
}

/* ═══ Кадр из полоски ═══════════════════════════════════════════════════════ */

const FRAME_W = 96 // коробка под кадр — 16:9; кадр другой пропорции встаёт в неё целиком, с полями
const FRAME_H = 54
const FRAME_AT = 0.1 // кадр берём не с нуля: первый кадр записи слишком часто чёрный

// Коробка одна у всех записей — столбец имён ровный при любой пропорции кадра.
const SLOT_STYLE = [
  'display:flex',
  'align-items:center',
  'justify-content:center',
  'flex:0 0 auto',
  `width:${FRAME_W}px`,
  `height:${FRAME_H}px`,
  'overflow:hidden',
  'border-radius:var(--radius-xl)',
  'background-color:var(--line)',
].join(';')

const FRAME_STYLE = [`width:${FRAME_W}px`, `height:${FRAME_H}px`, 'flex:0 0 auto', 'background-repeat:no-repeat'].join(';')

/** Кадр в коробке целиком, без обрезки: упирается в её ширину или в высоту. */
export function fitFrame(width: number, height: number): { width: number; height: number } {
  const ratio = width > 0 && height > 0 ? width / height : FRAME_W / FRAME_H
  const w = Math.min(FRAME_W, FRAME_H * ratio)
  return { width: Math.round(w), height: Math.round(w / ratio) }
}

/**
 * Место под кадр из полоски. Сам кадр приезжает позже: раскладку спрайта надо сначала загрузить,
 * а список должен появиться сразу — поэтому здесь только пустая коробка и ссылки для `paintFrames`.
 */
export function frameHtml(a: Asset): string {
  if (!a.files.thumbs || !a.files.thumbs_meta) return `<span style="${SLOT_STYLE}"></span>`
  return `<span style="${SLOT_STYLE}"><span style="${FRAME_STYLE}" data-frame="${a.id}"
    data-sprite="${a.files.thumbs}" data-meta="${a.files.thumbs_meta}" data-at="${(a.duration ?? 0) * FRAME_AT}"></span></span>`
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
        const fit = fitFrame(thumbs.width, thumbs.height)
        box.style.width = `${fit.width}px`
        box.style.height = `${fit.height}px`
        box.style.backgroundImage = `url('${sprite}')`
        box.style.backgroundSize = `${thumbs.cols * 100}% ${thumbs.rows * 100}%`
        box.style.backgroundPosition = `${thumbs.cols > 1 ? (col / (thumbs.cols - 1)) * 100 : 0}%
          ${thumbs.rows > 1 ? (row / (thumbs.rows - 1)) * 100 : 0}%`
      })
      .catch(() => {}) // нет раскладки — карточка живёт с пустой рамкой, это не повод шуметь
  })
}
