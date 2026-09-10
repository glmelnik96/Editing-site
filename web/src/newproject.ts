/**
 * Экран нового проекта: имя и вход в монтаж с пустой шкалой.
 *
 * Файл на шкалу кладут в исходниках редактора, здесь его не выбираем.
 */
import { ApiError } from './api'
import { createProject } from './project'

export function nameReady(value: string): boolean {
  return value.trim().length > 0
}

export function mountNewProject(el: HTMLElement) {
  el.innerHTML = `
    <div class="screen stack">
      <h1 class="display-l" style="margin:0">Новый проект</h1>
      <input id="np-name" class="field" maxlength="200" placeholder="Как назовём ролик"
        autofocus enterkeyhint="go" />
      <div class="row">
        <button id="np-go" class="btn btn-key" disabled>Создать</button>
      </div>
      <pre id="np-error" hidden></pre>
    </div>`

  const nameField = el.querySelector('#np-name') as HTMLInputElement
  const go = el.querySelector('#np-go') as HTMLButtonElement
  const errorBox = el.querySelector('#np-error') as HTMLPreElement

  const showError = (e: unknown) => {
    errorBox.hidden = false
    errorBox.textContent = e instanceof ApiError ? `Ошибка: ${e.message}` : String(e)
  }

  const sync = () => {
    go.disabled = !nameReady(nameField.value)
  }
  nameField.addEventListener('input', sync)
  // Одно поле и одна кнопка: Enter обязан отправлять. Без этого набранное имя просто висело,
  // и приходилось искать мышью кнопку в двух сантиметрах ниже.
  nameField.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || go.disabled) return
    event.preventDefault()
    go.click()
  })
  nameField.focus()

  go.addEventListener('click', async () => {
    const name = nameField.value.trim()
    if (!nameReady(name)) return
    go.disabled = true
    try {
      const project = await createProject(name)
      location.hash = `#/p/${project.id}`
    } catch (e) {
      go.disabled = false
      showError(e)
    }
  })

  return { stop(): void {} }
}
