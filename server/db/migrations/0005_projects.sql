-- doc: документ проекта целиком (JSON, раздел 4 спеки). Отдельных таблиц под клипы нет:
-- проект всегда сохраняется и читается целиком, а запросов «найди клип» не бывает.
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    doc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'finished')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX projects_user_idx ON projects(user_id, updated_at DESC);
