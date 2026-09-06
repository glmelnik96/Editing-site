/**
 * Дверь — единственный экран гостя: одно предложение о том, что здесь делают, и одна кнопка.
 *
 * Ни списка возможностей, ни футера: человек, впервые открывший сайт, должен прочитать строку
 * и нажать кнопку, а не выбирать, что прочитать сначала.
 */
import { loginErrorText } from './errors'
import { escapeHtml } from './html'

const TITLE = 'Здесь из записей собирают ролики.'
const LEAD = 'Загрузите запись, вырежьте лишнее, добавьте музыку и субтитры — заберите готовый файл.'
const WORD_STEP_MS = 70

export function mountDoor(el: HTMLElement): void {
  // Слова проявляются по очереди: заголовок читается как фраза, а не выпрыгивает целиком.
  // При prefers-reduced-motion всё движение гасит style.css, отдельной ветки здесь не нужно.
  const words = TITLE.split(' ')
  const title = words
    .map((w, i) => `<span class="appear" style="display:inline-block;--delay:${i * WORD_STEP_MS}ms">${w}</span>`)
    .join(' ')
  const afterTitle = words.length * WORD_STEP_MS // очередь продолжается пояснением и кнопкой

  const code = new URLSearchParams(location.search).get('error')
  const error = code
    ? `<p class="error" style="margin:16px 0 0">${escapeHtml(loginErrorText(code))}</p>`
    : ''

  // Колонка стоит по центру и чуть выше середины: снизу воздуха больше, чем сверху,
  // иначе текст на широком экране кажется съехавшим вниз.
  el.innerHTML = `
    <section class="screen" style="min-height:calc(100vh - 58px);display:flex;flex-direction:column;
      align-items:center;justify-content:center;text-align:center;padding-bottom:8vh">
      <div style="max-width:820px">
        <h1 class="display-xl">${title}</h1>
        <p class="lead appear" style="margin:0 0 32px;--delay:${afterTitle}ms">${LEAD}</p>
        <a class="btn btn-key appear" style="--delay:${afterTitle + WORD_STEP_MS}ms" href="/api/v1/auth/login">Войти через Яндекс</a>
        ${error}
      </div>
    </section>`
}
