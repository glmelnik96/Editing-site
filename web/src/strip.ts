/**
 * Данные для отрисовки блока клипа: звуковая волна из пиков и кадры из спрайта.
 *
 * Оба файла посчитал воркер: `peaks.json` (50 значений в секунду, 0..255) и `thumbs.json`
 * (раскладка спрайта `thumbs.jpg`). Здесь только арифметика: что показать на отрезке исходника.
 */

export type Peaks = { rate: number; peaks: number[] }
export type ThumbsMeta = { count: number; cols: number; rows: number; interval: number; width: number; height: number }
export type Range = { from: number; to: number }
export type AssetData = { peaks: Peaks | null; thumbs: ThumbsMeta | null }

/** Сжать ряд пиков до нужного числа столбиков: в каждом столбике максимум своего окна. */
export function waveBars(peaks: number[], count: number): number[] {
  if (count <= 0) return []
  if (peaks.length === 0) return new Array(count).fill(0)
  const out: number[] = []
  for (let i = 0; i < count; i++) {
    const from = Math.floor((i * peaks.length) / count)
    const to = Math.max(from + 1, Math.floor(((i + 1) * peaks.length) / count))
    let max = 0
    for (let j = from; j < to && j < peaks.length; j++) max = Math.max(max, peaks[j])
    out.push(max)
  }
  return out
}

/** Столбики волны для отрезка исходника. Нет пиков — ровная линия, а не ошибка. */
export function barsFor(data: Peaks | null, range: Range, count: number): number[] {
  if (count <= 0) return []
  if (!data || !data.peaks.length) return new Array(count).fill(0)
  const from = Math.max(0, Math.round(range.from * data.rate))
  const to = Math.min(data.peaks.length, Math.round(range.to * data.rate))
  if (to <= from) return new Array(count).fill(0)
  return waveBars(data.peaks.slice(from, to), count)
}

/** Смещение фона спрайта для кадра, ближайшего к моменту времени. */
export function thumbBackground(
  meta: ThumbsMeta,
  seconds: number,
): { x: number; y: number; width: number; height: number } {
  const raw = Math.floor(Math.max(0, seconds) / meta.interval)
  const index = Math.min(meta.count - 1, Math.max(0, raw))
  const col = index % meta.cols
  const row = Math.floor(index / meta.cols)
  return {
    x: col ? -col * meta.width : 0,
    y: row ? -row * meta.height : 0,
    width: meta.cols * meta.width,
    height: meta.rows * meta.height,
  }
}

export type Frame = { left: number; background: ReturnType<typeof thumbBackground> }

/** Кадры, которые влезают в блок шириной width: по одному на каждые meta.width пикселей. */
export function sliceThumbs(meta: ThumbsMeta | null, range: Range, width: number): Frame[] {
  if (!meta || width <= 0) return []
  const count = Math.max(1, Math.floor(width / meta.width))
  const span = Math.max(0, range.to - range.from)
  const frames: Frame[] = []
  for (let i = 0; i < count; i++) {
    const at = range.from + (span * i) / count
    frames.push({ left: i * meta.width, background: thumbBackground(meta, at) })
  }
  return frames
}

type Fetcher = (url: string) => Promise<unknown>

const defaultFetcher: Fetcher = async (url: string) => {
  const response = await fetch(url, { credentials: 'same-origin' })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json()
}

/**
 * Пики и раскладка кадров ассета, по одному запросу на файл за всё время жизни страницы.
 * Недоступный файл (ещё не посчитан, истёк по сроку) даёт null: блок нарисуется без волны или кадров.
 */
export async function assetData(
  assetId: string,
  links: { peaks: string | null; thumbs_meta: string | null },
  cache: Map<string, Promise<AssetData>>,
  fetcher: Fetcher = defaultFetcher,
): Promise<AssetData> {
  const existing = cache.get(assetId)
  if (existing) return existing
  const loading = (async (): Promise<AssetData> => {
    const load = async <T>(url: string | null): Promise<T | null> => {
      if (!url) return null
      try {
        return (await fetcher(url)) as T
      } catch {
        return null
      }
    }
    const [peaks, thumbs] = await Promise.all([
      load<Peaks>(links.peaks),
      load<ThumbsMeta>(links.thumbs_meta),
    ])
    return { peaks, thumbs }
  })()
  cache.set(assetId, loading)
  return loading
}
