export type EditorTab = 'source' | 'subtitles' | 'renders'

export function tabEnabled(tab: EditorTab, clipCount: number, hasReadyRender: boolean): boolean {
  if (tab === 'source') return true
  if (tab === 'subtitles') return clipCount > 0
  return clipCount > 0 || hasReadyRender
}

export function resolveTab(tab: EditorTab, clipCount: number, hasReadyRender: boolean): EditorTab {
  return tabEnabled(tab, clipCount, hasReadyRender) ? tab : 'source'
}
