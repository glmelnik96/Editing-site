import { describe, expect, it } from 'vitest'
import { EMPTY_TIMELINE, editorBlocks, sourceBlocks, tabBlock } from './blocked'

const base = { hasClips: true, picked: 'clip' as const, canUndo: true, canRedo: true, cuesReady: true }

describe('почему кнопка серая', () => {
  it('всё можно — причин нет', () => {
    expect(Object.values(editorBlocks(base)).every(reason => reason === null)).toBe(true)
  })

  it('пустая шкала гасит просмотр, разрез и масштаб одной причиной', () => {
    const b = editorBlocks({ ...base, hasClips: false })
    expect([b.play, b.split, b.zoom]).toEqual([EMPTY_TIMELINE, EMPTY_TIMELINE, EMPTY_TIMELINE])
  })

  it('дублировать можно только клип', () => {
    expect(editorBlocks({ ...base, picked: 'none' }).duplicate).toBe('Выберите клип на шкале')
    expect(editorBlocks({ ...base, picked: 'sound' }).duplicate).toBe('Звук и наложение пока не дублируются')
    expect(editorBlocks({ ...base, picked: 'overlay' }).duplicate).toBe('Звук и наложение пока не дублируются')
  })

  it('удалить можно любой выбранный кусок', () => {
    expect(editorBlocks({ ...base, picked: 'none' }).remove).toBe('Выберите клип, звук или наложение на шкале')
    expect(editorBlocks({ ...base, picked: 'sound' }).remove).toBeNull()
  })

  it('история и субтитры говорят своё', () => {
    const b = editorBlocks({ ...base, canUndo: false, canRedo: false, cuesReady: false })
    expect(b.undo).toBe('Отменять пока нечего')
    expect(b.redo).toBe('Возвращать нечего')
    expect(b.burn).toBe('Сначала сделайте субтитры во вкладке «Субтитры»')
  })
})

describe('серые кнопки исходника и вкладки', () => {
  it('без файла и с коротким куском', () => {
    expect(sourceBlocks({ hasFile: false, longEnough: false, overlayable: false }).add).toBe('Сначала выберите файл выше')
    expect(sourceBlocks({ hasFile: true, longEnough: false, overlayable: true }).add).toBe(
      'Кусок короче 0.1 с — раздвиньте границы',
    )
  })

  it('поверх — только картинки и видео, и сперва причина «добавить»', () => {
    expect(sourceBlocks({ hasFile: true, longEnough: true, overlayable: false }).over).toBe(
      'Поверх кладутся только картинки и видео',
    )
    expect(sourceBlocks({ hasFile: false, longEnough: false, overlayable: true }).over).toBe('Сначала выберите файл выше')
    expect(sourceBlocks({ hasFile: true, longEnough: true, overlayable: true })).toEqual({ add: null, over: null })
  })

  it('вкладка без клипов', () => {
    expect(tabBlock(false)).toBe('Появится, когда на шкале будет клип')
    expect(tabBlock(true)).toBeNull()
  })
})
