/**
 * Сборка приложения: оболочка, маршрут, экран.
 *
 * Здесь только это. Каждый экран — свой модуль с `mount…(el)`; предыдущий останавливается
 * своим `stop()` перед тем, как контейнер перепишут, иначе его опросы и плеер продолжат жить
 * в разобранной разметке.
 */
import './style.css'
import { api, ApiError } from './api'
import { mountAdmin } from './admin'
import { mountDoor } from './door'
import { mountEditor } from './editor'
import { mountFiles } from './files'
import { escapeHtml } from './html'
import { mountHome } from './home'
import { mountNewProject } from './newproject'
import { mountProjects } from './projects'
import { mountSettings } from './settings'
import { parseRoute, type Route } from './router'
import { mountShell, type Me } from './shell'

const root = document.getElementById('app') as HTMLElement
const shell = mountShell(root)

let current: { stop?: () => void } | null = null
// Номер перехода: пока летит запрос /me, человек мог уйти на другой адрес — ответ старого
// перехода не должен рисовать свой экран поверх нового.
let pass = 0

function screenText(title: string, lead: string): void {
  shell.screen.innerHTML = `
    <div class="screen stack">
      <h1 class="display-l" style="margin:0">${escapeHtml(title)}</h1>
      <p class="lead" style="margin:0">${escapeHtml(lead)}</p>
    </div>`
}

function show(route: Route, me: Me): void {
  // Место в шапке меняется от загрузки и удаления записей — экран записей сообщает об этом сюда.
  const refreshQuota = () => {
    void api<Me>('/api/v1/me')
      .then(fresh => shell.setUser(fresh))
      .catch(() => {}) // не обновилась цифра в шапке — не повод шуметь на весь экран
  }
  switch (route.name) {
    case 'home':
      current = mountHome(shell.screen, me)
      return
    case 'files':
      current = mountFiles(shell.screen, refreshQuota)
      return
    case 'new':
      current = mountNewProject(shell.screen)
      return
    case 'projects':
      current = mountProjects(shell.screen)
      return
    case 'settings':
      current = mountSettings(shell.screen)
      return
    case 'admin':
      // Ссылки на этот экран у обычного пользователя нет, а сервер ответит ему отказом.
      current = mountAdmin(shell.screen)
      return
    case 'editor':
      current = mountEditor(shell.screen, route.projectId)
  }
}

async function route(): Promise<void> {
  const mine = ++pass
  current?.stop?.()
  current = null
  const wanted = parseRoute(location.hash)

  let me: Me
  try {
    me = await api<Me>('/api/v1/me')
  } catch (e) {
    if (mine !== pass) return
    if (e instanceof ApiError && e.status === 401) {
      shell.clearUser()
      mountDoor(shell.screen)
    } else {
      screenText('Не открылось', e instanceof Error ? e.message : String(e))
    }
    return
  }
  if (mine !== pass) return

  shell.setUser(me)
  show(wanted, me)
}

window.addEventListener('hashchange', () => void route())
void route()
