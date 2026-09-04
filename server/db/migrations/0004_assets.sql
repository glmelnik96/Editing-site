-- Загрузки по частям: файл живёт в tmp/uploads/{id}, path хранится для janitor.
CREATE TABLE uploads (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('video', 'audio', 'subtitle')),
    chunk_size INTEGER NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX uploads_user_idx ON uploads(user_id);
CREATE INDEX uploads_expires_idx ON uploads(expires_at);

CREATE TABLE upload_chunks (
    upload_id TEXT NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    PRIMARY KEY (upload_id, idx)
);

-- ext: расширение исходника (source.<ext>), чтобы находить файл без обращения к диску.
CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('video', 'audio', 'subtitle')),
    original_name TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('uploaded', 'analyzing', 'ready', 'proxy_ready', 'failed')),
    duration REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    has_audio INTEGER CHECK (has_audio IN (0, 1)),
    video_codec TEXT,
    audio_codec TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    last_access_at TEXT NOT NULL
);
CREATE INDEX assets_user_idx ON assets(user_id, created_at);
CREATE INDEX assets_last_access_idx ON assets(last_access_at);

-- attempts растёт при каждом взятии задания воркером; heartbeat_at обновляет воркер раз в 10 с.
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('analyze', 'proxy', 'render', 'transcribe')),
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
CREATE INDEX jobs_queue_idx ON jobs(status, lane, priority DESC, created_at);
CREATE INDEX jobs_target_idx ON jobs(target_id);
