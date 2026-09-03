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
uv run python -m pytest
uv run ruff check .
cd web && npm test
```

## Разработка интерфейса

Два способа увидеть интерфейс локально:

- **Сборка + один сервер.** `cd web && npm run build`, затем `uv run uvicorn server.app.main:app --port 8010`: приложение раздаёт `web/dist` на `/`, cookie и проверка Origin работают как на VM.
- **Vite dev-сервер** (`cd web && npm run dev`, страница на http://localhost:5173, `/api` проксируется на 8010). Проверка cross-site сравнивает `Origin` с `VIDEO_PUBLIC_BASE_URL`, поэтому на время разработки в `.env` ставь `VIDEO_PUBLIC_BASE_URL=http://localhost:5173` (redirect URI приложения Яндекса должен совпадать).

## Деплой

1. DNS: A-запись поддомена на VM. Приложение Yandex OAuth с redirect URI `https://<домен>/api/v1/auth/callback` и правами `login:email login:info`.
2. Приватный репозиторий: положить deploy key в `/etc/editing-site/deploy_key` (bootstrap выставит права), публичный HTTPS-URL ключа не требует.
3. Один раз на чистой Ubuntu 24.04: `sudo bash bootstrap.sh <домен> <git-url>`, затем заполнить `/opt/editing-site/.env` (`VIDEO_DATA_DIR=/srv/video/data`, `VIDEO_PUBLIC_BASE_URL=https://<домен>`, `VIDEO_COOKIE_SECURE=true`, ключи Yandex, `VIDEO_ADMIN_EMAIL`).
4. Каждый релиз: `sudo bash /opt/editing-site/deploy/deploy.sh` (fast-forward `main`, зависимости, сборка интерфейса, миграции, переустановка Caddyfile и юнита, рестарт, ожидание `status=ok` в `/healthz`).
5. Замер скорости ffmpeg на машине: `sudo -u video .venv/bin/python tools/bench_ffmpeg.py --out /tmp/bench-report --work /srv/video/tmp/bench` из `/opt/editing-site`, отчёт скопировать в `docs/benchmarks/` локально.

`/healthz` отвечает 503 при статусе degraded (диск меньше 10 %, база недоступна, пульс воркера старше 120 с), так что внешний пинг по коду ответа достаточен.
