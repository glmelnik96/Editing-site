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

## Загрузка и файлы (M1a)

- `POST /api/v1/uploads` → `PUT /api/v1/uploads/{id}/chunks/{n}` (сырые байты, 32 МиБ) → `GET /api/v1/uploads/{id}` (докачка) → `POST /api/v1/uploads/{id}/complete` → `asset_id`. Справочный клиент: `python tools/upload_file.py https://video.cloudrudesign.ru $TOKEN clip.mp4`.
- Мелкие файлы (SRT, музыка до 64 МиБ): `POST /api/v1/assets/upload` (multipart `file`, необязательно `kind`).
- `GET /api/v1/assets`, `GET /api/v1/assets/{id}` (ссылки на `proxy`, `thumbs`, `peaks`, `analysis` появляются по статусу), `DELETE /api/v1/assets/{id}`. Квота и использование в `GET /api/v1/me`.
- Файлы: `/files/{user_id}/assets/{asset_id}/<имя>`; на VM отдаёт Caddy после `forward_auth` в `/internal/authz`, локально само приложение. `source.*` наружу не отдаётся.
- Пределы: 5 ГБ на файл, 20 ГБ на человека, 20 новых загрузок в час, отказ при свободном диске меньше 10 %.
- Janitor (`python -m server.janitor`, таймер раз в час): загрузки старше 24 ч, ассеты без обращений старше 24 ч, сироты на диске старше часа, зависшие задания, просроченные сессии, суточный бэкап базы в `data/backups/` (7 копий).
- Локальный запуск: `VIDEO_TMP_DIR` по умолчанию `data/tmp`; на VM `/srv/video/tmp` (тот же раздел, что `/srv/video/data`).
- Известное ограничение: завершение загрузки не идемпотентно. Если ответ на `complete` потерялся в сети уже после успеха на сервере, запись о загрузке удалена, и следующая попытка создаст вторую загрузку того же файла, то есть дубль ассета и лишний расход квоты. Дубль виден в списке и удаляется вручную.

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

## Соседи на той же VM

VM общая с сервисом VideoBoard (`/opt/videoboard`, пользователь `board`, порт 8020). Договорённости:

- `/etc/caddy/Caddyfile` наш и переписывается `deploy.sh` целиком; чужие site-блоки живут в `/etc/caddy/conf.d/*.caddy` и подключаются строкой `import` в начале нашего Caddyfile. Сосед валидирует общий конфиг перед каждым `systemctl reload caddy`.
- Yandex OAuth-приложение общее: у него два redirect URI, а `client_id`/`client_secret` лежат в обоих `.env`. Ротация секрета кладёт оба сервиса, перед сменой предупредить чат VideoBoard.
- Диск общий: у нас файлы живут сутки и квота 20 ГБ на человека, у соседа потолок 30 ГБ и TTL 30 дней; порог отказа в загрузке у обоих 10 % свободного места.
- Модули `server/app/auth/*`, `server/db/*`, `config`, `errors`, `ratelimit`, `security` скопированы к соседу как основа; найденные в них баги сообщать в чат VideoBoard.
