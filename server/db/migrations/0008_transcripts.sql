-- Транскрипт ассета. Сам текст лежит файлом transcript.json рядом с исходником: он большой,
-- нужен целиком, и в базе только мешал бы каждому SELECT по ассетам.
CREATE TABLE transcripts (
    asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    language TEXT NOT NULL,
    duration REAL NOT NULL,
    segments INTEGER NOT NULL,
    stats TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX transcripts_user_idx ON transcripts(user_id, created_at DESC);
