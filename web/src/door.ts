/**
 * Дверь — единственный экран гостя: что здесь делают и одна кнопка.
 *
 * Вёрстка и хронометраж взяты у Presentation Remote: колонка 880 прижата к верху (не по центру),
 * слова заголовка выходят из размытия по очереди, следом пояснение, следом кнопка. Так у трёх
 * сервисов ВМ одинаковое первое впечатление, и человек узнаёт знакомое место.
 */
import { loginErrorText } from './errors'
import { escapeHtml } from './html'

const TITLE = 'Привет! Это онлайн-редактор видеороликов.'
const LEAD = 'Загрузите запись, вырежьте лишнее, добавьте музыку и субтитры — заберите готовый файл.'

// Хронометраж первого экрана, мс от загрузки — тот же, что у соседа.
const WORDS_FROM = 150
const WORDS_STEP = 70
const NOTE_AT = 900
const BUTTON_AT = 1350

export function mountDoor(el: HTMLElement): void {
  // При prefers-reduced-motion всё движение гасит style.css, отдельной ветки здесь не нужно.
  const title = TITLE.split(' ')
    .map((w, i) => `<span class="word-in" style="--delay:${WORDS_FROM + i * WORDS_STEP}ms">${w}</span>`)
    .join(' ')

  const code = new URLSearchParams(location.search).get('error')
  const error = code
    ? `<p class="error msg-in" style="margin:0;--delay:${NOTE_AT + 300}ms">${escapeHtml(loginErrorText(code))}</p>`
    : ''

  el.innerHTML = `
    <main class="door">
      <h1 class="display-xl" style="margin:0">${title}</h1>
      <p class="lead msg-in door-note" style="margin:0;--delay:${NOTE_AT}ms">${LEAD}</p>
      ${error}
      <div class="row" style="margin:0">
        <a class="btn btn-key chip-in" style="--delay:${BUTTON_AT}ms"
          href="/api/v1/auth/login">Войти через Яндекс</a>
      </div>
    </main>`
}
