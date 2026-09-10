/**
 * Нарезка блока шкалы на плитки и выбор тех, что видны.
 *
 * Волна и кадры клипа рисуются не одним куском во всю ширину блока, а плитками. Холст шире
 * 65 535 пикселей Chrome молча не рисует — замерено: контекст создаётся, ширина та, а на холст не
 * ложится ни пикселя. Запись на полтора часа при обычных 40 px/с — это 198 тысяч пикселей, и волна
 * исчезала целиком, стоило укрупнить масштаб. Сжимать холст тоже нельзя: волну читают, чтобы
 * попасть резом в паузу, а при сжатии в десятки раз столбик шире самой паузы.
 *
 * Рисуем только плитки, которые видны, с запасом на экран в каждую сторону. Иначе на крупном
 * масштабе в памяти висели бы сотни мегабайт холстов и десятки тысяч кадров, которых никто не
 * видит, — и всё это пересобиралось бы при каждой перерисовке шкалы.
 */

/** Желаемая ширина плитки. Далеко от предела холста и соразмерна экрану. */
export const TILE_PX = 2048

/**
 * Ширина плитки, кратная ширине кадра: кадр не должен резаться границей плитки, иначе на стыке
 * двух плиток он показывался бы половинками разных моментов.
 */
export function tileWidth(frameWidth: number | null, target = TILE_PX): number {
  if (!frameWidth || frameWidth <= 0) return target
  return Math.max(frameWidth, Math.floor(target / frameWidth) * frameWidth)
}

export type Tile = { index: number; x0: number; x1: number }

/**
 * Плитки блока, которые пересекают полосу [viewFrom, viewTo) шкалы.
 *
 * Координаты — пиксели шкалы: блок начинается с blockLeft и тянется на blockWidth. Последняя
 * плитка короче остальных, если на неё не хватило ширины блока.
 */
export function visibleTiles(
  blockLeft: number,
  blockWidth: number,
  viewFrom: number,
  viewTo: number,
  tile: number,
): Tile[] {
  const from = Math.max(0, viewFrom - blockLeft)
  const to = Math.min(blockWidth, viewTo - blockLeft)
  if (blockWidth <= 0 || to <= from) return []
  const first = Math.floor(from / tile)
  const last = Math.ceil(to / tile) - 1
  const tiles: Tile[] = []
  for (let index = first; index <= last; index++) {
    tiles.push({ index, x0: index * tile, x1: Math.min(blockWidth, (index + 1) * tile) })
  }
  return tiles
}

/**
 * Отрезок исходника под плиткой: доля ширины блока — та же доля клипа.
 *
 * Считаем долями, а не через пиксели в секунду: у очень короткого клипа блок растянут до
 * минимальной ширины, и пиксели в секунду у него свои.
 */
export function tileRange(tile: Tile, blockWidth: number, from: number, to: number): { from: number; to: number } {
  const span = to - from
  return { from: from + (span * tile.x0) / blockWidth, to: from + (span * tile.x1) / blockWidth }
}
