-- Точки сохранения проекта: снимок документа и имени на момент нажатия кнопки.
-- Автосохранение сюда не пишет: пул маленький, и полминуты правок вытеснили бы всё осмысленное.
CREATE TABLE project_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    doc TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX project_versions_project_idx ON project_versions(project_id, created_at DESC);
