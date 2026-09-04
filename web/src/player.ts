import { escapeHtml } from './html'

export type ThumbsMeta = { count: number; cols: number; rows: number; interval: number; width: number; height: number }
export type AssetFiles = { proxy: string | null }

/** Клетка спрайта для момента времени: индекс и смещение фона. */
export function thumbAt(meta: ThumbsMeta, seconds: number): { index: number; x: number; y: number } {
  const raw = Math.floor(Math.max(0, seconds) / meta.interval)
  const index = Math.min(meta.count - 1, Math.max(0, raw))
  const col = index % meta.cols
  const rowIdx = Math.floor(index / meta.cols)
  // без ветки col/rowIdx===0 unary minus даёт -0, а toEqual в vitest отличает его от 0
  return {
    index,
    x: col ? -col * meta.width : 0,
    y: rowIdx ? -rowIdx * meta.height : 0,
  }
}

/** Человеческое описание обработки: пока идёт анализ и прокси, пользователь видит, чего ждать. */
export function progressText(status: string, progress: number | null): string {
  const pct = progress === null ? null : Math.round(Math.min(1, Math.max(0, progress)) * 100)
  if (status === 'uploaded') return 'ждёт обработки'
  if (status === 'analyzing') return pct === null ? 'анализ' : `анализ, ${pct} %`
  if (status === 'ready') return pct === null ? 'готовим прокси' : `готовим прокси, ${pct} %`
  return ''
}

/** Плеер прокси. Пока прокси нет, ничего не рисуем: исходник наружу не отдаётся. */
export function playerMarkup(files: AssetFiles, kind: string): string {
  if (!files.proxy) return ''
  const src = escapeHtml(files.proxy)
  return kind === 'audio'
    ? `<audio class="player" controls preload="metadata" src="${src}"></audio>`
    : `<video class="player" controls preload="metadata" src="${src}"></video>`
}
