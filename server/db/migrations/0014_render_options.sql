-- Настройки сборки: формат, разрешение и битрейт. CHECK качества в SQLite не расширить —
-- таблицу пересоздаём. На renders никто не ссылается, поэтому внешние ключи выключать не нужно:
-- DROP TABLE дочерней таблицы ничего каскадом не уносит.
--
-- Прежние draft и final остаются: их по-прежнему присылает агент через API, и старые ролики
-- должны читаться под своими именами. Новые качества выбирает человек в панели сборки.
-- width и height у старых строк неизвестны (NULL): разрешение тогда бралось из настроек.
CREATE TABLE renders_new (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL,
    quality TEXT NOT NULL CHECK (quality IN ('draft', 'final', 'preview', 'medium', 'high', 'target')),
    format TEXT NOT NULL DEFAULT 'mp4' CHECK (format IN ('mp4', 'webm', 'm4a')),
    width INTEGER,
    height INTEGER,
    video_bitrate INTEGER,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    duration REAL NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
INSERT INTO renders_new (
    id, project_id, user_id, job_id, quality, format, path, size, duration, created_at, expires_at
)
SELECT
    id, project_id, user_id, job_id, quality, 'mp4', path, size, duration, created_at, expires_at
FROM renders;
DROP TABLE renders;
ALTER TABLE renders_new RENAME TO renders;
CREATE INDEX renders_project_idx ON renders(project_id, created_at DESC);
CREATE INDEX renders_expires_idx ON renders(expires_at);
