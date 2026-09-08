-- jobs.type: SQLite не умеет расширить CHECK, таблицу пересоздаём. Старые строки копируются как есть.
-- convert конкурирует за полосу cpu с render (спека пакета C).
CREATE TABLE jobs_new (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('analyze', 'proxy', 'render', 'transcribe', 'convert')),
    lane TEXT NOT NULL CHECK (lane IN ('cpu', 'net')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'canceled')),
    priority INTEGER NOT NULL DEFAULT 0,
    target_id TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    progress REAL NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 1),
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    heartbeat_at TEXT,
    worker_pid INTEGER
);
INSERT INTO jobs_new (
    id, user_id, type, lane, status, priority, target_id, params, progress, error,
    attempts, created_at, started_at, finished_at, heartbeat_at, worker_pid
)
SELECT
    id, user_id, type, lane, status, priority, target_id, params, progress, error,
    attempts, created_at, started_at, finished_at, heartbeat_at, worker_pid
FROM jobs;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;
CREATE INDEX jobs_queue_idx ON jobs(status, lane, priority DESC, created_at);
CREATE INDEX jobs_target_idx ON jobs(target_id);

-- Готовый файл конвертера. Строка появляется только после успеха, как renders.
CREATE TABLE conversions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL,
    format TEXT NOT NULL CHECK (format IN ('mp3', 'm4a', 'wav', 'mp4')),
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    duration REAL NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX conversions_asset_idx ON conversions(asset_id, created_at DESC);
CREATE INDEX conversions_expires_idx ON conversions(expires_at);
