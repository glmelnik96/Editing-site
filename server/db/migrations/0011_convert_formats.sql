-- SQLite не умеет расширить CHECK: таблицу conversions пересоздаём, старые строки копируются.
-- Новые форматы совпадают с FORMATS в server/media/convert.py.
CREATE TABLE conversions_new (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL,
    format TEXT NOT NULL CHECK (format IN ('mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg', 'mp4', 'webm')),
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    duration REAL NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
INSERT INTO conversions_new (
    id, user_id, asset_id, job_id, format, path, size, duration, created_at, expires_at
)
SELECT
    id, user_id, asset_id, job_id, format, path, size, duration, created_at, expires_at
FROM conversions;
DROP TABLE conversions;
ALTER TABLE conversions_new RENAME TO conversions;
CREATE INDEX conversions_asset_idx ON conversions(asset_id, created_at DESC);
CREATE INDEX conversions_expires_idx ON conversions(expires_at);
