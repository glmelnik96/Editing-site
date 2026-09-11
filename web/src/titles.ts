/** Заголовок вкладки браузера: экран или проект, потом сервис — чтобы соседние вкладки различались. */
export function pageTitle(screen?: string | null): string {
  return screen ? `${screen} — Editing site` : 'Editing site'
}
