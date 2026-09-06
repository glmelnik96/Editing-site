/**
 * Разбор адреса страницы: `location.hash` → экран.
 *
 * Чистая часть маршрутизации, отдельно от DOM: правила «какой мусор считается главной»
 * проверяются тестами, а не глазами на стенде.
 */

export type Route =
  | { name: 'home' }
  | { name: 'files' }
  | { name: 'new' }
  | { name: 'projects' }
  | { name: 'settings' }
  | { name: 'admin' }
  | { name: 'editor'; projectId: string }

/* Идентификатор проекта — буквы, цифры, дефис и подчёркивание, и ничего больше.
 * Строку намеренно не раскодируем: `%2e%2e` и пробел так не пролезут в путь запроса,
 * а настоящие идентификаторы (`prj_` и шестнадцатеричные цифры) кодировать нечем. */
const EDITOR_PATH = /^\/p\/([A-Za-z0-9_-]+)$/

export function parseRoute(hash: string): Route {
  const path = hash.startsWith('#') ? hash.slice(1) : hash

  const editor = EDITOR_PATH.exec(path)
  if (editor) return { name: 'editor', projectId: editor[1] }

  switch (path) {
    case '/files':
      return { name: 'files' }
    case '/new':
      return { name: 'new' }
    case '/projects':
      return { name: 'projects' }
    case '/settings':
      return { name: 'settings' }
    case '/admin':
      return { name: 'admin' }
    default:
      // Неизвестный адрес — не ошибка, а главная: человек мог поправить строку браузера руками.
      return { name: 'home' }
  }
}
