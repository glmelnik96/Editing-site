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

## Деплой

`deploy/bootstrap.sh` один раз на чистой Ubuntu 24.04, затем `deploy/deploy.sh` на каждый релиз. Подробности в спеке, раздел 12.
