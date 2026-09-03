# Editing site

Онлайн-редактор видео на VM с API для внешнего агента. Дизайн: `docs/superpowers/specs/2026-09-03-video-editor-design.md`.

## Локальный запуск

```bash
uv sync
cp .env.example .env            # заполнить VIDEO_ADMIN_EMAIL и ключи Yandex
uv run uvicorn server.app.main:app --reload --port 8010
cd web && npm install && npm run dev   # интерфейс на http://localhost:5173, /api проксируется на 8010
```

## Тесты

```bash
uv run pytest
cd web && npm test
```

## Разработка интерфейса

Два способа увидеть интерфейс локально:

- **Сборка + один сервер.** `cd web && npm run build`, затем `uv run uvicorn server.app.main:app --port 8010`: приложение раздаёт `web/dist` на `/`, cookie и проверка Origin работают как на VM.
- **Vite dev-сервер** (`cd web && npm run dev`, страница на http://localhost:5173, `/api` проксируется на 8010). Проверка cross-site сравнивает `Origin` с `VIDEO_PUBLIC_BASE_URL`, поэтому на время разработки в `.env` ставь `VIDEO_PUBLIC_BASE_URL=http://localhost:5173` (redirect URI приложения Яндекса должен совпадать).

## Деплой

`deploy/bootstrap.sh` один раз на чистой Ubuntu 24.04, затем `deploy/deploy.sh` на каждый релиз. Подробности в спеке, раздел 12.
