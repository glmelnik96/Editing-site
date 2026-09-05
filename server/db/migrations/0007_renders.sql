-- Готовые ролики. Статуса тут нет намеренно: строка существует — значит файл собран и лежит на диске.
-- Ход работы и ошибки видны в задании (таблица jobs), а не здесь.
CREATE TABLE renders (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL,
    quality TEXT NOT NULL CHECK (quality IN ('draft', 'final')),
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    duration REAL NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX renders_project_idx ON renders(project_id, created_at DESC);
CREATE INDEX renders_expires_idx ON renders(expires_at);
